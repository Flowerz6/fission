"""One-time: authorize ALL Google Workspace API scopes into a single token.

The API adapters (docs/sheets/slides/calendar/gmail planting + content writes)
each request their own scopes via ``_auth.get_service``. If the cached OAuth
token doesn't already cover a requested scope, the auth layer pops an
interactive consent flow — which hangs in a background run. This script requests
the UNION of every adapter's scopes in ONE consent, so afterwards every API path
reuses the cached token with no prompt.

Prereqs (do these once in the Google Cloud console for project ``advtest-2``):
  1. Enable the APIs: Sheets, Docs, Drive, Slides, Calendar, Gmail.
  2. OAuth consent screen → Test users → add the account(s) you authorize here.

Usage (interactive — opens a browser for the consent click):
    poetry run python scripts/authorize_apis.py                    # recipient (victim) token
    poetry run python scripts/authorize_apis.py --token-cache ~/.advtest/oauth-token-attacker-sender.json
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# Pull each surface's scope tuple straight from the code so the union stays
# complete as adapters change.
from advtest_ext.adapters._auth import DOCS_SCOPES, GMAIL_SCOPES, SHEETS_SCOPES
from advtest_ext.adapters.calendar.target import CALENDAR_SCOPES
from advtest_ext.adapters.slides.target import SLIDES_SCOPES

# Full Drive (not just drive.file) so a guide can also read/seed arbitrary
# Drive files for the Drive surface.
_DRIVE_FULL = ("https://www.googleapis.com/auth/drive",)

ALL_SCOPES: list[str] = sorted(
    set(DOCS_SCOPES)
    | set(SHEETS_SCOPES)
    | set(SLIDES_SCOPES)
    | set(CALENDAR_SCOPES)
    | set(GMAIL_SCOPES)
    | set(_DRIVE_FULL)
)

_DEFAULT_CLIENT = Path.home() / ".advtest" / "oauth-client.json"
_DEFAULT_TOKEN = Path.home() / ".advtest" / "oauth-token.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--client-secrets",
        default=os.environ.get("ADVTEST_OAUTH_CLIENT_SECRETS", str(_DEFAULT_CLIENT)),
        help="OAuth client secrets JSON (default: ADVTEST_OAUTH_CLIENT_SECRETS or ~/.advtest/oauth-client.json)",
    )
    ap.add_argument(
        "--token-cache",
        default=os.environ.get("ADVTEST_OAUTH_TOKEN_CACHE", str(_DEFAULT_TOKEN)),
        help="where to write the token (default: the recipient token ~/.advtest/oauth-token.json)",
    )
    args = ap.parse_args()

    client = Path(args.client_secrets).expanduser()
    token = Path(args.token_cache).expanduser()
    if not client.exists():
        raise SystemExit(f"client secrets not found: {client}")

    print("Requesting consent for ALL Workspace scopes:")
    for s in ALL_SCOPES:
        print("  -", s)
    print(f"\nclient: {client}\ntoken → {token}\n")
    print("A browser will open — pick the account and click Allow.\n")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials as UserCreds
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token.exists():
        try:
            creds = UserCreds.from_authorized_user_file(str(token), ALL_SCOPES)
        except Exception:
            creds = None
    # Force a fresh consent when the cached token doesn't already cover every
    # scope (has_scopes only true if the union is a subset of granted scopes).
    if creds is not None and creds.valid and creds.has_scopes(ALL_SCOPES):
        print("cached token already covers all scopes — nothing to do.")
        return
    if creds is not None and creds.expired and creds.refresh_token and creds.has_scopes(ALL_SCOPES):
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(client), ALL_SCOPES)
        creds = flow.run_local_server(port=0)

    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    try:
        token.chmod(0o600)
    except OSError:
        pass
    print(f"\n✓ token written to {token}")
    print("  every API adapter (docs/sheets/slides/calendar/gmail/drive) will now reuse it.")


if __name__ == "__main__":
    main()
