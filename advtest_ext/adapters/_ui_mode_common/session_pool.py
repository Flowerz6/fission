"""``UiModeSessionPool`` — manages multiple per-account Playwright
sessions for one Run.

Each Workspace account in the
:class:`advtest_ext.core.account_pool.AccountPool` gets its own
``UiModeSession`` (one persistent Chrome instance, one
``user_data_dir``). The pool opens sessions lazily — Chrome
launches the first time a given role is requested, reuses across
subsequent invocations within the same Run, and closes everything
on :meth:`close`.

Product-agnostic: takes a ``session_factory`` callable so each
target (Docs, Gmail, future Calendar/Slides) can wire in its own
``UiModeSession`` class without this module needing to import any
product-specific code. The factory is invoked once per role with
``Path(profile_dir)`` as the single argument; it should return
an object with the standard ``UiModeSession`` lifecycle
(``ensure_open`` / ``ensure_page`` / ``close``).

Phase 4 of the :doc:`red_team_report_infra_plan`. Built so
Phase-5's Recipe Runner can resolve a Step's ``account_role`` to
a concrete browser session in one call.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from advtest_ext.core.account_pool import AccountPool
from advtest_ext.core.surface import AccountRole

# A session factory takes a profile dir and returns a session
# object. Typed as a generic callable so each product can pass its
# own ``UiModeSession`` class without us importing it here.
SessionFactory = Callable[[Path], Any]


class UiModeSessionPool:
    """Role → :class:`UiModeSession` registry for one Run.

    Single-account behaviour (no pool needed): when the
    underlying ``AccountPool`` is empty, callers continue to use
    their target's existing single-session path. This class only
    materialises when a Recipe explicitly references multiple
    roles.

    Usage::

        pool = UiModeSessionPool(account_pool, factory)
        session = await pool.session_for("recipient")
        await pool.close()

    Reusable across many steps within one Run; close once at
    Run end.
    """

    def __init__(
        self,
        account_pool: AccountPool,
        session_factory: SessionFactory,
    ) -> None:
        self._pool = account_pool
        self._factory = session_factory
        # role → session instance. Populated lazily by session_for.
        self._sessions: dict[AccountRole, Any] = {}

    async def session_for(self, role: AccountRole) -> Any:
        """Return the session for ``role``, creating it if needed.

        Raises ``KeyError`` if ``role`` isn't in the underlying
        :class:`AccountPool` — a Recipe that references an
        unbound role fails fast rather than silently using the
        wrong account.
        """
        existing = self._sessions.get(role)
        if existing is not None:
            return existing
        slot = self._pool.get_slot(role)
        session = self._factory(slot.profile_dir)
        self._sessions[role] = session
        return session

    def has_open_session(self, role: AccountRole) -> bool:
        """True iff a session has been materialised for ``role``.
        Useful for tests + the dashboard's pool-status view."""
        return role in self._sessions

    def open_roles(self) -> list[AccountRole]:
        """Return the list of roles with currently-open sessions,
        in insertion order. Mostly diagnostic."""
        return list(self._sessions)

    async def close(self) -> None:
        """Close every materialised session. Safe to call when no
        sessions were ever opened (no-op) and safe to call
        multiple times (idempotent — each close is wrapped in
        try/except so partial-tear-down failures don't strand
        sibling sessions)."""
        for role, session in list(self._sessions.items()):
            try:
                await session.close()
            except Exception:
                # One broken session must not strand the others.
                pass
            finally:
                self._sessions.pop(role, None)
