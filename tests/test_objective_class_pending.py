"""B5: a held objective class is visible, not silently swallowed.

``safe_objective_class`` must catch ``PendingObjectiveClass`` separately from an
ordinary failure, bump a module counter, and still return None so scoring does
not raise. The counter lets a harness assert a held id never reached scoring.
"""

from __future__ import annotations

import advtest_ext.core.orch_trace_meta as otm
from advtest_ext.scoring.guide_map import PendingObjectiveClass


def test_routed_id_does_not_touch_the_pending_counter() -> None:
    before = otm.PENDING_OBJECTIVE_HITS
    assert otm.safe_objective_class("leak_system_prompt") == "system_prompt_disclosure"
    # out_of_scope / unknown ids resolve to None, still no pending hit.
    assert otm.safe_objective_class("totally-unknown-id") is None
    assert otm.PENDING_OBJECTIVE_HITS == before


def test_pending_objective_class_is_counted_and_returns_none(monkeypatch) -> None:
    def _raise(behavior_id: str) -> str:
        raise PendingObjectiveClass(f"held: {behavior_id}")

    monkeypatch.setattr("advtest_ext.scoring.guide_map.resolve_objective_class", _raise)
    before = otm.PENDING_OBJECTIVE_HITS
    result = otm.safe_objective_class("sheets_skill_leak")
    assert result is None  # does not raise into scoring
    assert otm.PENDING_OBJECTIVE_HITS == before + 1
