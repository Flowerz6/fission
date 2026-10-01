"""Self-heal drifted ui_mode side-panel selectors.

Finds which side-panel selectors no longer match the live DOM, asks the LLM to
propose replacements, VALIDATES each proposal against the real page (unique
match + right shape), and writes the accepted fixes to the selector overlay JSON.
Runs (and the invoker) then pick the overlay up automatically — no code edit.

The LLM proposes; the live page decides. A proposal that doesn't resolve on the
page (or over-matches a singleton) is rejected and printed for you to inspect.

Two modes:

* ``--auto`` (default, google_docs) — no manual steps: browser-mints a blank
  host doc, opens the panel itself, and drives one benign probe turn so a reply
  exists. That's what lets the state-dependent selectors (the reply bubble, the
  action bar, …) actually be validated — an idle panel can't verify them.
* ``--manual --url <doc/sheet>`` — you open the page and the panel yourself and
  press Enter. Use this for Sheets (no auto path yet) or to heal a specific page.

Usage:
  # One command, no manual steps (Docs), dry run then apply:
  poetry run python scripts/heal_selectors.py
  poetry run python scripts/heal_selectors.py --apply

  # Manual (e.g. a Sheet), you open the panel:
  poetry run python scripts/heal_selectors.py --manual --url https://docs.google.com/spreadsheets/d/<id>/edit --apply

Then point runs at the overlay:
  export ADVTEST_SELECTOR_OVERLAY=$PWD/out/selector_overlay.json

Prereq: --extras ui_mode installed, a logged-in Playwright profile, and
ANTHROPIC_API_KEY (the CLAUDE_ATTACKER role) for the proposal step.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from advtest_ext.adapters._ui_mode_common.selector_heal import heal
from advtest_ext.adapters._ui_mode_common.selector_overlay import write_overlay

_REPO_ROOT = Path(__file__).resolve().parents[1]

# One intent line per selector key (shared across apps — the keys are common).
_INTENTS: dict[str, str] = {
    "side_panel_button": "the toolbar/rail button that OPENS the Gemini side panel",
    "side_panel_container": "the side-panel region (role=complementary, aria-label Gemini) present whenever the panel is open",
    "side_panel_input": "the contenteditable prompt field inside the open panel",
    "side_panel_response": "the latest agent reply text bubble (many per chat; the invoker takes .last)",
    "side_panel_loading_indicator": "the thinking/generating indicator shown while Gemini responds",
    "side_panel_response_complete": "the action bar at the bottom of the latest reply once generation finishes",
    "side_panel_new_conversation": "the 'New conversation' button to start a fresh chat",
    "side_panel_close": "the close (X) button for the side panel",
    "side_panel_send": "the send/submit button on the composer",
}


def _selectors_for(app: str) -> dict[str, str]:
    """The app's current (base + overlay) selector registry."""
    if app == "google_docs":
        from advtest_ext.adapters.google_docs.ui_mode.selectors import SELECTORS

        return {k: str(v) for k, v in SELECTORS.items()}
    if app == "google_sheets":
        from advtest_ext.adapters.sheets.ui_mode.selectors import SELECTORS

        return {k: str(v) for k, v in SELECTORS.items()}
    if app == "google_calendar":
        from advtest_ext.adapters.calendar.ui_mode.selectors import SELECTORS

        return {k: str(v) for k, v in SELECTORS.items()}
    raise SystemExit(
        f"no selector registry wired for app {app!r} yet "
        "(supported: google_docs, google_sheets, google_calendar)"
    )


def _infer_app(url: str) -> str:
    if "/spreadsheets/" in url:
        return "google_sheets"
    if "/document/" in url:
        return "google_docs"
    if "calendar.google.com" in url:
        return "google_calendar"
    raise SystemExit("could not infer --app from --url; pass --app explicitly")


async def _acquire_page_auto(session: Any, out_dir: Path, probe: str) -> Any:
    """No manual steps: browser-mint a host doc, open the panel, and drive one
    benign probe turn so a reply is present — then return the live page. Reuses
    the real multi-turn target/invoker, so it opens + sends exactly like a run."""
    from advtest_ext.adapters.google_docs.ui_mode.multi_turn_target import (
        GeminiDocsMultiTurnTarget,
    )
    from advtest_ext.document.document import AbstractDocument

    target = GeminiDocsMultiTurnTarget(output_dir=out_dir, browser_mint=True, session=session)
    await target.setup()
    ctx = await target.render_document(AbstractDocument(id="heal-probe", title="", body_blocks=[]))
    print(f"driving a probe turn ({probe!r}) to open the panel and surface a reply …")
    try:
        await target.invoke(ctx, probe)
    except Exception as exc:  # a drifted read selector may fail; heal the DOM anyway
        print(f"(probe turn raised {type(exc).__name__}: {exc}; healing whatever opened)")
    page = await session.ensure_page()
    # Leave UNSENT draft text in the composer so the send button is present to
    # validate — it only renders when the composer is non-empty. Real keystrokes
    # (type, not fill) so Google's JS fires the events that reveal the button.
    # No manual typing needed; the run captures reply + composer-with-text at once.
    try:
        from advtest_ext.adapters.google_docs.ui_mode.selectors import SELECTORS

        composer = page.locator(SELECTORS["side_panel_input"]).first
        await composer.click()
        await composer.type("draft — heal probe, do not send")
    except Exception as exc:
        print(f"(couldn't seed composer draft for the send selector: {exc})")
    return page


async def _acquire_page_manual(session: Any, url: str) -> Any:
    page = await session.ensure_page()
    print(f"navigating to {url} …")
    await page.goto(url)
    print(
        "\nIn the browser window: log into the right account and OPEN the Gemini\n"
        "side panel, then press Enter here to check + heal selectors."
    )
    input("  … press Enter once the panel is visible: ")
    return page


async def _run(
    *,
    app: str,
    provider_config: Path,
    overlay: Path,
    apply: bool,
    manual: bool,
    url: str | None,
    probe: str,
) -> None:
    from advtest_ext._cli_providers import _load_provider_role
    from advtest_ext.adapters._ui_mode_common import UiModeSession
    from advtest_ext.adapters._ui_mode_common.config import resolve_profile_dir

    selectors = _selectors_for(app)
    provider = _load_provider_role(
        provider_config, "CLAUDE_ATTACKER", missing_role_hint="add a CLAUDE_ATTACKER role"
    )

    session = UiModeSession(profile_dir=resolve_profile_dir())
    try:
        if manual:
            assert url is not None
            page = await _acquire_page_manual(session, url)
        else:
            page = await _acquire_page_auto(session, overlay.parent, probe)
        report = await heal(page, provider, selectors=selectors, intents=_INTENTS)
    finally:
        await session.close()

    if not report.drifted:
        print("\n✓ no drift — every selector still matches the live DOM.")
        return

    print(f"\ndrifted selectors ({len(report.drifted)}): {', '.join(report.drifted)}")
    if report.fixes:
        print("\naccepted fixes (validated on the live page):")
        for key, sel in report.fixes.items():
            print(f"  {key}\n    {selectors[key]}\n  → {sel}")
    if report.rejected:
        print("\nrejected (proposal failed live validation — inspect manually):")
        for key, sel in report.rejected.items():
            print(f"  {key}: {sel}")

    if not report.fixes:
        print("\nnothing to write.")
        return
    if apply:
        write_overlay(app, report.fixes, path=overlay)
        print(f"\nwrote {len(report.fixes)} fix(es) to {overlay}")
        print(f"export ADVTEST_SELECTOR_OVERLAY={overlay} for your runs.")
    else:
        print(f"\ndry run — re-run with --apply to write these to {overlay}")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--manual",
        action="store_true",
        help="open --url + the panel yourself (else auto: browser-mint + probe, Docs only)",
    )
    p.add_argument("--url", help="(manual mode) a Doc/Sheet URL you can open")
    p.add_argument("--app", help="google_docs | google_sheets (default google_docs)")
    p.add_argument(
        "--probe",
        default="Reply with exactly one word: pong.",
        help="(auto mode) the benign probe prompt used to surface a reply",
    )
    p.add_argument(
        "--provider-config",
        type=Path,
        default=_REPO_ROOT / "configs" / "dev.yaml",
        help="providers YAML with a CLAUDE_ATTACKER role (default: configs/dev.yaml)",
    )
    p.add_argument(
        "--overlay",
        type=Path,
        default=_REPO_ROOT / "out" / "selector_overlay.json",
        help="overlay JSON to write (default: ./out/selector_overlay.json)",
    )
    p.add_argument("--apply", action="store_true", help="write the validated fixes (else dry run)")
    args = p.parse_args()

    if args.manual and not args.url:
        raise SystemExit("--manual requires --url")
    if not args.manual and args.app in {"google_sheets"}:
        raise SystemExit("auto mode is google_docs-only; use --manual --url for Sheets")
    app = args.app or (_infer_app(args.url) if args.manual and args.url else "google_docs")
    asyncio.run(
        _run(
            app=app,
            provider_config=args.provider_config,
            overlay=args.overlay,
            apply=args.apply,
            manual=args.manual,
            url=args.url,
            probe=args.probe,
        )
    )


if __name__ == "__main__":
    main()
