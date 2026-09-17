"""Authorize a Google account for a staging role and cache its OAuth token.

Cross-account guides plant their carrier AS a second account (e.g. the
``attacker_sender`` that emails the victim). The plant ops are API-based, so that
account needs its own cached OAuth token — distinct from the recipient's. This
script runs the interactive OAuth flow for one role and writes its token to the
path that role's env var points at, so a later run picks it up automatically.

It must run where a browser can open (a local desktop session): the flow pops a
Google consent screen. In the Claude Code terminal, prefix with ``!`` so it runs
in your session:

    ! poetry run python scripts/authorize_account.py --role attacker_sender

Sign in as the SENDER account in the browser that opens. On success the token is
written and the script prints the env var to set (already added to .env by the
wiring, but shown for clarity).

Roles: attacker_sender, bystander, recipient (recipient usually already set up).
Scopes default to gmail (send/read); pass --scopes for docs/sheets too.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from advtest_ext.adapters._auth import DOCS_SCOPES, GMAIL_SCOPES, SHEETS_SCOPES, get_service

_ADVTEST_HOME = Path.home() / ".advtest"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from a repo .env for keys not already set.

    A plain script run (unlike the dashboard) does not auto-load .env, so the
    auth env vars (ADVTEST_OAUTH_CLIENT_SECRETS, ADVTEST_AUTH_MODE, the role token
    paths) would be missing. This mirrors how the dashboard injects .env: real
    process-env values win; simple KEY=VALUE lines only, no shell execution."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value and value[0] in "'\"":
            quote = value[0]
            end = value.find(quote, 1)
            if end != -1:
                value = value[1:end]
        else:
            value = value.split(" #", 1)[0].rstrip()
        if key and not os.environ.get(key):
            os.environ[key] = value

# role -> (token env var, default token filename)
_ROLE_TOKEN = {
    "recipient": ("ADVTEST_OAUTH_TOKEN_CACHE", "oauth-token.json"),
    "attacker_sender": ("ADVTEST_OAUTH_TOKEN_ATTACKER_SENDER", "oauth-token-attacker-sender.json"),
    "bystander": ("ADVTEST_OAUTH_TOKEN_BYSTANDER", "oauth-token-bystander.json"),
}

_SCOPE_BUNDLES = {
    "gmail": GMAIL_SCOPES,
    "docs": DOCS_SCOPES,
    "sheets": SHEETS_SCOPES,
}

# gmail send + read needs a discovery service that exercises the token.
_PROBE_API = {"gmail": ("gmail", "v1"), "docs": ("docs", "v1"), "sheets": ("sheets", "v4")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--role", choices=sorted(_ROLE_TOKEN), default="attacker_sender")
    ap.add_argument(
        "--scopes",
        nargs="+",
        choices=sorted(_SCOPE_BUNDLES),
        default=["gmail"],
        help="scope bundles to request (default: gmail).",
    )
    ap.add_argument(
        "--token-path",
        type=Path,
        default=None,
        help="override where the token is written (default: the role's env var or ~/.advtest path).",
    )
    args = ap.parse_args()

    # Load repo .env so ADVTEST_OAUTH_CLIENT_SECRETS / ADVTEST_AUTH_MODE / role
    # token paths are present the same way the dashboard sees them.
    _load_dotenv(_REPO_ROOT / ".env")

    env_var, default_name = _ROLE_TOKEN[args.role]
    token_path = (
        args.token_path
        or (Path(os.environ[env_var]) if os.environ.get(env_var) else _ADVTEST_HOME / default_name)
    ).expanduser()

    scopes: tuple[str, ...] = tuple(
        s for bundle in args.scopes for s in _SCOPE_BUNDLES[bundle]
    )
    # Use the first requested bundle's API to force the consent flow + token write.
    api, version = _PROBE_API[args.scopes[0]]

    if os.environ.get("ADVTEST_AUTH_MODE") != "oauth_user":
        print(
            "note: ADVTEST_AUTH_MODE is not 'oauth_user'; this script authorizes the "
            "interactive OAuth flow. Set ADVTEST_AUTH_MODE=oauth_user first."
        )

    print(f"Authorizing role {args.role!r} -> token {token_path}")
    print(f"Requesting scopes: {', '.join(scopes)}")
    print("A browser will open — sign in as the account for this role.\n")

    # get_service triggers _build_oauth_user_credentials(token_cache=token_path),
    # which pops the consent screen when no valid token exists and persists it.
    get_service(api, version, scopes, token_cache=token_path)

    print(f"\n✓ token written to {token_path}")
    print(f"  wired via {env_var} (see .env). Cross-account guides for role "
          f"{args.role!r} will now plant as this account.")


if __name__ == "__main__":
    main()
