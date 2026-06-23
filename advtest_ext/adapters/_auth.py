"""Shared Google Workspace authentication helper.

Two auth modes selected via ``ADVTEST_AUTH_MODE``:

* ``service_account`` — load a JSON key from
  ``ADVTEST_SA_KEY_PATH`` and (optionally) impersonate a user via
  ``ADVTEST_SA_IMPERSONATE`` for domain-wide delegation. The JSON
  key file must come from a service account with the relevant
  scopes pre-authorised in the Workspace admin console.
* ``oauth_user`` — InstalledAppFlow against the OAuth client
  secrets at ``ADVTEST_OAUTH_CLIENT_SECRETS``, with a refresh-token
  cache at ``ADVTEST_OAUTH_TOKEN_CACHE`` (default
  ``~/.advtest/oauth-token.json``). First run pops a browser; later
  runs reuse the cached token and refresh it transparently.

Public entry: :func:`get_service` returns an authorised
``googleapiclient.discovery.Resource`` for any (api, version, scopes)
combination. The Workspace adapters
(:class:`GoogleDocsTarget` / ``GmailTarget`` / ``GoogleSheetsTarget``,
landing in a follow-up prompt) all go through this helper so the
auth surface is one module.

Caching: credentials are LRU-cached by ``(mode, scopes)`` so
repeated calls reuse the same authenticated principal. The cache is
deliberately at credential-build level rather than discovery-Resource
level — building a Resource is cheap, but building credentials does
disk I/O (and, for the OAuth flow, an interactive browser pop).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.auth.credentials import Credentials


# ----------------------------------------------------------------------
# Scope bundles
# ----------------------------------------------------------------------


# Read/write Docs + create-and-delete the test doc through Drive.
# ``drive.file`` (not ``drive``) limits scope to files this app
# created — the principle-of-least-privilege default for adversarial
# testing where the SA might own elevated access in a customer
# tenant.
DOCS_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
)

# Gmail.modify covers send + read + label management — the surface
# the GeminiJack-style exfiltration tests exercise. Use
# ``gmail.readonly`` for read-only test profiles.
GMAIL_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.modify",)

# Sheets read/write + drive.file for create-and-delete bookkeeping
# (the smoke probe pattern Docs uses).
SHEETS_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
)


# ----------------------------------------------------------------------
# Env-var contract
# ----------------------------------------------------------------------


_ENV_MODE = "ADVTEST_AUTH_MODE"
_ENV_SA_KEY = "ADVTEST_SA_KEY_PATH"
_ENV_SA_IMPERSONATE = "ADVTEST_SA_IMPERSONATE"
_ENV_OAUTH_SECRETS = "ADVTEST_OAUTH_CLIENT_SECRETS"
_ENV_OAUTH_TOKEN_CACHE = "ADVTEST_OAUTH_TOKEN_CACHE"

_DEFAULT_OAUTH_TOKEN_CACHE = Path.home() / ".advtest" / "oauth-token.json"


# ----------------------------------------------------------------------
# Credential builders (cached)
# ----------------------------------------------------------------------


def _require_env(name: str, *, hint: str) -> str:
    """Read an env var or raise a :class:`RuntimeError` whose
    message names the missing variable AND the resolution. Generic
    ``KeyError`` from ``os.environ`` would land less helpfully in
    the typical "I'm running this for the first time" failure mode.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"environment variable {name!r} is required for the chosen auth mode — {hint}"
        )
    return value


def _build_service_account_credentials(scopes: tuple[str, ...]) -> Credentials:
    """Service-account flow. Optional ``ADVTEST_SA_IMPERSONATE``
    triggers domain-wide-delegation impersonation via
    ``Credentials.with_subject``."""
    from google.oauth2 import service_account

    key_path = _require_env(
        _ENV_SA_KEY,
        hint="point ADVTEST_SA_KEY_PATH at the service-account JSON key file",
    )
    # google-auth ships no type stubs in the pre-commit hook's isolated
    # env; full ``poetry run mypy`` (which sees google-auth proper) needs
    # these ignores or the calls trip ``no-untyped-call`` / ``attr-defined``.
    # Add ``unused-ignore`` so the hook env (where the calls look fine)
    # doesn't itself complain. Same trick on every google-auth call below.
    creds: Credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call,unused-ignore]
        key_path, scopes=list(scopes)
    )
    impersonate = os.environ.get(_ENV_SA_IMPERSONATE)
    if impersonate:
        creds = creds.with_subject(impersonate)  # type: ignore[attr-defined,unused-ignore]
    return creds


def _resolve_oauth_token_cache() -> Path:
    raw = os.environ.get(_ENV_OAUTH_TOKEN_CACHE)
    return Path(raw).expanduser() if raw else _DEFAULT_OAUTH_TOKEN_CACHE


def _cache_covers_scopes(cache_path: Path, scopes: tuple[str, ...]) -> bool:
    """Read the on-disk OAuth token JSON and check whether the
    *granted* scopes (the ``scopes`` field the original consent
    flow wrote) cover every entry in ``scopes``.

    Why we don't use ``Credentials.has_scopes`` for this:
    ``has_scopes`` reads ``creds.scopes``, which on a cred built via
    ``from_authorized_user_file(path, scopes)`` reflects the
    *requested* scope set passed into the constructor — not what
    the user originally consented to. So ``has_scopes`` always
    returns True for whatever the new caller is asking for, even
    if the on-disk grant is a strict subset, and the API rejects
    the call with a 403 "insufficient authentication scopes" only
    at request time. Reading the JSON directly is the only way to
    catch the mismatch before we hand back unusable creds.
    """
    import json as _json

    try:
        data = _json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    granted = set(data.get("scopes") or [])
    return set(scopes).issubset(granted)


def _build_oauth_user_credentials(scopes: tuple[str, ...]) -> Credentials:
    """OAuth2 installed-app flow. On a cache hit the refresh token
    is exercised silently; on a cache miss (or corrupt cache) we
    pop the browser for consent. The token is rewritten on disk
    after each successful refresh so subsequent runs stay quiet."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials as UserCreds
    from google_auth_oauthlib.flow import InstalledAppFlow

    cache_path = _resolve_oauth_token_cache()
    # Drop the cache up-front when the on-disk grant doesn't cover
    # what we're now asking for. Otherwise we'd happily hand back
    # creds whose ``valid`` is True but whose server-side grant is
    # too narrow.
    if cache_path.exists() and not _cache_covers_scopes(cache_path, scopes):
        cache_path.unlink(missing_ok=True)

    creds: UserCreds | None = None
    if cache_path.exists():
        try:
            creds = UserCreds.from_authorized_user_file(  # type: ignore[no-untyped-call,unused-ignore]
                str(cache_path), list(scopes)
            )
        except (ValueError, OSError):
            creds = None  # corrupt cache — fall through to fresh flow

    if creds is not None and creds.valid:
        return creds
    if creds is not None and creds.expired and creds.refresh_token:
        creds.refresh(Request())  # type: ignore[no-untyped-call,unused-ignore]
        _persist_oauth_token(creds, cache_path)
        return creds

    secrets_path = _require_env(
        _ENV_OAUTH_SECRETS,
        hint=(
            "point ADVTEST_OAUTH_CLIENT_SECRETS at the OAuth client "
            "JSON downloaded from Google Cloud Console"
        ),
    )
    flow = InstalledAppFlow.from_client_secrets_file(secrets_path, list(scopes))
    creds = flow.run_local_server(port=0)
    _persist_oauth_token(creds, cache_path)
    return creds


def _persist_oauth_token(creds: Any, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(creds.to_json())


@cache
def _credentials_for(mode: str, scopes: tuple[str, ...]) -> Credentials:
    """Cached builder. Re-import the module (or call
    :func:`clear_credentials_cache`) to invalidate."""
    if mode == "service_account":
        return _build_service_account_credentials(scopes)
    if mode == "oauth_user":
        return _build_oauth_user_credentials(scopes)
    raise RuntimeError(
        f"unknown {_ENV_MODE} value {mode!r}; expected 'service_account' or 'oauth_user'"
    )


def clear_credentials_cache() -> None:
    """Drop cached credentials. Tests use this between cases; runtime
    code rarely needs it (changing the auth mode mid-process is not
    a supported workflow)."""
    _credentials_for.cache_clear()


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def get_service(api_name: str, api_version: str, scopes: Sequence[str]) -> Any:
    """Build an authorised ``googleapiclient.discovery.Resource`` for
    ``(api_name, api_version)`` using the configured auth mode and the
    requested ``scopes``.

    The scope tuple is part of the credential cache key so two
    callers asking for distinct scope sets get distinct credentials —
    the Workspace adapters do this when one needs Docs+Drive while
    another needs Gmail-only.
    """
    mode = _require_env(
        _ENV_MODE,
        hint="set ADVTEST_AUTH_MODE to 'service_account' or 'oauth_user'",
    )
    creds = _credentials_for(mode, tuple(scopes))
    from googleapiclient.discovery import build

    # ``cache_discovery=False`` avoids the noisy "file_cache is
    # only supported with oauth2client<4.0.0" warning under modern
    # google-auth — we're not optimising for cold-start latency
    # here, so the in-process discovery cache miss is cheap.
    return build(
        api_name,
        api_version,
        credentials=creds,
        cache_discovery=False,
    )


__all__ = [
    "DOCS_SCOPES",
    "GMAIL_SCOPES",
    "SHEETS_SCOPES",
    "clear_credentials_cache",
    "get_service",
]
