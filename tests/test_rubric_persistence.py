"""§4: the rubric sub-verdict columns persist, and unparsed traces are queryable."""

from __future__ import annotations

from advtest_ext.core.models import AttackTrace, JudgeScore, Payload
from advtest_ext.judges.rubric_ipi import AxisFinding, Mark, RubricVerdict
from advtest_ext.storage import (
    get_trace,
    insert_trace,
    make_engine,
    make_session_factory,
    session_scope,
    traces_with_unparsed_judge,
    update_trace_rubric,
    upsert_run,
)


def _factory(url: str = "sqlite:///:memory:"):
    return make_session_factory(make_engine(url))


def _rubric_verdict(*, r4: str = "yes", gate: str = "accept") -> RubricVerdict:
    return RubricVerdict(
        r1_grounded=AxisFinding(mark=Mark.YES, evidence="span"),
        r2_complied=AxisFinding(mark=Mark.YES, evidence="did a thing"),
        r3_effect_present=AxisFinding(mark=Mark.YES, evidence="leaked text"),
        r4_authentic=AxisFinding(mark=Mark(r4), evidence="CANARY-7" if r4 == "yes" else ""),
        r5_arrived=AxisFinding(mark=Mark.YES, evidence="sink hit"),
        r6_gate=gate,
        r7_defence="none",
        r8_mechanism="known",
        confidence=5,
        reason="downgraded: r2 (yes without evidence)",
    )


def _trace(trace_id: str, *, axes: dict | None, gated: bool, error: bool = False) -> AttackTrace:
    js = JudgeScore(
        judge_name="rubric_ipi",
        score=0.0 if error else 1.0,
        reasoning="r1=yes r2=yes; downgraded: r2 (yes without evidence)",
        flagged=not error,
        error=error,
        axes=axes,
    )
    return AttackTrace(
        id=trace_id,
        run_id="run-rubric",
        behavior_id="system_prompt_disclosure",
        payload=Payload(id=f"{trace_id}-p", text="p", generator_name="static"),
        target_name="StubTarget",
        raw_request="summarise this",
        raw_response="reply",
        judge_scores=[js],
        verdict="error" if error else "success",  # type: ignore[arg-type]
        metadata={"rubric_gated": gated},
    )


def test_rubric_columns_persist_from_insert_trace() -> None:
    factory = _factory()
    v = _rubric_verdict(gate="accept")
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-rubric", target_name="t", strategy="direct")
        insert_trace(s, run_id="run-rubric", trace=_trace("tr1", axes=v.to_axes_dict(), gated=True))
    with session_scope(factory) as s:
        row = get_trace(s, "tr1")
        assert row is not None
        assert row.judge_axes is not None
        assert row.judge_axes["r4_authentic"]["mark"] == "yes"
        assert row.gated is True
        assert row.judge_unparsed is False
        # The downgrade suffix is appended to the stored reasoning.
        js = row.judge_scores[0]
        assert js.reasoning.rstrip().endswith("| downgraded: r2")


def test_unparsed_trace_is_flagged_and_queryable() -> None:
    factory = _factory()
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-rubric", target_name="t", strategy="direct")
        insert_trace(s, run_id="run-rubric", trace=_trace("ok", axes=None, gated=False))
        insert_trace(
            s, run_id="run-rubric", trace=_trace("bad", axes=None, gated=False, error=True)
        )
    with session_scope(factory) as s:
        unparsed = traces_with_unparsed_judge(s, run_id="run-rubric")
        assert {t.id for t in unparsed} == {"bad"}


def test_update_trace_rubric_setter() -> None:
    factory = _factory()
    v = _rubric_verdict()
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-rubric", target_name="t", strategy="direct")
        insert_trace(s, run_id="run-rubric", trace=_trace("tr2", axes=None, gated=False))
    with session_scope(factory) as s:
        update_trace_rubric(
            s, trace_id="tr2", judge_axes=v.to_axes_dict(), gated=True, judge_unparsed=False
        )
    with session_scope(factory) as s:
        row = get_trace(s, "tr2")
        assert row is not None
        assert row.judge_axes is not None
        assert row.gated is True
