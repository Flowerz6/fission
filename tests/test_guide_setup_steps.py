"""Tests for a Guide's operational ``setup_steps`` — the out-of-band staging
steps (plant email, open carrier doc, drive side panel) that make a playbook
executable, each bound to a REAL Recipe-Runner operation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from advtest_ext.core.builtin_operations import default_operations
from advtest_ext.guides.corpus import load_guide, load_guides
from advtest_ext.guides.models import (
    Guide,
    GuideProvenance,
    GuideStep,
    GuideSuccess,
    GuideTarget,
    validate_setup_operations,
)

_GUIDES = Path(__file__).resolve().parents[1] / "guides"


def test_v1_guide_has_tool_bound_setup_steps_and_target_workspace() -> None:
    g = load_guide(_GUIDES / "workspace_engineer_persona_forgery.json")
    # Target names the workspace and the account the disclosure lands on.
    assert g.target.account_role == "recipient"
    assert g.target.workspace and "Workspace" in g.target.workspace
    # The staging chain: plant email -> ack -> carrier doc -> drive side panel.
    steps = {s.id: s for s in g.setup_steps}
    assert list(steps) == ["plant_email", "ack_email", "open_carrier_doc", "drive_side_panel"]
    assert (steps["plant_email"].region, steps["plant_email"].operation) == ("gmail", "send_email")
    assert steps["plant_email"].account_role == "attacker_sender"
    assert (steps["ack_email"].region, steps["ack_email"].operation) == ("gmail", "reply")
    assert (steps["open_carrier_doc"].region, steps["open_carrier_doc"].operation) == (
        "docs",
        "open_document",
    )
    assert (steps["drive_side_panel"].region, steps["drive_side_panel"].operation) == (
        "docs",
        "type_prompt",
    )
    # The email step carries the real op's param names (to/subject/body).
    assert {"to", "subject", "body"} <= set(steps["plant_email"].params)
    # Inter-step reference to a prior step's output (Runner substitution shape).
    assert steps["ack_email"].params["thread_id"] == "{{step.plant_email.thread_id}}"


def test_every_shipped_guide_setup_step_is_a_real_registered_operation() -> None:
    registry = default_operations()
    for g in load_guides(_GUIDES):
        # Raises if any step names an operation the Runner can't execute.
        validate_setup_operations(g, registry)


def test_guides_without_setup_steps_default_empty_and_skip_validation() -> None:
    g = load_guide(_GUIDES / "sheets_skill_leak.yaml")
    assert g.setup_steps == []
    validate_setup_operations(g)  # no-op, must not raise or need the registry


def test_unknown_operation_is_rejected() -> None:
    g = Guide(
        id="bogus",
        objective="x",
        target=GuideTarget(app="gmail"),
        success=GuideSuccess(rubric="x"),
        provenance=GuideProvenance(source="test"),
        setup_steps=[
            GuideStep(id="s1", intent="x", app="gmail", operation="not_a_real_operation"),
        ],
    )
    with pytest.raises(ValueError, match="unknown operation"):
        validate_setup_operations(g)
