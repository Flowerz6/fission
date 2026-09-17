"""Account preflight fails loudly instead of silently planting as the wrong identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from advtest_ext.core.account_pool import AccountPool, AccountSlot
from advtest_ext.guides.stage import preflight_setup_accounts


def _pool(tmp_path: Path, *, token_exists: bool) -> AccountPool:
    token = tmp_path / "attacker-token.json"
    if token_exists:
        token.write_text("{}", encoding="utf-8")
    return AccountPool(
        slots=[
            AccountSlot(
                role="attacker_sender",
                profile_dir=tmp_path / "profile",
                token_cache=token,
                label=None,
            )
        ]
    )


def test_none_pool_is_a_noop() -> None:
    preflight_setup_accounts(None)  # single-account guide: no error


def test_missing_second_account_token_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError) as exc:
        preflight_setup_accounts(_pool(tmp_path, token_exists=False))
    msg = str(exc.value)
    assert "attacker_sender" in msg
    assert "ADVTEST_OAUTH_TOKEN_ATTACKER_SENDER" in msg  # names the env var to set
    assert "test nothing" in msg  # explains why it matters


def test_provisioned_second_account_passes(tmp_path: Path) -> None:
    preflight_setup_accounts(_pool(tmp_path, token_exists=True))  # no error


def test_recipient_slot_is_skipped(tmp_path: Path) -> None:
    """The recipient legitimately uses the default identity — no token required."""
    pool = AccountPool(
        slots=[
            AccountSlot(
                role="recipient",
                profile_dir=tmp_path / "p",
                token_cache=tmp_path / "missing-token.json",
                label=None,
            )
        ]
    )
    preflight_setup_accounts(pool)  # no error despite the missing token
