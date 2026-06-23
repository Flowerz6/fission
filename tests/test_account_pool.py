"""Tests for the Phase-4 account pool + session pool.

No Playwright, no real Chrome — the session pool's factory is a
trivial fake so we can exercise the role-lookup and lifecycle
without launching browsers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from advtest_ext.adapters._ui_mode_common import UiModeSessionPool
from advtest_ext.core.account_pool import AccountPool, AccountSlot

# ----------------------------------------------------------------------
# AccountSlot
# ----------------------------------------------------------------------


def test_account_slot_minimal(tmp_path: Path) -> None:
    s = AccountSlot(role="recipient", profile_dir=tmp_path / "p")
    assert s.role == "recipient"
    assert s.profile_dir == (tmp_path / "p").expanduser()
    assert s.label is None


def test_account_slot_expands_user_home(tmp_path: Path) -> None:
    """Profile-dir paths with ``~`` should expand at construction
    so the pool never carries an un-expanded path downstream."""
    s = AccountSlot(role="recipient", profile_dir=Path("~/test-profile"))
    assert str(s.profile_dir).startswith(str(Path.home()))


def test_account_slot_rejects_unknown_role(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        AccountSlot(role="overlord", profile_dir=tmp_path / "p")  # type: ignore[arg-type]


def test_account_slot_is_frozen(tmp_path: Path) -> None:
    """Slots are immutable so they're safe to share across threads
    / hold as dict keys."""
    s = AccountSlot(role="recipient", profile_dir=tmp_path / "p")
    with pytest.raises(ValidationError):
        s.role = "attacker_sender"  # type: ignore[misc]


# ----------------------------------------------------------------------
# AccountPool
# ----------------------------------------------------------------------


def test_account_pool_empty() -> None:
    p = AccountPool()
    assert len(p) == 0
    assert list(p) == []


def test_account_pool_get_slot(tmp_path: Path) -> None:
    p = AccountPool(
        slots=[
            AccountSlot(role="recipient", profile_dir=tmp_path / "a"),
            AccountSlot(role="attacker_sender", profile_dir=tmp_path / "b"),
        ]
    )
    assert p.has_role("recipient")
    assert p.has_role("attacker_sender")
    assert not p.has_role("bystander")
    slot = p.get_slot("recipient")
    assert slot.profile_dir == tmp_path / "a"


def test_account_pool_get_slot_raises_for_missing_role(tmp_path: Path) -> None:
    p = AccountPool(slots=[AccountSlot(role="recipient", profile_dir=tmp_path / "a")])
    with pytest.raises(KeyError, match="no slot bound to role"):
        p.get_slot("attacker_sender")


def test_account_pool_rejects_duplicate_roles(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="duplicate role"):
        AccountPool(
            slots=[
                AccountSlot(role="recipient", profile_dir=tmp_path / "a"),
                AccountSlot(role="recipient", profile_dir=tmp_path / "b"),
            ]
        )


def test_account_pool_is_iterable(tmp_path: Path) -> None:
    slots = [
        AccountSlot(role="recipient", profile_dir=tmp_path / "a"),
        AccountSlot(role="attacker_sender", profile_dir=tmp_path / "b"),
    ]
    p = AccountPool(slots=slots)
    assert list(p) == slots


# ----------------------------------------------------------------------
# UiModeSessionPool
# ----------------------------------------------------------------------


class _FakeSession:
    """Stand-in for a real UiModeSession. Tracks open / close calls
    so tests can assert lifecycle without launching Chrome."""

    instances_opened: list[Path] = []  # noqa: RUF012 — class-level test-state

    def __init__(self, profile_dir: Path) -> None:
        self.profile_dir = Path(profile_dir)
        self.closed = False
        type(self).instances_opened.append(self.profile_dir)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fake_session_state() -> Any:
    _FakeSession.instances_opened.clear()
    yield


def _pool(tmp_path: Path) -> AccountPool:
    return AccountPool(
        slots=[
            AccountSlot(role="recipient", profile_dir=tmp_path / "recipient"),
            AccountSlot(role="attacker_sender", profile_dir=tmp_path / "sender"),
        ]
    )


@pytest.mark.asyncio
async def test_session_pool_lazy_construction(tmp_path: Path) -> None:
    """Sessions should only materialise when first requested — no
    Chrome launches at pool-init time."""
    sp = UiModeSessionPool(_pool(tmp_path), _FakeSession)
    assert _FakeSession.instances_opened == []
    sess = await sp.session_for("recipient")
    assert isinstance(sess, _FakeSession)
    assert _FakeSession.instances_opened == [tmp_path / "recipient"]


@pytest.mark.asyncio
async def test_session_pool_reuses_same_session_for_role(tmp_path: Path) -> None:
    sp = UiModeSessionPool(_pool(tmp_path), _FakeSession)
    s1 = await sp.session_for("recipient")
    s2 = await sp.session_for("recipient")
    assert s1 is s2
    # Factory only called once.
    assert len(_FakeSession.instances_opened) == 1


@pytest.mark.asyncio
async def test_session_pool_independent_per_role(tmp_path: Path) -> None:
    sp = UiModeSessionPool(_pool(tmp_path), _FakeSession)
    s_recipient = await sp.session_for("recipient")
    s_sender = await sp.session_for("attacker_sender")
    assert s_recipient is not s_sender
    assert s_recipient.profile_dir != s_sender.profile_dir


@pytest.mark.asyncio
async def test_session_pool_session_for_unbound_role_raises(tmp_path: Path) -> None:
    sp = UiModeSessionPool(_pool(tmp_path), _FakeSession)
    with pytest.raises(KeyError):
        await sp.session_for("bystander")


@pytest.mark.asyncio
async def test_session_pool_has_open_session_tracks_state(tmp_path: Path) -> None:
    sp = UiModeSessionPool(_pool(tmp_path), _FakeSession)
    assert not sp.has_open_session("recipient")
    await sp.session_for("recipient")
    assert sp.has_open_session("recipient")
    assert not sp.has_open_session("attacker_sender")


@pytest.mark.asyncio
async def test_session_pool_close_closes_all_open_sessions(tmp_path: Path) -> None:
    sp = UiModeSessionPool(_pool(tmp_path), _FakeSession)
    s1 = await sp.session_for("recipient")
    s2 = await sp.session_for("attacker_sender")
    await sp.close()
    assert s1.closed is True
    assert s2.closed is True
    assert sp.open_roles() == []


@pytest.mark.asyncio
async def test_session_pool_close_is_idempotent(tmp_path: Path) -> None:
    sp = UiModeSessionPool(_pool(tmp_path), _FakeSession)
    await sp.session_for("recipient")
    await sp.close()
    await sp.close()  # second call must not raise


@pytest.mark.asyncio
async def test_session_pool_close_swallows_per_session_errors(tmp_path: Path) -> None:
    """One broken session's close() shouldn't strand its siblings."""

    class _ExplosiveSession:
        def __init__(self, profile_dir: Path) -> None:
            self.profile_dir = profile_dir
            self.closed = False

        async def close(self) -> None:
            self.closed = True
            raise RuntimeError("kaboom")

    sp = UiModeSessionPool(_pool(tmp_path), _ExplosiveSession)
    s1 = await sp.session_for("recipient")
    s2 = await sp.session_for("attacker_sender")
    await sp.close()  # should not raise
    assert s1.closed is True
    assert s2.closed is True
    assert sp.open_roles() == []


@pytest.mark.asyncio
async def test_session_pool_with_empty_pool(tmp_path: Path) -> None:
    """Empty pool → session_for always raises, close is no-op."""
    sp = UiModeSessionPool(AccountPool(), _FakeSession)
    with pytest.raises(KeyError):
        await sp.session_for("recipient")
    await sp.close()
