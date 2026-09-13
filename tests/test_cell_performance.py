"""Tests for the empirical cell-performance gate (``guides.cell_performance``).

The gate decides which mutation cells to keep by evidence, not opinion: a cell in
a verified-success trajectory is a performer; a cell tried repeatedly with no
success is a prune candidate; an oracle-only success is flagged 'unverified' (the
LLM judge over-calls). These tests pin that classification and the prune step.
"""

from __future__ import annotations

from advtest_ext.guides.cell_performance import (
    CellUsage,
    assess_cell_performance,
    prune_from_guide_dict,
)

_AUTHORED = {"persona_ladder": ["deputy agent"], "proof_pressure": ["audit-proof framing"]}


def _report(usages, *, min_attempts=3):
    return assess_cell_performance(
        "sheets_skill_leak", usages, _AUTHORED, n_runs=2, min_attempts_to_prune=min_attempts
    )


def _stat(report, value):
    return next(s for s in report.stats if s.value == value)


def test_verified_win_makes_a_performer() -> None:
    usages = [CellUsage("persona_ladder", "shadow auditor", success=True, verified=True)]
    rep = _report(usages)
    assert _stat(rep, "shadow auditor").status == "performer"
    assert "shadow auditor" in [s.value for s in rep.promote]


def test_oracle_only_success_is_unverified_not_performer() -> None:
    usages = [CellUsage("persona_ladder", "sandbox operator", success=True, verified=False)]
    rep = _report(usages)
    assert _stat(rep, "sandbox operator").status == "unverified"
    assert not rep.promote  # not promoted on the over-calling oracle alone


def test_repeated_failure_is_prune_candidate() -> None:
    usages = [
        CellUsage("proof_pressure", "weak framing", success=False, verified=False) for _ in range(4)
    ]
    rep = _report(usages, min_attempts=3)
    assert _stat(rep, "weak framing").status == "underperformer"
    assert "weak framing" in [s.value for s in rep.prune]


def test_few_attempts_is_inconclusive_not_pruned() -> None:
    usages = [CellUsage("proof_pressure", "new framing", success=False, verified=False)]
    rep = _report(usages, min_attempts=3)
    assert _stat(rep, "new framing").status == "inconclusive"
    assert not rep.prune


def test_authored_but_unused_cell_is_untried() -> None:
    rep = _report([])
    assert _stat(rep, "deputy agent").status == "untried"


def test_prune_never_empties_an_operator() -> None:
    # The only authored cell is an underperformer; pruning must NOT remove it.
    usages = [
        CellUsage("proof_pressure", "audit-proof framing", success=False, verified=False)
        for _ in range(5)
    ]
    rep = _report(usages)
    assert _stat(rep, "audit-proof framing").status == "underperformer"
    raw = {
        "mutation_operators": [{"name": "proof_pressure", "cell_values": ["audit-proof framing"]}]
    }
    out = prune_from_guide_dict(raw, rep)
    assert out["mutation_operators"][0]["cell_values"] == ["audit-proof framing"]  # kept


def test_prune_removes_underperformers_but_keeps_others() -> None:
    usages = [
        CellUsage("persona_ladder", "bad persona", success=False, verified=False) for _ in range(4)
    ]
    rep = assess_cell_performance(
        "g",
        usages,
        {"persona_ladder": ["deputy agent", "bad persona"]},
        n_runs=1,
        min_attempts_to_prune=3,
    )
    raw = {
        "mutation_operators": [
            {"name": "persona_ladder", "cell_values": ["deputy agent", "bad persona"]}
        ]
    }
    out = prune_from_guide_dict(raw, rep)
    assert out["mutation_operators"][0]["cell_values"] == ["deputy agent"]  # bad one dropped
