"""One-off: log the automation Playwright profile into Google, with NO timeout.

The ui_mode runs drive a *persistent* Chromium profile
(``~/.advtest/playwright-profile`` by default). Google logins persist in it
across runs — but a run's selector timeouts close the browser long before a
human can type a password. This helper opens that same profile and simply
WAITS (no timer) until you press Enter, so you can log in at your own pace.
The session is saved in the profile and every later run reuses it.

Usage (interactive — run in your own terminal, it prompts for Enter):

    poetry run python scripts/login_profile.py            # account /u/0
    poetry run python scripts/login_profile.py --index 1  # a different /u/<n>

After it says "profile saved", re-run your guide normally.
"""

from __future__ import annotations

import argparse
import asyncio

from advtest_ext.adapters._ui_mode_common.config import resolve_profile_dir
from advtest_ext.adapters._ui_mode_common.session import close_context, launch_context


async def _run(index: int) -> None:
    profile = resolve_profile_dir()
    print(f"launching persistent profile: {profile}")
    context = await launch_context(profile)
    try:
        page = await context.new_page()
        # Land on Docs for the chosen account index — this is the surface the
        # runs mint against, so logging in here proves /u/<index> works.
        await page.goto(f"https://docs.google.com/document/u/{index}/")
        print(
            "\n"
            "In the Chrome window that just opened:\n"
            "  1. Log into the VICTIM Google account (the one Gemini reads).\n"
            "  2. Make sure you can see Google Docs for that account.\n"
            "  3. If it's not account /u/0, note which /u/<n> it is.\n"
            "\nTake as long as you need — there is no timeout here.\n"
        )
        input("  … press Enter once you are fully logged in: ")
        # Re-read the URL so we can tell the user the real account index to use.
        try:
            print(f"\ncurrent URL: {getattr(page, 'url', '') or '(unknown)'}")
        except Exception:
            pass
    finally:
        await close_context(context)
    print("\nprofile saved. re-run your guide; it will reuse this login.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--index",
        type=int,
        default=0,
        help="Google account index /u/<n> to land on (default 0)",
    )
    args = p.parse_args()
    asyncio.run(_run(args.index))


if __name__ == "__main__":
    main()
