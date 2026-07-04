"""Capture the live Gemini side-panel DOM so selectors can be (re)written.

The ui_mode selectors are demo-grade and drift whenever Google ships UI
changes. This script opens a Workspace page in YOUR logged-in browser profile,
lets you open the Gemini side panel, then dumps everything needed to write
correct selectors — the full HTML, a filtered list of candidate elements
(anything Gemini/Send/New-conversation/Close-labelled, every complementary
region, every contenteditable, and the shared ``appsElementsSidekick*``
component classes), and a screenshot.

Hand the JSON (or the whole ``out/inspect/`` dir) back and the selectors in
``adapters/*/ui_mode/selectors.py`` can be updated from real DOM, not guesses.

It only reads the DOM — it sends no prompt and runs no attack.

Usage:
  # Docs (to fix the drift you hit), or Sheets (to author the new adapter):
  poetry run python scripts/inspect_side_panel.py --url https://docs.google.com/document/d/<id>/edit
  poetry run python scripts/inspect_side_panel.py --url https://docs.google.com/spreadsheets/d/<id>/edit

Prereq: the same logged-in Playwright profile your ui_mode runs use
(``ADVTEST_PLAYWRIGHT_PROFILE_DIR``), and ``--extras ui_mode`` installed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from advtest_ext.adapters._ui_mode_common.selector_heal import capture_candidates


async def _run(url: str, out_dir: Path) -> None:
    from advtest_ext.adapters._ui_mode_common import UiModeSession
    from advtest_ext.adapters._ui_mode_common.config import resolve_profile_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    session = UiModeSession(profile_dir=resolve_profile_dir())
    try:
        page = await session.ensure_page()
        print(f"navigating to {url} …")
        await page.goto(url)
        print(
            "\nA browser window is open. In it:\n"
            "  1. make sure you're logged into the right Google account,\n"
            "  2. OPEN the Gemini side panel (click the Gemini icon),\n"
            "  3. come back here and press Enter to capture."
        )
        input("  … press Enter once the Gemini panel is visible: ")

        html = await page.content()
        (out_dir / "page.html").write_text(html, encoding="utf-8")

        probe = await capture_candidates(page)
        (out_dir / "candidates.json").write_text(
            json.dumps(probe, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        shot = out_dir / "screenshot.png"
        try:
            await page.screenshot(path=str(shot))
        except Exception as exc:  # screenshot is best-effort
            print(f"(screenshot failed: {exc})")

        print(f"\nwrote:\n  {out_dir / 'page.html'}\n  {out_dir / 'candidates.json'}\n  {shot}")
        print("\n--- candidate summary ---")
        print(f"Gemini/Send/New/Close-labelled elements : {len(probe['labelled'])}")
        print(f"role=complementary regions              : {len(probe['complementary'])}")
        print(f"contenteditable fields                  : {len(probe['editables'])}")
        print(f"distinct appsElementsSidekick* classes  : {len(probe['sidekickClasses'])}")
        for item in probe["labelled"][:12]:
            print(f"  [{item['tag']} role={item['role']}] aria-label={item['ariaLabel']!r}")
        print("\nSend candidates.json back and I'll write the exact selectors.")
    finally:
        await session.close()


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--url", required=True, help="Workspace page URL (a Doc or Sheet you can open)")
    p.add_argument(
        "--out", type=Path, default=Path("./out/inspect"), help="output dir (default ./out/inspect)"
    )
    args = p.parse_args()
    asyncio.run(_run(args.url, args.out))


if __name__ == "__main__":
    main()
