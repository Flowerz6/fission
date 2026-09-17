"""Wiring + pure-surface tests for the new Slides and Calendar adapters.

The live UI-mode invokers and API staging need a browser and real Google
credentials (their selectors are seeded from Docs/Sheets and must be healed
against the live surface), so they are exercised by the opt-in integration
suite, not here. These tests lock the parts that are testable offline: the
target registry wiring, per-target option validation, journey resolution, the
operation-registry entries, and each target's pure capability surface.
"""

from __future__ import annotations

import pytest
import typer

from advtest_ext._cli_factories import _resolve_target
from advtest_ext.adapters.calendar import CalendarTarget
from advtest_ext.adapters.calendar.ui_mode.multi_turn_target import (
    GeminiCalendarMultiTurnTarget,
)
from advtest_ext.adapters.slides import SlidesTarget
from advtest_ext.adapters.slides.ui_mode.multi_turn_target import GeminiSlidesMultiTurnTarget
from advtest_ext.core.builtin_operations import default_operations
from advtest_ext.delivery.journeys import resolve_journey


def test_multiturn_targets_resolve_from_the_registry() -> None:
    assert isinstance(_resolve_target("google_slides_multiturn", None), GeminiSlidesMultiTurnTarget)
    assert isinstance(
        _resolve_target("google_calendar_multiturn", None), GeminiCalendarMultiTurnTarget
    )


def test_multiturn_factories_reject_unknown_options() -> None:
    with pytest.raises(typer.BadParameter, match="google_slides_multiturn"):
        _resolve_target("google_slides_multiturn", {"sheet_id": "x"})
    with pytest.raises(typer.BadParameter, match="google_calendar_multiturn"):
        _resolve_target("google_calendar_multiturn", {"doc_id": "x"})


def test_multiturn_factories_validate_id_list_type() -> None:
    with pytest.raises(typer.BadParameter, match="deck_ids"):
        _resolve_target("google_slides_multiturn", {"deck_ids": "not-a-list"})
    with pytest.raises(typer.BadParameter, match="event_ids"):
        _resolve_target("google_calendar_multiturn", {"event_ids": "not-a-list"})


def test_journeys_resolve_for_new_apps() -> None:
    assert (
        resolve_journey(target_name="", guide_app="google_slides", guide_surface="summarize")
        == "J-SLIDES-SUMMARIZE"
    )
    assert (
        resolve_journey(target_name="", guide_app="google_calendar", guide_surface="side_panel")
        == "J-CALENDAR-ASSIST"
    )
    # Fallback by target-name substring when no guide app/surface is given.
    assert resolve_journey(target_name="google_slides_multiturn") == "J-SLIDES-SUMMARIZE"
    assert resolve_journey(target_name="google_calendar_multiturn") == "J-CALENDAR-ASSIST"


def test_staging_operations_registered() -> None:
    reg = default_operations()
    assert reg.has("slides", "open_deck")
    assert reg.has("slides", "set_speaker_notes")
    assert reg.has("slides", "type_prompt")
    assert reg.has("calendar", "create_event")
    assert reg.has("calendar", "type_prompt")


def test_slides_target_capabilities_are_docs_like() -> None:
    target = SlidesTarget(gemini_provider=object())  # type: ignore[arg-type]
    caps = target.capabilities()
    # Slides carries an image surface (deck media), like Docs.
    assert caps.multimodal is True
    assert target.supported_techniques()  # non-empty technique set


def test_calendar_target_capabilities_are_text_only() -> None:
    target = CalendarTarget(gemini_provider=object())  # type: ignore[arg-type]
    caps = target.capabilities()
    # An event is short text fields: no comments, no rich formatting surface.
    assert caps.comments is False
    assert caps.multimodal is False
    assert target.supported_techniques()  # non-empty technique set
