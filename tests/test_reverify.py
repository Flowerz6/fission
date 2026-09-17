"""Tests for re-verifying suspect (oracle-only) wins by reproduction.

An oracle-only win (success, no hard signal) is not discarded and not trusted; it
is re-run in fresh sessions and kept only if it reproduces. These tests pin: which
traces are suspects, that the evidence is saved, the content and win-again
reproduction verdicts, and promotion of reproduced suspects.
"""

from __future__ import annotations

from advtest_ext.guides.reverify import (
    SuspectWin,
    find_suspect_wins,
    promote_reproduced,
    reverify_content,
    reverify_winagain,
)
from advtest_ext.storage import (
    insert_signal_records,
    insert_trace,
    list_guide_variants,
    make_engine,
    make_session_factory,
    session_scope,
    upsert_run,
)
from tests.test_storage import _domain_trace

_PATTERNS = [r"get_skills|execute_plan|sheets_executor|technical_skills/"]
_LEAK = "sheets_executor exposes execute_plan with get_skills and technical_skills/api_safety body"


def _factory():
    return make_session_factory(make_engine("sqlite:///:memory:"))


def test_find_suspect_captures_oracle_only_wins_with_evidence() -> None:
    factory = _factory()
    with session_scope(factory) as s:
        upsert_run(s, run_id="r1", target_name="sheets", strategy="grid")
        # oracle-only success (no signals) -> suspect
        sus = _domain_trace("sus", verdict="success")
        sus.raw_response = _LEAK
        insert_trace(s, run_id="r1", trace=sus)
        # hard-verified success (deterministic signal) -> NOT a suspect
        hard = _domain_trace("hard", verdict="success")
        hard.raw_response = _LEAK
        insert_trace(s, run_id="r1", trace=hard)
        insert_signal_records(
            s,
            trace_id="hard",
            records=[{"name": "canary", "kind": "canary_appears", "fired": True}],
        )

    suspects = find_suspect_wins(factory, ["r1"], artifact_patterns=_PATTERNS)
    ids = {s.trace_id for s in suspects}
    assert ids == {"sus"}  # only the oracle-only one
    sw = suspects[0]
    assert sw.artifact == _LEAK  # evidence saved
    assert "get_skills" in sw.artifact_tokens  # specific success tokens captured


def _suspect(artifact=_LEAK):
    return SuspectWin(
        trace_id="sus",
        run_id="r1",
        behavior_id="behaviour_x",
        trajectory=[{"attacker": "a", "target": artifact}],
        artifact=artifact,
        artifact_tokens=["get_skills"],
    )


def test_content_reproduction_confirms_consistent_leak() -> None:
    sw = _suspect()
    res = reverify_content(sw, [_LEAK, _LEAK], min_consistency=0.5)
    assert res.reproduced is True
    assert res.score > 0.9


def test_content_reproduction_rejects_divergent_leak() -> None:
    sw = _suspect()
    reruns = ["totally different invented content one", "another unrelated fabricated answer two"]
    res = reverify_content(sw, reruns, min_consistency=0.5)
    assert res.reproduced is False


def test_winagain_reproduction() -> None:
    sw = _suspect()
    assert reverify_winagain(sw, [True, True, False], min_winrate=0.5).reproduced is True
    assert reverify_winagain(sw, [True, False, False], min_winrate=0.5).reproduced is False
    assert reverify_winagain(sw, [], min_winrate=0.5).reproduced is False


def test_promote_reproduced_writes_verified_variant() -> None:
    factory = _factory()
    sw = _suspect()
    good = reverify_content(sw, [_LEAK, _LEAK])
    assert promote_reproduced(factory, sw, good, guide_id="sheets_skill_leak") is True
    with session_scope(factory) as s:
        variants = list_guide_variants(s, guide_id="sheets_skill_leak")
    assert len(variants) == 1
    assert variants[0].status == "verified_by_reproduction"


def test_promote_skips_when_not_reproduced() -> None:
    factory = _factory()
    sw = _suspect()
    bad = reverify_winagain(sw, [False, False])
    assert promote_reproduced(factory, sw, bad, guide_id="g") is False
    with session_scope(factory) as s:
        assert list_guide_variants(s, guide_id="g") == []
