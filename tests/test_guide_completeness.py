"""Completeness checks for the carrier-provisioning rule."""

from __future__ import annotations

from advtest_ext.guides.completeness import check_guide
from advtest_ext.guides.models import Guide


def _base(**over):
    data = {
        "id": "t",
        "objective": "o",
        "target": {"app": "gmail", "surface": "summarize", "account_role": "recipient"},
        "success": {"signals": [{"kind": "regex_match", "name": "s", "pattern": "x"}], "rubric": "r"},
        "exemplar_turns": ["summarize this"],
        "provenance": {"source": "human:test"},
        "tags": ["indirect-injection"],
    }
    data.update(over)
    return Guide.model_validate(data)


def test_declared_carrier_without_plant_step_is_an_error():
    g = _base(carrier_payload_source="seed_data/x/carrier.txt")
    codes = {i.code: i.level for i in check_guide(g)}
    assert codes.get("carrier_not_provisioned") == "error"


def test_declared_carrier_with_plant_step_is_ok():
    g = _base(
        carrier_payload="<payload>",
        setup_steps=[
            {
                "id": "plant",
                "intent": "plant the email",
                "app": "gmail",
                "account_role": "attacker_sender",
                "operation": "send_email",
                "params": {"to": "v", "subject": "s", "body": "b"},
            }
        ],
    )
    codes = {i.code for i in check_guide(g)}
    assert "carrier_not_provisioned" not in codes


def test_indirect_no_carrier_no_steps_warns():
    g = _base()  # indirect tag, recipient, no carrier, no steps
    codes = {i.code: i.level for i in check_guide(g)}
    assert codes.get("carrier_provisioning_unverified") == "warning"
    assert "carrier_not_provisioned" not in codes  # not a hard error


def test_direct_side_panel_attack_not_flagged():
    g = _base(tags=["persona-forgery"], target={"app": "google_sheets", "surface": "side_panel", "account_role": "self"})
    codes = {i.code for i in check_guide(g)}
    assert "carrier_provisioning_unverified" not in codes
    assert "carrier_not_provisioned" not in codes
