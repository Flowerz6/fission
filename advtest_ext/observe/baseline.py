"""Baseline snapshot + delta diffing, and fixture reset hooks.

``snapshot_baseline`` is called BEFORE the injection; ``diff_observation``
compares an after-snapshot to it. Diffing against a baseline is what stops
accumulated tenant state (old drafts, pre-existing shares) from reading as an
effect. A channel that was ``failed``/``unavailable`` in EITHER snapshot yields
an empty delta with that status propagated — never a false negative.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from advtest_ext.observe.model import (
    CHANNELS,
    ObservationStatus,
    ObservedItem,
    SideEffectObservation,
    TenantState,
)


class WorkspaceObserver(Protocol):
    """Anything that can snapshot tenant state (live or mock)."""

    def snapshot(self) -> TenantState: ...


def snapshot_baseline(observer: WorkspaceObserver) -> TenantState:
    """Capture the pre-injection tenant state."""
    return observer.snapshot()


def _resolve_status(before: ObservationStatus, after: ObservationStatus) -> ObservationStatus:
    # A failure in either snapshot means we can't certify the delta.
    if before == "failed" or after == "failed":
        return "failed"
    if before == "unavailable" or after == "unavailable":
        return "unavailable"
    return "observed"


def diff_observation(before: TenantState, after: TenantState) -> SideEffectObservation:
    """Compute per-channel deltas (items present after but not before)."""
    obs = SideEffectObservation()
    for channel in CHANNELS:
        bstat = before.status.get(channel, "unavailable")
        astat = after.status.get(channel, "unavailable")
        status = _resolve_status(bstat, astat)
        obs.status[channel] = status
        if status == "failed":
            # Prefer the after-snapshot's error kind, else the baseline's.
            kind = after.errors.get(channel) or before.errors.get(channel)
            if kind is not None:
                obs.errors[channel] = kind
            continue
        if status == "unavailable":
            continue
        before_keys = set(before.channel(channel))
        new_items: list[ObservedItem] = [
            item for key, item in after.channel(channel).items() if key not in before_keys
        ]
        setattr(obs, channel, new_items)
    return obs


# ---------------------------------------------------------------------------
# Fixture reset hooks (for the later frozen-sentinel panel — that loop doesn't
# exist yet, but a clean fixture each cycle needs these seams now).
# ---------------------------------------------------------------------------


def setup_fixture(reset: Callable[[], None] | None = None) -> None:
    """Reset the test fixture to a known-clean state before a cycle.

    ``reset`` is the tenant-specific cleaner (delete test drafts, revoke test
    shares, empty the sink). No-op when unset — the seam exists so the panel can
    inject one without this layer knowing tenant details."""
    if reset is not None:
        reset()


def teardown_fixture(reset: Callable[[], None] | None = None) -> None:
    """Reset the fixture after a cycle (symmetric with :func:`setup_fixture`)."""
    if reset is not None:
        reset()


__all__ = [
    "WorkspaceObserver",
    "diff_observation",
    "setup_fixture",
    "snapshot_baseline",
    "teardown_fixture",
]
