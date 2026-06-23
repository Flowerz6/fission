"""Budget accounting for orchestrator runs.

Extracted from ``orchestrator.py`` so the run loop reads as orchestration,
not bookkeeping. ``BudgetExceeded`` is re-exported by ``orchestrator`` for
back-compat (``from advtest_ext.core.orchestrator import BudgetExceeded``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from advtest_ext.core.models import Budget

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.providers.cost import CostLedger


class BudgetExceeded(Exception):  # noqa: N818 — not an "Error", a budget-control sentinel
    """Raised by ``_BudgetTracker`` to short-circuit the run when any
    of ``max_queries`` / ``max_seconds`` / ``max_cost_usd`` is hit.

    Named without the ``Error`` suffix because it is a control-flow
    sentinel rather than a programming error: catching it at the run
    boundary is the *expected* path when the operator deliberately
    sets a tight budget.
    """


@dataclass
class _BudgetTracker:
    """Hard-stop accountant for the three Budget axes. Thread-safe via
    a single lock so multiple concurrent attempts can ``charge`` from
    the same instance."""

    budget: Budget
    cost_ledger: CostLedger | None = None
    started_at: float = field(default_factory=time.monotonic)
    queries_used: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def reserve_one_query(self) -> None:
        """Reserve one query slot. Raises :class:`BudgetExceeded` if the
        run has already passed any of its limits OR if reserving
        another query would cross ``max_queries``. Reserved BEFORE the
        target call so we never exceed the cap mid-attempt."""
        async with self._lock:
            self._raise_if_time_or_cost_exceeded()
            if self.budget.max_queries is not None:
                if self.queries_used >= self.budget.max_queries:
                    raise BudgetExceeded(f"max_queries={self.budget.max_queries} reached")
            self.queries_used += 1

    def _raise_if_time_or_cost_exceeded(self) -> None:
        if self.budget.max_seconds is not None:
            elapsed = time.monotonic() - self.started_at
            if elapsed >= self.budget.max_seconds:
                raise BudgetExceeded(
                    f"max_seconds={self.budget.max_seconds} reached (elapsed={elapsed:.2f}s)"
                )
        if self.budget.max_cost_usd is not None and self.cost_ledger is not None:
            if self.cost_ledger.total_cost_usd >= self.budget.max_cost_usd:
                raise BudgetExceeded(f"max_cost_usd={self.budget.max_cost_usd} reached")

    def snapshot(self) -> dict[str, Any]:
        return {
            "queries_used": self.queries_used,
            "elapsed_seconds": round(time.monotonic() - self.started_at, 3),
            "cost_usd": (self.cost_ledger.total_cost_usd if self.cost_ledger else 0.0),
        }
