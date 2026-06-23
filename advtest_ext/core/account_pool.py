"""``AccountPool`` — the role-keyed registry of Workspace accounts a
Run can drive.

A Recipe declares accounts by ROLE
(``recipient`` / ``attacker_sender`` / ``bystander`` — see
:mod:`advtest_ext.core.surface`). At Run launch, the operator
binds each role to a concrete Chrome profile via
``RunConfig.account_pool``. The orchestrator then hands the
resolved AccountPool to the targets, which look up Playwright
sessions by role.

Without this layer, multi-account Recipes (V1 in the Google Red
Team Report: "Account B emails Account A, then A opens Docs")
can't be represented: the existing single-profile model has
nowhere to put "the OTHER account."

Backward compatibility: configs without an ``account_pool:``
block run unchanged. The legacy single-profile path
(``~/.advtest/playwright-profile``) still drives every target
the way it does today.

Phase 4 of the :doc:`red_team_report_infra_plan`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advtest_ext.core.surface import AccountRole


class AccountSlot(BaseModel):
    """One Chrome profile bound to one role.

    The ``profile_dir`` is a persistent Playwright
    ``user_data_dir`` containing the operator's signed-in
    Google session cookies. Set up once via
    :mod:`scripts.setup_playwright_profile` (the
    ``--profile-dir`` / ``--role`` flags).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AccountRole
    profile_dir: Path = Field(
        description=(
            "Persistent Chrome profile directory holding the "
            "signed-in session cookies for this account."
        ),
    )
    label: str | None = Field(
        default=None,
        description=(
            "Human-readable label (typically the email address) "
            "shown on the dashboard's account-pool view. Optional "
            "— omit for sensitive engagements where account labels "
            "shouldn't land in the operator-facing UI."
        ),
    )

    @field_validator("profile_dir")
    @classmethod
    def _expand_user(cls, v: Path) -> Path:
        return Path(v).expanduser()


class AccountPool(BaseModel):
    """The set of role-labelled accounts available to a Run.

    Constructed from ``RunConfig.account_pool`` at Run launch.
    Enforces role uniqueness — two slots can't claim the same
    role (the runtime needs an unambiguous role → profile_dir
    lookup).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    slots: list[AccountSlot] = Field(default_factory=list)

    @field_validator("slots")
    @classmethod
    def _enforce_unique_roles(cls, slots: list[AccountSlot]) -> list[AccountSlot]:
        seen: set[str] = set()
        for s in slots:
            if s.role in seen:
                raise ValueError(
                    f"duplicate role {s.role!r} in account pool — "
                    "each role must bind to exactly one profile"
                )
            seen.add(s.role)
        return slots

    def get_slot(self, role: AccountRole) -> AccountSlot:
        """Return the slot bound to ``role``. Raises ``KeyError``
        when the role isn't in the pool — Recipes that reference
        unbound roles fail loudly at Run launch."""
        for s in self.slots:
            if s.role == role:
                return s
        available = sorted(s.role for s in self.slots)
        raise KeyError(f"no slot bound to role {role!r}; available roles: {available}")

    def has_role(self, role: AccountRole) -> bool:
        return any(s.role == role for s in self.slots)

    def __len__(self) -> int:
        return len(self.slots)

    def __iter__(self) -> Iterator[AccountSlot]:  # type: ignore[override]
        return iter(self.slots)
