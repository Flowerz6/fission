"""Run names describe configured objectives rather than orchestration labels."""

from types import SimpleNamespace

import pytest

from advtest_ext.dashboard.presentation import run_display_name, run_method_label


def saved_run(**overrides):
    return SimpleNamespace(
        **{
            "target_name": "GeminiDocsMultiTurnTarget",
            "strategy": "grid",
            "guide_id": None,
            "config": {},
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "target,objective,expected",
    [
        ("GeminiDocsMultiTurnTarget", "leak_system_prompt", "Docs · System prompt extraction test"),
        ("GeminiSheetsMultiTurnTarget", "sheets_skill_leak", "Sheets · Skill disclosure test"),
        ("GmailTarget", "gmail_0090911_tool_disclosure", "Gmail · Tool disclosure test"),
        ("GoogleDocsTarget", "docs_indirect_summary_leak", "Docs · Summary injection test"),
    ],
)
def test_objective_names(target, objective, expected):
    run = saved_run(target_name=target, config={"behaviors": [objective]})
    assert run_display_name(run) == expected


def test_attack_method_is_separate_from_grid_orchestration():
    run = saved_run(config={"attacks": ["tap_crescendo"], "behaviors": ["leak_system_prompt"]})
    assert run_method_label(run) == "TAP + Crescendo"
    assert run_display_name(run) == "Docs · System prompt extraction test"


@pytest.mark.parametrize("field", ["display_name", "run_name"])
def test_explicit_name_is_preserved(field):
    assert run_display_name(saved_run(config={field: "My original name"})) == "My original name"


def test_legacy_guide_and_demo_names():
    run = saved_run(target_name="GeminiSheetsSidePanel", guide_id="sheets_skill_leak")
    assert run_display_name(run) == "Sheets · Skill disclosure test"
    run.guide_id = None
    run.strategy = "guide:sheets_skill_leak"
    assert run_display_name(run) == "Sheets · Skill disclosure test"
    assert run_method_label(run) == "Guide replay"
    run.strategy = "demo_flow"
    assert run_display_name(run) == "Gmail → Docs · Hidden instruction test"
    assert run_method_label(run) == "Workflow demo"


def test_multiple_objectives_are_not_reduced_to_a_single_guide():
    run = saved_run(
        target_name="SheetsTarget",
        guide_id="leak_system_prompt",
        config={"behaviors": ["exfil_doc_via_image_url", "fake_meeting_time_in_email_summary"]},
    )
    assert run_display_name(run) == "Sheets · Exfiltration & summary manipulation test"
    run.config["behaviors"].append("leak_system_prompt")
    assert run_display_name(run) == "Sheets · Prompt injection suite (3 objectives)"


def test_unknown_and_missing_metadata_have_honest_fallbacks():
    run = saved_run(config={"behaviors": ["new_objective", "new_objective"]})
    assert run_display_name(run) == "Docs · New objective test"
    run.config["behaviors"].append("another_objective")
    assert run_display_name(run) == "Docs · Security suite (2 objectives)"
    run.config = None
    assert run_display_name(run) == "Docs · Grid test"
    run.strategy = None
    assert run_display_name(run) == "Docs · Security test"
