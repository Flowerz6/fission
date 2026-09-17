"""Anti-automation (bot-detection) vs selector-drift vs attack-error taxonomy."""

from __future__ import annotations

import pytest

from advtest_ext.adapters._ui_mode_common.errors import (
    ATTACK_ERROR,
    BOT_DETECTED,
    SELECTOR_DRIFT,
    AntiAutomationDetected,
    SidePanelUnavailable,
    classify_error_kind,
    looks_like_anti_automation,
)


def test_anti_automation_is_a_side_panel_error() -> None:
    """Existing ``except SidePanelUnavailable`` handlers still catch the subclass."""
    assert issubclass(AntiAutomationDetected, SidePanelUnavailable)


@pytest.mark.parametrize(
    "text",
    [
        "We've detected unusual activity from your computer network",
        "To continue, please verify it's you",
        "verify it’s you",  # noqa: RUF001 — curly apostrophe is the point of the test
        "https://www.google.com/sorry/index?continue=...",
        "Our systems have detected unusual traffic",
        "please confirm you're not a robot",
    ],
)
def test_marker_text_is_flagged(text: str) -> None:
    assert looks_like_anti_automation(text) is True


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "Sorry, I can't help with that request.",  # a genuine model refusal
        "The side panel button was not found in the DOM",  # selector drift
    ],
)
def test_non_marker_text_is_not_flagged(text: str | None) -> None:
    assert looks_like_anti_automation(text) is False


def test_classify_error_kind() -> None:
    assert classify_error_kind(AntiAutomationDetected("blocked")) == BOT_DETECTED
    assert classify_error_kind(SidePanelUnavailable("no button")) == SELECTOR_DRIFT
    assert classify_error_kind(RuntimeError("boom")) == ATTACK_ERROR
    assert classify_error_kind(ValueError("bad")) == ATTACK_ERROR


def test_error_kind_attribute_on_exceptions() -> None:
    """The orchestrator reads error_kind via getattr (no adapters import)."""
    assert getattr(AntiAutomationDetected("x"), "error_kind", None) == BOT_DETECTED
    assert getattr(SidePanelUnavailable("x"), "error_kind", None) == SELECTOR_DRIFT
    assert getattr(RuntimeError("x"), "error_kind", "attack_error") == "attack_error"
