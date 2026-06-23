"""Set up the persistent Playwright profile for ui_mode demos.

Opens a real Chrome window against the on-disk profile directory
so the user can sign in to a Google account once. The profile
persists between runs; subsequent ``ui_mode_invoke`` calls reuse it
without an OAuth prompt.

Run this once before the pitch (TODO_BEFORE_PITCH.md item 3):

    poetry run python scripts/setup_playwright_profile.py

The script will:

1. Resolve the profile directory from
   ``ADVTEST_PLAYWRIGHT_PROFILE_DIR`` (default
   ``~/.advtest/playwright-profile``); create it if missing.
2. Launch a headed Chrome (``channel="chrome"``) against that profile.
3. Open https://accounts.google.com and wait for you to sign in.
4. Print a message confirming the profile was written and exit
   cleanly when you close the browser.

This script DOES talk to a live Google sign-in page. CLAUDE.md
forbids running it in-session — it's a manual pre-flight only.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

_PROFILE_ENV = "ADVTEST_PLAYWRIGHT_PROFILE_DIR"
_DEFAULT_PROFILE_DIR = Path.home() / ".advtest" / "playwright-profile"

# Named shortcuts: ``--service <name>`` resolves to (profile_dir,
# start_url) so the operator doesn't have to remember exact URLs +
# profile paths for each service we automate. Adding a new service
# = one line here + a one-time login.
_SERVICE_SHORTCUTS: dict[str, tuple[Path, str]] = {
    "google": (
        _DEFAULT_PROFILE_DIR,
        "https://accounts.google.com/",
    ),
    "outlook": (
        Path.home() / ".advtest" / "profile-outlook-sender",
        "https://outlook.live.com/owa/",
    ),
    "gmail": (
        # The Gmail SENDER profile used for the Gmail->Outlook ack
        # (GmailUiComposeSession). Sign in as the recipient Gmail
        # account (the one the bait lands in / the side panel grounds on).
        Path.home() / ".advtest" / "profile-gmail-sender",
        "https://mail.google.com/",
    ),
}


def _profile_dir() -> Path:
    raw = os.environ.get(_PROFILE_ENV)
    return Path(raw).expanduser() if raw else _DEFAULT_PROFILE_DIR


async def _launch(profile: Path, start_url: str, role: str | None) -> None:
    """Open the headed Chrome window and wait for the user to close it.

    Closing the window is the "I'm done signing in" signal — there's
    no programmatic done-state for the Google login flow, so we
    rely on the user driving the UI.

    ``role`` is purely informational at this script's level — it's
    a reminder for the operator about which account to sign in as.
    The runtime AccountPool binds roles to profile_dirs via config;
    we just print the role here so a multi-profile setup session
    doesn't accidentally sign the wrong account into the wrong dir.
    """
    from playwright.async_api import async_playwright

    profile.mkdir(parents=True, exist_ok=True)

    role_note = f" (role: {role})" if role else ""
    print(f"Launching system Chrome against profile: {profile}{role_note}")
    if role:
        print(f"This profile is intended for the '{role}' role. Sign in to the")
        print("account you've designated for that role — typically:")
        print("  recipient        — the victim (Gemini operates on their behalf)")
        print("  attacker_sender  — plants content into the recipient's inbox")
        print("  bystander        — third-party account referenced by the recipe")
    else:
        print("Sign in to the Workspace AI Premium account in the open browser.")
    print("Close the browser window when you're done — that's the exit signal.")

    pw = await async_playwright().start()
    try:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            channel="chrome",  # use system Chrome, not bundled Chromium
            slow_mo=200,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
                "--no-first-run",
            ],
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1280, "height": 900},
        )
        # Open the sign-in page in a fresh tab.
        page = await context.new_page()
        await page.goto(start_url)
        # Block on the context being closed. Playwright fires the
        # ``close`` event on context teardown; awaiting an
        # ``asyncio.Event`` set inside the handler gives us a
        # cross-platform wait.
        closed = asyncio.Event()
        context.on("close", lambda: closed.set())
        await closed.wait()
    finally:
        await pw.stop()

    print(f"Profile saved at {profile}")
    # Print the demo script's ABSOLUTE path (works regardless of the cwd
    # the operator launched from — relative 'scripts/...' breaks when run
    # from advtest_ext/).
    demo = Path(__file__).resolve().parent / "demo_ui_scenarios.py"
    if demo.exists():
        print(f"(optional) sanity-check ui_mode with: poetry run python {demo} --scenario list")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--service",
        choices=sorted(_SERVICE_SHORTCUTS),
        default=None,
        help=(
            "Convenience shortcut — fills in --profile-dir and "
            "--start-url for a known service. 'google' sets up the "
            "default Workspace recipient profile; 'outlook' sets "
            "up the Outlook UI compose profile that the "
            "outlook.send_email handler uses. Explicit "
            "--profile-dir / --start-url override the shortcut."
        ),
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help=(
            f"Profile directory (overrides ${_PROFILE_ENV} and any "
            f"--service shortcut). Default: {_DEFAULT_PROFILE_DIR}."
        ),
    )
    parser.add_argument(
        "--start-url",
        default=None,
        help=(
            "Initial URL to load. Defaults to the --service shortcut's "
            "URL, or Google sign-in when no shortcut is given."
        ),
    )
    parser.add_argument(
        "--role",
        choices=["recipient", "attacker_sender", "bystander"],
        default=None,
        help=(
            "Optional role label for this profile. Informational "
            "only — the runtime AccountPool config binds role to "
            "profile_dir. Use when setting up multiple accounts "
            "for a multi-account Recipe (V1-style cross-account "
            "chain): run this script once per role with a "
            "distinct --profile-dir each time."
        ),
    )
    args = parser.parse_args()
    # Resolution order: explicit --profile-dir / --start-url win;
    # otherwise --service shortcut; otherwise env var / Google default.
    shortcut = _SERVICE_SHORTCUTS.get(args.service) if args.service else None
    profile = args.profile_dir or (shortcut[0] if shortcut else _profile_dir())
    start_url = args.start_url or (shortcut[1] if shortcut else "https://accounts.google.com/")
    asyncio.run(_launch(profile, start_url, args.role))


if __name__ == "__main__":
    main()
