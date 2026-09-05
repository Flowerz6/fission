from __future__ import annotations

from advtest_ext.attacks.tap_search import (
    DiversityArchive,
    MutationCandidate,
    MutationDimension,
    calibrated_partial_score,
    parse_candidate,
    typed_operators,
    validate_candidate,
)


def test_typed_operators_cover_mechanism_preserving_dimensions() -> None:
    operators = typed_operators(
        [
            "persona labels",
            "turn order within the escalation",
            "carrier placement in the document body",
            "summary request phrasing",
        ]
    )
    assert {operator.dimension for operator in operators} == {
        MutationDimension.PERSONA,
        MutationDimension.TURN_ORDER,
        MutationDimension.CARRIER_PLACEMENT,
        MutationDimension.WORDING,
    }


def test_hard_gate_rejects_boundary_invariant_and_duplicate_violations() -> None:
    operators = typed_operators(["persona labels"])
    candidate = MutationCandidate(
        strategy="Use the same warm evaluator role",
        dimension=MutationDimension.FRAMING,
        mutation="change the payload",
        preserved_invariants=("wrong invariant",),
    )
    result = validate_candidate(
        candidate,
        operators=operators,
        invariants=["payload remains in the data channel"],
        prior_strategies=["Use the same warm evaluator role"],
        duplicate_threshold=0.75,
    )
    assert result.valid is False
    assert len(result.rejection_reasons) >= 3


def test_plain_text_manual_candidate_becomes_typed_and_preserves_invariants() -> None:
    operators = typed_operators(["turn order"])
    candidate = parse_candidate(
        "Swap the two middle turns",
        operators=operators,
        invariants=["keep the probe unchanged"],
    )
    assert candidate.dimension is MutationDimension.TURN_ORDER
    assert candidate.preserved_invariants == ("keep the probe unchanged",)


def test_archive_retains_best_elite_per_dimension() -> None:
    archive = DiversityArchive()
    first = MutationCandidate("first", MutationDimension.PERSONA, "persona")
    second = MutationCandidate("second", MutationDimension.PERSONA, "persona")
    assert archive.update(first, score=0.4, node_id="n1") is True
    assert archive.update(second, score=0.3, node_id="n2") is False
    assert archive.update(second, score=0.7, node_id="n2") is True
    assert archive.elites["persona:legacy:unspecified"].node_id == "n2"


def test_calibrated_partial_signals_have_distinct_scores_and_never_converge() -> None:
    lower, lower_weights = calibrated_partial_score(
        0.1,
        ["grounded"],
        weights={"grounded": 0.35, "schema": 0.72},
        default_floor=0.2,
        success_threshold=0.85,
    )
    higher, higher_weights = calibrated_partial_score(
        0.1,
        ["schema"],
        weights={"grounded": 0.35, "schema": 0.72},
        default_floor=0.2,
        success_threshold=0.85,
    )
    assert lower == 0.35 and higher == 0.72
    assert lower_weights != higher_weights
    capped, _ = calibrated_partial_score(
        0.1,
        ["too_high"],
        weights={"too_high": 1.0},
        default_floor=0.2,
        success_threshold=0.85,
    )
    assert capped == 0.84
    successful_base, _ = calibrated_partial_score(
        0.9,
        ["grounded"],
        weights={"grounded": 0.35},
        default_floor=0.2,
        success_threshold=0.85,
    )
    assert successful_base == 0.9
