#!/usr/bin/env python3
"""Fission — FULL end-to-end demo driver (one command, headed browsers).

What `make fission` runs. Three acts, all visible on screen and all
streaming to the dashboard under ONE run_id:

  Act 0  DELIVER   Outlook -> Gmail bait series, then the Gmail ->
                   Outlook acknowledgment, via the Playwright UI senders
                   (reuses scripts/quillfeather_send.py). Real Chrome
                   windows compose and send the mail on camera.
  Act 1  WAIT      brief pause for Gmail to index the new mail so the
                   side panel can ground on it.
  Act 2  ATTACK    the unified two-phase run via the orchestrator:
                     Phase 1  Gmail -> Docs  (side panel on the inbox)
                     Phase 2  Docs -> Docs   (Drive-doc retrieval)
                   Both phases stream to the dashboard as two trees.

The attack phases are driven by configs/run_unified_gmail_then_docs.yaml
through ``cli._run_async`` — the same code path as ``advtest run`` — so
every turn persists to the dashboard DB exactly like a normal run.

CAUTION — NOT run in-session (CLAUDE.md): sends real email AND drives a
live attacker LLM loop against the real Gemini side panel. Run it
yourself, own accounts only.

PREREQUISITES (one-time, see scripts/setup_playwright_profile.py)
-----------------------------------------------------------------
* google profile  signed into the RECIPIENT Gmail (the side panel drives
                  this inbox; the bait lands here)            [--service google]
* outlook profile signed into the Outlook sender             [--service outlook]
* gmail   profile signed into the Gmail sender (for the ack) [--service gmail]
* ANTHROPIC_API_KEY exported (attacker + StrongREJECT judge are Claude)
* The Docs phase's poisoned Drive doc already present in the account.

USAGE
-----
  # Terminal A — dashboard (leave running):
  cd advtest_ext && make dashboard

  # Terminal B — the whole demo:
  cd advtest_ext && make fission
  #   or directly:
  #   poetry run python ../scripts/fission_e2e.py \
  #       --gmail <recipient> --outlook <sender>
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import logging
import os
import sys
from pathlib import Path

# --- sys.path bootstrap (package dir + scripts dir) -------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_PKG_DIR = _REPO_ROOT / "advtest_ext"
for _p in (str(_PKG_DIR), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import quillfeather_send as qf  # noqa: E402  (UI senders: _send_series, _send_ack)

_DEFAULT_CONFIG = _REPO_ROOT / "configs" / "run_unified_gmail_then_docs.yaml"
_DEFAULT_DB_URL = "sqlite:///./advtest.db"


def _quiet_logs() -> None:
    """Silence the chatty run logs for a clean demo terminal.

    structlog is unconfigured by default (prints every level to stderr,
    including per-turn / model / branch detail). Pin it to ERROR+ and
    do the same for stdlib logging, so the terminal shows only the
    phase banners we print explicitly — no model names, no internals."""
    try:
        import structlog

        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
        )
    except Exception:
        pass
    logging.disable(logging.WARNING)


@contextlib.contextmanager
def _muted():
    """Suppress stdout/stderr from a noisy call (e.g. the senders, which
    print recipient addresses). Keeps identifiable values off-screen.

    BUT if the call RAISES, re-surface the captured output — a swallowed
    failure that just looks like a freeze is worse than a leaked address.
    Set ADVTEST_DEMO_VERBOSE=1 to never mute (debugging)."""
    if os.environ.get("ADVTEST_DEMO_VERBOSE"):
        yield
        return
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            yield
    except BaseException:
        # Delivery failed — show what the muted call printed so the
        # error isn't an invisible hang.
        sys.stderr.write(buf.getvalue())
        raise


def _banner(text: str) -> None:
    line = "─" * 48
    print(f"\n{line}\n  {text}\n{line}")


async def _deliver(args: argparse.Namespace) -> None:
    _banner("Phase 1 · Delivering emails (Outlook ⇄ Gmail)")
    # Mute the senders' own prints — they echo recipient addresses and
    # per-message detail we don't want on screen. (Errors re-surface;
    # see _muted.)
    print("  sending bait (Outlook → Gmail)…", flush=True)
    with _muted():
        await qf._send_series(args.gmail, args.gap)
    if not args.skip_ack:
        print("  sending reply (Gmail → Outlook)…", flush=True)
        with _muted():
            await qf._send_ack(args.outlook)
    print("  emails delivered.")


async def _attack(args: argparse.Namespace) -> int:
    from advtest_ext import cli
    from advtest_ext.storage import make_engine, make_session_factory

    _banner("Phase 2 · Adversarial run (Gmail → Docs, then Docs → Docs)")
    cfg = cli.load_run_config(Path(args.config))
    session_factory = (
        make_session_factory(make_engine(args.db_url)) if args.db_url else None
    )
    print("  running — watch the dashboard for the live trees.")
    result = await cli._run_async(
        strategy="grid",
        config=cfg,
        output_dir=cfg.output_dir,
        session_factory=session_factory,
    )
    _banner("Done")
    if args.db_url:
        print(f"  Dashboard: http://localhost:8000/runs/{result.run_id}/tree")
    return 0


async def _run(args: argparse.Namespace) -> int:
    _quiet_logs()
    if not args.skip_deliver:
        if not args.gmail or not args.outlook:
            print("delivery needs --gmail and --outlook (or pass --skip-deliver)")
            return 2
        await _deliver(args)
        _banner("Indexing")
        await asyncio.sleep(args.index_wait)
    return await _attack(args)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--gmail", default="f04438179@gmail.com",
                   help="Gmail recipient for the bait series")
    p.add_argument("--outlook", default="testuser961206@outlook.com",
                   help="Outlook recipient for the ack reply")
    p.add_argument("--gap", type=float, default=2.0, help="seconds between bait sends")
    p.add_argument("--skip-ack", action="store_true",
                   help="send the bait but not the Gmail→Outlook ack")
    p.add_argument("--skip-deliver", action="store_true",
                   help="skip Acts 0–1; bait already delivered + indexed")
    p.add_argument("--index-wait", type=int, default=20,
                   help="seconds to wait for Gmail to index before the run (default 20)")
    p.add_argument("--config", default=str(_DEFAULT_CONFIG),
                   help="unified phased RunConfig YAML")
    p.add_argument("--db-url", default=_DEFAULT_DB_URL,
                   help="dashboard DB (default sqlite:///./advtest.db). '' disables.")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_parse_args())))
