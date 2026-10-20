"""Open a blank Google Sheet in the automation profile, with NO timeout, and capture its id.

The ui_mode runs mint their host Sheet either via the Sheets API (needs OAuth we haven't
authorized) or via the browser (a redirect that's been flaky). This sidesteps both: it opens
``sheets.new`` in the SAME persistent Playwright profile the runs use, waits (no timer) while
you log in / let the blank sheet load, then reads the resulting URL and writes the sheet id to
``out/prepared_sheet_id.txt``. Then a run can open that existing sheet with ``--doc-id``.

Usage (interactive — run in your own terminal, it waits for Enter):
    poetry run python scripts/prepare_sheet.py
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from advtest_ext.adapters._ui_mode_common.config import resolve_profile_dir
from advtest_ext.adapters._ui_mode_common.session import close_context, launch_context

OUT = Path("out/prepared_sheet_id.txt")


async def _run() -> None:
    profile = resolve_profile_dir()
    print(f"launching persistent profile: {profile}")
    context = await launch_context(profile)
    try:
        page = await context.new_page()
        print("navigating to sheets.new (creates a blank Sheet in your signed-in account) …")
        try:
            await page.goto("https://sheets.new", timeout=60000)
        except Exception as exc:
            print(f"(navigation note: {exc}); continue in the window anyway")
        print(
            "\nIn the Chrome window:\n"
            "  1. If it asks you to log in, do so — there is NO timeout here.\n"
            "  2. Wait until you see a blank spreadsheet (URL shows /spreadsheets/d/<id>/edit).\n"
        )
        input("  … press Enter once the blank Sheet is open: ")
        url = getattr(page, "url", "") or ""
        m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
        if m:
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(m.group(1))
            print(f"\n✓ sheet id: {m.group(1)}")
            print(f"  (saved to {OUT} — I'll pick it up from there)")
        else:
            print(f"\n✗ could not find a sheet id in the URL:\n  {url!r}")
            print("  Make sure you're on a blank spreadsheet, then re-run.")
    finally:
        await close_context(context)
    print("\nprofile released. tell me it's done and I'll run the guide against this sheet.")


if __name__ == "__main__":
    asyncio.run(_run())
