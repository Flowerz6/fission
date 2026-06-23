"""Tests for ``advtest_ext.adapters._auth``.

All tests mock the Google client libraries — no real credentials,
no network calls. The mocks live tight to the call boundary
(``google.oauth2.service_account.Credentials.from_service_account_file``,
``google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file``,
``googleapiclient.discovery.build``) so the test exercises the auth
module's branching without depending on the libs' internals.

The credential cache is cleared between cases to keep tests
order-independent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from advtest_ext.adapters import _auth
from advtest_ext.adapters._auth import (
    DOCS_SCOPES,
    GMAIL_SCOPES,
    SHEETS_SCOPES,
    clear_credentials_cache,
    get_service,
)


@pytest.fixture(autouse=True)
def _reset_auth_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the lru_cache and unset every advtest auth env var so each
    test starts from the same blank slate. Without this a later test
    inherits an earlier test's mocked credentials and asserts the
    wrong code path."""
    clear_credentials_cache()
    for var in (
        "ADVTEST_AUTH_MODE",
        "ADVTEST_SA_KEY_PATH",
        "ADVTEST_SA_IMPERSONATE",
        "ADVTEST_OAUTH_CLIENT_SECRETS",
        "ADVTEST_OAUTH_TOKEN_CACHE",
    ):
        monkeypatch.delenv(var, raising=False)


# ----------------------------------------------------------------------
# Scope bundles
# ----------------------------------------------------------------------


def test_scope_bundles_carry_required_apis() -> None:
    """Lock the scope contents — drift here means an adapter would
    silently lose access to its API. ``drive.file`` (not ``drive``)
    is the principle-of-least-privilege choice we documented in the
    module docstring."""
    assert "https://www.googleapis.com/auth/documents" in DOCS_SCOPES
    assert "https://www.googleapis.com/auth/drive.file" in DOCS_SCOPES
    assert "https://www.googleapis.com/auth/gmail.modify" in GMAIL_SCOPES
    assert "https://www.googleapis.com/auth/spreadsheets" in SHEETS_SCOPES


# ----------------------------------------------------------------------
# Service-account mode
# ----------------------------------------------------------------------


def test_service_account_mode_loads_key_from_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SA mode reads the key file path from ``ADVTEST_SA_KEY_PATH``
    and forwards it to ``Credentials.from_service_account_file``
    along with the requested scopes."""
    fake_creds = MagicMock(name="sa_creds")
    fake_resource = MagicMock(name="resource")
    sa_loader = MagicMock(return_value=fake_creds)
    build_mock = MagicMock(return_value=fake_resource)

    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        sa_loader,
    )
    monkeypatch.setattr("googleapiclient.discovery.build", build_mock)

    key_path = tmp_path / "sa.json"
    key_path.write_text("{}")  # contents irrelevant under the mock
    monkeypatch.setenv("ADVTEST_AUTH_MODE", "service_account")
    monkeypatch.setenv("ADVTEST_SA_KEY_PATH", str(key_path))

    out = get_service("docs", "v1", DOCS_SCOPES)

    assert out is fake_resource
    sa_loader.assert_called_once()
    args, kwargs = sa_loader.call_args
    assert args[0] == str(key_path)
    assert kwargs["scopes"] == list(DOCS_SCOPES)
    # No impersonation env var set → ``with_subject`` was not called.
    fake_creds.with_subject.assert_not_called()
    # Discovery call gets the SA creds + the requested API.
    build_mock.assert_called_once()
    bargs, bkwargs = build_mock.call_args
    assert bargs[0] == "docs"
    assert bargs[1] == "v1"
    assert bkwargs["credentials"] is fake_creds
    assert bkwargs["cache_discovery"] is False


def test_service_account_mode_applies_impersonation_when_subject_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When ``ADVTEST_SA_IMPERSONATE`` is set, the SA creds get
    ``with_subject(...)`` applied for domain-wide delegation. The
    impersonated creds — not the originals — flow into the discovery
    build call."""
    sa_creds = MagicMock(name="sa_creds")
    impersonated = MagicMock(name="impersonated")
    sa_creds.with_subject.return_value = impersonated
    sa_loader = MagicMock(return_value=sa_creds)
    build_mock = MagicMock(return_value=MagicMock())

    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        sa_loader,
    )
    monkeypatch.setattr("googleapiclient.discovery.build", build_mock)

    monkeypatch.setenv("ADVTEST_AUTH_MODE", "service_account")
    monkeypatch.setenv("ADVTEST_SA_KEY_PATH", str(tmp_path / "sa.json"))
    monkeypatch.setenv("ADVTEST_SA_IMPERSONATE", "user@example.com")

    get_service("gmail", "v1", GMAIL_SCOPES)

    sa_creds.with_subject.assert_called_once_with("user@example.com")
    bkwargs = build_mock.call_args.kwargs
    assert bkwargs["credentials"] is impersonated


def test_service_account_mode_missing_key_path_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADVTEST_AUTH_MODE", "service_account")
    # ``ADVTEST_SA_KEY_PATH`` deliberately unset.
    with pytest.raises(RuntimeError, match="ADVTEST_SA_KEY_PATH"):
        get_service("docs", "v1", DOCS_SCOPES)


# ----------------------------------------------------------------------
# OAuth user-flow mode
# ----------------------------------------------------------------------


def _write_token_file(path: Path, *, scopes: tuple[str, ...] = DOCS_SCOPES) -> None:
    """A non-empty token file. Contents don't have to parse on the
    main path (``Credentials.from_authorized_user_file`` is mocked
    in those tests), but the ``scopes`` field IS read directly by
    ``_cache_covers_scopes`` — so we have to include scopes that
    cover what the test asks for, or the cache check will unlink
    the file before the mocked loader gets a turn."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"refresh_token": "x", "scopes": list(scopes)}))


def test_oauth_user_mode_reuses_valid_cached_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When the token cache exists and the loaded creds report
    ``valid=True``, the auth helper returns them directly — no flow
    pop, no refresh."""
    cache_path = tmp_path / "token.json"
    _write_token_file(cache_path)

    cached = MagicMock(name="cached")
    cached.valid = True
    cached.expired = False
    user_loader = MagicMock(return_value=cached)
    flow_factory = MagicMock(name="from_client_secrets_file")
    build_mock = MagicMock(return_value=MagicMock())

    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file",
        user_loader,
    )
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        flow_factory,
    )
    monkeypatch.setattr("googleapiclient.discovery.build", build_mock)

    monkeypatch.setenv("ADVTEST_AUTH_MODE", "oauth_user")
    monkeypatch.setenv("ADVTEST_OAUTH_TOKEN_CACHE", str(cache_path))
    # ``ADVTEST_OAUTH_CLIENT_SECRETS`` deliberately unset — it MUST
    # NOT be required when the cached token is reusable.

    get_service("docs", "v1", DOCS_SCOPES)

    user_loader.assert_called_once_with(str(cache_path), list(DOCS_SCOPES))
    cached.refresh.assert_not_called()
    flow_factory.assert_not_called()
    bkwargs = build_mock.call_args.kwargs
    assert bkwargs["credentials"] is cached


def test_oauth_user_mode_refreshes_expired_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Expired but refresh-token-bearing creds get refreshed in
    place; the rewritten token persists to disk so the next process
    sees a fresh token."""
    cache_path = tmp_path / "token.json"
    _write_token_file(cache_path)

    expired = MagicMock(name="expired")
    expired.valid = False
    expired.expired = True
    expired.refresh_token = "x"
    expired.to_json.return_value = '{"refreshed": true}'

    user_loader = MagicMock(return_value=expired)
    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file",
        user_loader,
    )
    monkeypatch.setattr("googleapiclient.discovery.build", MagicMock())

    monkeypatch.setenv("ADVTEST_AUTH_MODE", "oauth_user")
    monkeypatch.setenv("ADVTEST_OAUTH_TOKEN_CACHE", str(cache_path))

    get_service("docs", "v1", DOCS_SCOPES)

    expired.refresh.assert_called_once()
    # Token file rewritten with the refreshed JSON.
    assert cache_path.read_text() == '{"refreshed": true}'


def test_oauth_user_mode_runs_flow_when_no_cached_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No token cache on disk → run the InstalledAppFlow against the
    client secrets and persist the resulting token."""
    cache_path = tmp_path / "token.json"
    secrets_path = tmp_path / "client_secret.json"
    secrets_path.write_text("{}")
    assert not cache_path.exists()

    fresh = MagicMock(name="fresh")
    fresh.valid = True
    fresh.expired = False
    fresh.to_json.return_value = '{"fresh": true}'

    flow = MagicMock(name="flow")
    flow.run_local_server.return_value = fresh
    flow_factory = MagicMock(return_value=flow)

    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        flow_factory,
    )
    monkeypatch.setattr("googleapiclient.discovery.build", MagicMock())

    monkeypatch.setenv("ADVTEST_AUTH_MODE", "oauth_user")
    monkeypatch.setenv("ADVTEST_OAUTH_TOKEN_CACHE", str(cache_path))
    monkeypatch.setenv("ADVTEST_OAUTH_CLIENT_SECRETS", str(secrets_path))

    get_service("sheets", "v4", SHEETS_SCOPES)

    flow_factory.assert_called_once_with(str(secrets_path), list(SHEETS_SCOPES))
    flow.run_local_server.assert_called_once()
    assert cache_path.exists()
    assert cache_path.read_text() == '{"fresh": true}'


def test_oauth_user_mode_discards_cached_token_with_insufficient_scopes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If the on-disk token's granted scopes don't cover the new
    request, drop the cache and fall through to a fresh
    InstalledAppFlow. Locks the regression we hit when the Docs
    probe cached a token whose grants didn't include
    ``gmail.modify``. The check reads the JSON directly because
    ``Credentials.has_scopes`` always reflects the requested
    set — never the granted set."""
    cache_path = tmp_path / "token.json"
    secrets_path = tmp_path / "client_secret.json"
    secrets_path.write_text("{}")
    # Cached token records a *narrower* grant than what we're about
    # to ask for — Docs+Drive only.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "refresh_token": "x",
                "scopes": [
                    "https://www.googleapis.com/auth/documents",
                    "https://www.googleapis.com/auth/drive.file",
                ],
            }
        )
    )

    user_loader = MagicMock(name="user_loader")  # must NOT be called

    fresh = MagicMock(name="fresh")
    fresh.valid = True
    fresh.expired = False
    fresh.to_json.return_value = '{"fresh": true}'
    flow = MagicMock(name="flow")
    flow.run_local_server.return_value = fresh
    flow_factory = MagicMock(return_value=flow)

    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.from_authorized_user_file",
        user_loader,
    )
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        flow_factory,
    )
    monkeypatch.setattr("googleapiclient.discovery.build", MagicMock())

    monkeypatch.setenv("ADVTEST_AUTH_MODE", "oauth_user")
    monkeypatch.setenv("ADVTEST_OAUTH_TOKEN_CACHE", str(cache_path))
    monkeypatch.setenv("ADVTEST_OAUTH_CLIENT_SECRETS", str(secrets_path))

    get_service("gmail", "v1", ("https://www.googleapis.com/auth/gmail.modify",))

    # The narrow cache was unlinked before any attempt to load it.
    assert not cache_path.exists() or cache_path.read_text() == '{"fresh": true}'
    user_loader.assert_not_called()
    flow.run_local_server.assert_called_once()  # forced through the fresh flow


def test_oauth_user_mode_missing_secrets_raises_when_no_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "token.json"
    monkeypatch.setenv("ADVTEST_AUTH_MODE", "oauth_user")
    monkeypatch.setenv("ADVTEST_OAUTH_TOKEN_CACHE", str(cache_path))
    # ``ADVTEST_OAUTH_CLIENT_SECRETS`` deliberately unset.
    with pytest.raises(RuntimeError, match="ADVTEST_OAUTH_CLIENT_SECRETS"):
        get_service("docs", "v1", DOCS_SCOPES)


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


def test_missing_auth_mode_raises_clear_runtime_error() -> None:
    """No ``ADVTEST_AUTH_MODE`` → message names the env var AND lists
    the two valid values. Generic ``KeyError`` would land less
    helpfully on a first-time runner."""
    with pytest.raises(RuntimeError, match="ADVTEST_AUTH_MODE"):
        get_service("docs", "v1", DOCS_SCOPES)


def test_unknown_auth_mode_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADVTEST_AUTH_MODE", "magic")
    with pytest.raises(RuntimeError, match="unknown ADVTEST_AUTH_MODE"):
        get_service("docs", "v1", DOCS_SCOPES)


# ----------------------------------------------------------------------
# Caching
# ----------------------------------------------------------------------


def test_credentials_are_cached_per_mode_and_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two calls with the same (mode, scopes) hit the cache and skip
    the second SA-key load. Discovery ``build`` runs each time
    because Resources are intentionally not cached."""
    sa_loader = MagicMock(return_value=MagicMock())
    build_mock = MagicMock(return_value=MagicMock())

    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        sa_loader,
    )
    monkeypatch.setattr("googleapiclient.discovery.build", build_mock)

    monkeypatch.setenv("ADVTEST_AUTH_MODE", "service_account")
    monkeypatch.setenv("ADVTEST_SA_KEY_PATH", str(tmp_path / "sa.json"))

    get_service("docs", "v1", DOCS_SCOPES)
    get_service("drive", "v3", DOCS_SCOPES)
    # Distinct scope set ⇒ separate cache entry ⇒ second loader call.
    get_service("gmail", "v1", GMAIL_SCOPES)

    assert sa_loader.call_count == 2
    assert build_mock.call_count == 3


def test_clear_credentials_cache_forces_reload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sa_loader = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        sa_loader,
    )
    monkeypatch.setattr("googleapiclient.discovery.build", MagicMock())

    monkeypatch.setenv("ADVTEST_AUTH_MODE", "service_account")
    monkeypatch.setenv("ADVTEST_SA_KEY_PATH", str(tmp_path / "sa.json"))

    get_service("docs", "v1", DOCS_SCOPES)
    clear_credentials_cache()
    get_service("docs", "v1", DOCS_SCOPES)
    assert sa_loader.call_count == 2


def test_default_oauth_token_cache_under_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``ADVTEST_OAUTH_TOKEN_CACHE`` is unset the default lands
    at ``~/.advtest/oauth-token.json``. Locks the public default so
    a docs / setup script can reference the path with confidence."""
    monkeypatch.delenv("ADVTEST_OAUTH_TOKEN_CACHE", raising=False)
    expected = Path.home() / ".advtest" / "oauth-token.json"
    assert _auth._resolve_oauth_token_cache() == expected


def test_oauth_token_cache_env_expands_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADVTEST_OAUTH_TOKEN_CACHE", "~/scratch/t.json")
    assert _auth._resolve_oauth_token_cache() == Path.home() / "scratch" / "t.json"


# ----------------------------------------------------------------------
# Defensive: no Google API was actually called
# ----------------------------------------------------------------------


def test_get_service_does_not_touch_real_network_when_mocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Sanity: with the standard mocks in place, `get_service` returns
    whatever ``build`` yields without going near the network. Locks
    the contract that tests can run without internet access."""
    sentinel: Any = object()
    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr("googleapiclient.discovery.build", MagicMock(return_value=sentinel))
    monkeypatch.setenv("ADVTEST_AUTH_MODE", "service_account")
    monkeypatch.setenv("ADVTEST_SA_KEY_PATH", str(tmp_path / "sa.json"))
    assert get_service("docs", "v1", DOCS_SCOPES) is sentinel
