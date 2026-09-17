"""Tests for multi-account guide setup: acting AS a distinct account.

Covers the pieces that let a guide's setup step (e.g. an attacker mailbox
planting the bait email) authenticate as a different account than the one
under test:

- ``AccountSlot.token_cache`` carries the per-account OAuth token.
- ``get_service`` / ``_credentials_for`` thread that token cache through.
- ``gmail.send_email`` picks the token cache from the Step's account slot.
- ``RecipeRunner`` resolves the account slot for API-only ops (no session pool).
- ``build_setup_account_pool`` derives roles → (profile, token) from the
  operator env conventions.

No live Google / Playwright — the auth builder and Gmail client are faked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from advtest_ext.core.account_pool import AccountPool, AccountSlot
from advtest_ext.core.recipe import Recipe, Step
from advtest_ext.core.recipe_operations import (
    OperationContext,
    OperationRegistry,
    OperationResult,
)
from advtest_ext.core.recipe_runner import RecipeRunner
from advtest_ext.core.surface import Surface

# ----------------------------------------------------------------------
# AccountSlot.token_cache
# ----------------------------------------------------------------------


def test_account_slot_carries_token_cache(tmp_path: Path) -> None:
    s = AccountSlot(
        role="attacker_sender",
        profile_dir=tmp_path / "p",
        token_cache=tmp_path / "tok.json",
    )
    assert s.token_cache == (tmp_path / "tok.json").expanduser()


def test_account_slot_token_cache_expands_home() -> None:
    s = AccountSlot(
        role="attacker_sender",
        profile_dir=Path("~/p"),
        token_cache=Path("~/tok.json"),
    )
    assert str(s.token_cache).startswith(str(Path.home()))


def test_account_slot_token_cache_defaults_none(tmp_path: Path) -> None:
    s = AccountSlot(role="recipient", profile_dir=tmp_path / "p")
    assert s.token_cache is None


# ----------------------------------------------------------------------
# get_service / _credentials_for token-cache threading
# ----------------------------------------------------------------------


def test_credentials_for_threads_token_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_credentials_for`` under oauth_user mode must hand the token cache
    to the builder, so distinct accounts resolve to distinct credentials."""
    from advtest_ext.adapters import _auth

    _auth.clear_credentials_cache()
    seen: dict[str, Any] = {}

    def _fake_builder(scopes: tuple[str, ...], token_cache: Path | None = None) -> Any:
        seen["token_cache"] = token_cache
        return object()

    monkeypatch.setattr(_auth, "_build_oauth_user_credentials", _fake_builder)

    _auth._credentials_for("oauth_user", ("scope-a",), Path("/tmp/atk-token.json"))
    assert seen["token_cache"] == Path("/tmp/atk-token.json")
    _auth.clear_credentials_cache()


def test_credentials_for_token_cache_is_cache_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two different token caches must not collide in the @cache — otherwise
    the second account would silently get the first's credentials."""
    from advtest_ext.adapters import _auth

    _auth.clear_credentials_cache()
    calls: list[Path | None] = []

    def _fake_builder(scopes: tuple[str, ...], token_cache: Path | None = None) -> Any:
        calls.append(token_cache)
        return object()

    monkeypatch.setattr(_auth, "_build_oauth_user_credentials", _fake_builder)
    _auth._credentials_for("oauth_user", ("s",), Path("/tmp/a.json"))
    _auth._credentials_for("oauth_user", ("s",), Path("/tmp/b.json"))
    _auth._credentials_for("oauth_user", ("s",), Path("/tmp/a.json"))  # cache hit
    assert calls == [Path("/tmp/a.json"), Path("/tmp/b.json")]
    _auth.clear_credentials_cache()


# ----------------------------------------------------------------------
# gmail.send_email uses the Step's account slot token cache
# ----------------------------------------------------------------------


class _FakeGmail:
    """Minimal Gmail client stub: records the send and returns ids."""

    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}

    def users(self) -> _FakeGmail:
        return self

    def getProfile(self, userId: str) -> _FakeExec:  # noqa: N802, N803 (google api name)
        return _FakeExec({"emailAddress": "attacker@example.com"})

    def messages(self) -> _FakeGmail:
        return self

    def send(self, userId: str, body: dict[str, Any]) -> _FakeExec:  # noqa: N803
        self.sent = body
        return _FakeExec({"threadId": "T1", "id": "M1"})


class _FakeExec:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def execute(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_send_email_uses_account_slot_token_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from advtest_ext.adapters.gmail import recipe_ops as gmail_ops

    captured: dict[str, Any] = {}

    def _fake_get_service(
        api: str, ver: str, scopes: Any, token_cache: Path | None = None
    ) -> _FakeGmail:
        captured["token_cache"] = token_cache
        return _FakeGmail()

    monkeypatch.setattr(gmail_ops, "get_service", _fake_get_service)

    slot = AccountSlot(
        role="attacker_sender",
        profile_dir=tmp_path / "p",
        token_cache=tmp_path / "atk.json",
    )
    step = Step(
        id="plant",
        intent="plant bait",
        surface=Surface(app="gmail", region="compose", account_role="attacker_sender"),
        operation="send_email",
        params={"to": "victim@example.com", "subject": "hi", "body": "<p>x</p>"},
    )
    ctx = OperationContext(
        step=step,
        params=step.params,
        session=None,
        account_slot=slot,
    )
    result = await gmail_ops.send_email(ctx)
    assert result.outputs["thread_id"] == "T1"
    assert captured["token_cache"] == (tmp_path / "atk.json").expanduser()


@pytest.mark.asyncio
async def test_send_email_falls_back_to_default_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No account slot → no token cache override → default identity."""
    from advtest_ext.adapters.gmail import recipe_ops as gmail_ops

    captured: dict[str, Any] = {"token_cache": "unset"}

    def _fake_get_service(
        api: str, ver: str, scopes: Any, token_cache: Path | None = None
    ) -> _FakeGmail:
        captured["token_cache"] = token_cache
        return _FakeGmail()

    monkeypatch.setattr(gmail_ops, "get_service", _fake_get_service)
    step = Step(
        id="plant",
        intent="plant bait",
        surface=Surface(app="gmail", region="compose", account_role="recipient"),
        operation="send_email",
        params={"to": "me", "subject": "hi", "body": "<p>x</p>"},
    )
    ctx = OperationContext(step=step, params=step.params, session=None, account_slot=None)
    await gmail_ops.send_email(ctx)
    assert captured["token_cache"] is None


# ----------------------------------------------------------------------
# RecipeRunner resolves the slot for API-only ops (no session pool)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_resolves_slot_without_session_pool(tmp_path: Path) -> None:
    """An API-only op (no UI session) must still receive its account slot so
    it can act as that account. session_pool is None; account_pool is set."""
    seen: dict[str, Any] = {}
    reg = OperationRegistry()

    @reg.register("gmail", "send_email")
    async def _capture(ctx: OperationContext) -> OperationResult:
        seen["slot"] = ctx.account_slot
        seen["session"] = ctx.session
        return OperationResult(outputs={"thread_id": "T"})

    step = Step(
        id="plant",
        intent="plant",
        surface=Surface(app="gmail", region="compose", account_role="attacker_sender"),
        operation="send_email",
        params={},
    )
    recipe = Recipe(
        id="r",
        seed_id="s",
        name="n",
        content_hash="sha256:x",
        steps=[step],
    )
    pool = AccountPool(
        slots=[
            AccountSlot(
                role="attacker_sender",
                profile_dir=tmp_path / "p",
                token_cache=tmp_path / "atk.json",
            )
        ]
    )
    runner = RecipeRunner(
        recipe=recipe,
        operations=reg,
        account_pool=pool,
        session_pool=None,  # API-only: no browser needed
    )
    result = await runner.run()
    assert result.step_observations[0].fired is True
    assert seen["session"] is None
    assert seen["slot"] is not None
    assert seen["slot"].token_cache == (tmp_path / "atk.json").expanduser()


# ----------------------------------------------------------------------
# build_setup_account_pool env conventions
# ----------------------------------------------------------------------


def _gmail_attacker_guide() -> Any:
    from advtest_ext.guides import load_guide

    return load_guide(Path("guides/gmail_123432_tool_disclosure.yaml"))


def test_build_setup_account_pool_attacker_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from advtest_ext.guides.stage import build_setup_account_pool

    monkeypatch.setenv("ADVTEST_PROFILE_ATTACKER_SENDER", "/tmp/atk-profile")
    monkeypatch.setenv("ADVTEST_OAUTH_TOKEN_ATTACKER_SENDER", "/tmp/atk-token.json")
    pool = build_setup_account_pool(_gmail_attacker_guide())
    assert pool is not None
    slot = pool.get_slot("attacker_sender")
    assert str(slot.profile_dir) == "/tmp/atk-profile"
    assert str(slot.token_cache) == "/tmp/atk-token.json"


def test_build_setup_account_pool_defaults_under_advtest_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from advtest_ext.guides.stage import build_setup_account_pool

    monkeypatch.delenv("ADVTEST_PROFILE_ATTACKER_SENDER", raising=False)
    monkeypatch.delenv("ADVTEST_OAUTH_TOKEN_ATTACKER_SENDER", raising=False)
    pool = build_setup_account_pool(_gmail_attacker_guide())
    assert pool is not None
    slot = pool.get_slot("attacker_sender")
    assert slot.token_cache is not None
    assert slot.token_cache.name == "oauth-token-attacker-sender.json"
    assert str(slot.profile_dir).endswith("profile-attacker-sender")


def test_build_setup_account_pool_none_for_recipient_only() -> None:
    """A guide whose setup drives only the recipient (or has no setup) needs
    no distinct account — legacy single-account path."""
    from advtest_ext.guides import load_guide
    from advtest_ext.guides.stage import build_setup_account_pool

    g = load_guide(Path("guides/sheets_skill_leak.yaml"))  # no setup steps
    assert build_setup_account_pool(g) is None


def test_all_promoted_gmail_guides_load_and_map() -> None:
    from advtest_ext.guides import load_guide
    from advtest_ext.guides.corpus import resolve_guide_path
    from advtest_ext.guides.to_tap_crescendo import map_guide

    expected_screenshot_turns = {
        "gmail_00000_configuration_footer": 1,
        "gmail_0090911_tool_disclosure": 1,
        "gmail_123432_tool_disclosure": 1,
        "gmail_9101_manifest_canary": 4,
        "gmail_summarize_next_script": 3,
    }
    for gid, expected_turns in expected_screenshot_turns.items():
        path = resolve_guide_path(Path("guides"), gid)
        assert path is not None, f"guide {gid} not found in guides/"
        g = load_guide(path)
        plan = map_guide(g)
        assert plan.behavior is not None
        assert plan.attack_kwargs.get("seed_chain")
        assert len(plan.attack_kwargs["seed_chain"]) == expected_turns
