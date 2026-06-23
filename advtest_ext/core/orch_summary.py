"""Run-summary aggregation: fold a flat list of traces into SummaryStats
(ASR breakdown + judge agreement). Extracted from ``orchestrator.py``;
re-exported there for back-compat.
"""

from __future__ import annotations

from advtest_ext.core.models import AsrBreakdownRow, AttackTrace, SummaryStats


def summarise(traces: list[AttackTrace]) -> SummaryStats:
    """Aggregate a flat list of traces into a :class:`SummaryStats`.

    ``error``-verdict traces are EXCLUDED from ASR denominators (they
    don't count against the attack's success rate, since the attempt
    didn't actually exercise the target). They DO appear in
    ``total_traces`` so the operator can see the failure surface.
    """
    rows: dict[tuple[str, str | None, str], AsrBreakdownRow] = {}
    successes = 0
    counted = 0  # non-error attempts
    by_judge: dict[str, list[bool]] = {}
    for trace in traces:
        is_error = trace.verdict == "error"
        is_success = trace.verdict == "success"
        attack_name = trace.payload.generator_name
        technique_id = trace.technique.id if trace.technique else None
        key = (attack_name, technique_id, trace.behavior_id)
        row = rows.setdefault(
            key,
            AsrBreakdownRow(
                attack_name=attack_name,
                technique_id=technique_id,
                behavior_id=trace.behavior_id,
            ),
        )
        if not is_error:
            row.attempts += 1
            counted += 1
            if is_success:
                row.successes += 1
                successes += 1
        for s in trace.judge_scores:
            by_judge.setdefault(s.judge_name, []).append(s.flagged)

    for row in rows.values():
        row.asr = (row.successes / row.attempts) if row.attempts else 0.0
    overall = (successes / counted) if counted else 0.0

    # Pairwise judge agreement on flagged-vs-not.
    agreement: dict[str, float] = {}
    judge_names = sorted(by_judge)
    for i, a in enumerate(judge_names):
        for b in judge_names[i + 1 :]:
            la, lb = by_judge[a], by_judge[b]
            n = min(len(la), len(lb))
            if n == 0:
                continue
            matches = sum(1 for x, y in zip(la[:n], lb[:n], strict=True) if x == y)
            agreement[f"{a}|{b}"] = matches / n

    return SummaryStats(
        total_traces=len(traces),
        successful_traces=successes,
        overall_asr=overall,
        asr_breakdown=list(rows.values()),
        judge_agreement_rates=agreement,
    )
