"""Tests for the failure-mode-steered mutation policy (IterInject loop)."""

from __future__ import annotations

from advtest_ext.guides import failure_mode_mutation as fm


def test_success_harvests_not_mutates() -> None:
    assert fm.strategy_for("success").name == "harvest"
    assert fm.strategy_for("success").axis == ""


def test_detected_reframes_for_legitimacy_not_obfuscation() -> None:
    # Our Gemini finding: resist/refuse → legitimacy reframe, never heavier obfuscation.
    first = fm.strategy_for("detected")
    assert first.name == "legitimacy_reframe"
    assert first.axis == "pretext_scenario"


def test_ignored_raises_authority() -> None:
    assert fm.strategy_for("ignored").name == "authority_boost"


def test_partial_escalates() -> None:
    assert fm.strategy_for("partial").name == "escalate"


def test_rotation_advances_on_recurrence() -> None:
    names = [fm.strategy_for("detected", prior_labels=["detected"] * i).name for i in range(4)]
    # cycles legitimacy_reframe → camouflage → format_shift → (wraps) legitimacy_reframe
    assert names == ["legitimacy_reframe", "camouflage", "format_shift", "legitimacy_reframe"]


def test_dominant_failure_mode_ignores_success() -> None:
    assert fm.dominant_failure_mode(["detected", "detected", "ignored", "success"]) == "detected"
    assert fm.dominant_failure_mode(["success", "success"]) == "success"
    assert fm.dominant_failure_mode([]) == "detected"


def test_steer_hint_shapes_next_seed() -> None:
    assert "DISTINCT" in fm.steer_hint(["success", "success"])
    # First detected failure → recommend the legitimacy reframe (our Gemini finding).
    assert "legitimacy" in fm.steer_hint(["detected"]).lower()
    # The hint always names the dominant mode and an axis to move.
    hint = fm.steer_hint(["detected", "detected"])
    assert "detected" in hint and "axis" in hint
