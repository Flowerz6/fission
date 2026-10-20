"""Orchestration loop (Part F2).

Schedules, per ``target_profile``: forward native attack runs (Part D mutation
engine → deliver UI-only → Part B observe → Part C score → Part A archive),
sentinel cycles (Part E), and the judge gold-set re-score each cycle. Adds NO new
measurement logic — it runs A-E on a schedule and persists their per-cycle output.

**The one real invariant:** sentinel cycles mutate the tenant (even with reset),
so they MUST NOT run concurrently with native attack runs against the same
``target_profile`` — a per-profile lock enforces phase separation. Different
profiles on different tenants run concurrently.
"""

from __future__ import annotations

from advtest_ext.orchestrate.loop import (
    NativePhaseResult,
    TenantScheduler,
    evaluate_and_record_alerts,
    on_version_change,
    profile_token,
    run_judge_gate,
    run_native_phase,
    run_sentinel_phase,
)

__all__ = [
    "NativePhaseResult",
    "TenantScheduler",
    "evaluate_and_record_alerts",
    "on_version_change",
    "profile_token",
    "run_judge_gate",
    "run_native_phase",
    "run_sentinel_phase",
]
