"""Completeness checks for the carrier-provisioning rule."""

from __future__ import annotations

from advtest_ext.guides.completeness import check_guide
from advtest_ext.guides.models import Guide


def _base(**over):
    data = {
        "id": "t",
        "objective": "o",
        "target": {"app": "gmail", "surface": "summarize", "account_role": "recipient"},
        "success": {
            "signals": [{"kind": "regex_match", "name": "s", "pattern": "x"}],
            "rubric": "r",
        },
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
    g = _base(
        tags=["persona-forgery"],
        target={"app": "google_sheets", "surface": "side_panel", "account_role": "self"},
    )
    codes = {i.code for i in check_guide(g)}
    assert "carrier_provisioning_unverified" not in codes
    assert "carrier_not_provisioned" not in codes


def test_discovery_relaxes_missing_signal_to_warning():
    data = {
        "id": "d",
        "objective": "o",
        "target": {"app": "google_docs", "surface": "side_panel", "account_role": "self"},
        "success": {"rubric": "judge decides"},  # no deterministic signals
        "exemplar_turns": ["do the thing"],
        "provenance": {"source": "human:ideation"},
        "maturity": "discovery",
    }
    g = Guide.model_validate(data)
    codes = {i.code: i.level for i in check_guide(g)}
    assert codes.get("no_deterministic_signal") == "warning"  # allowed for discovery


def _gold(**success):
    return Guide.model_validate(
        {
            "id": "g",
            "objective": "o",
            "target": {"app": "google_docs", "surface": "side_panel", "account_role": "self"},
            "success": success,
            "exemplar_turns": ["do the thing"],
            "provenance": {"source": "human:bikash"},
            "maturity": "gold",
        }
    )


def test_gold_requires_a_deterministic_signal():
    # rubric-only (valid Pydantic) but no signal -> error for a gold guide
    g = _gold(rubric="judge decides")
    codes = {i.code: i.level for i in check_guide(g)}
    assert codes.get("no_deterministic_signal") == "error"


def test_gold_requires_a_written_rubric():
    # signals-only (valid Pydantic) but no rubric -> error for a gold guide
    g = _gold(signals=[{"kind": "regex_match", "name": "s", "pattern": "x"}])
    codes = {i.code: i.level for i in check_guide(g)}
    assert codes.get("no_success_rubric") == "error"


def _gold_exfil(**success):
    # a 'gold' guide whose declared harm is data exfiltration
    return Guide.model_validate(
        {
            "id": "g",
            "objective": "exfiltrate the user's recent mail",
            "target": {"app": "google_docs", "surface": "side_panel", "account_role": "self"},
            "threat_class": "indirect_prompt_injection, data_exfiltration",
            "success": success,
            "exemplar_turns": ["do the thing"],
            "provenance": {"source": "human:bikash"},
            "maturity": "gold",
        }
    )


def test_gold_exfil_without_effect_anchor_is_an_error():
    # a regex on free text is not proof of a REAL leak for exfil (handbook §8)
    g = _gold_exfil(
        signals=[{"kind": "regex_match", "name": "s", "pattern": "x"}],
        rubric="data left to an attacker host",
    )
    codes = {i.code: i.level for i in check_guide(g)}
    assert codes.get("exfil_without_effect_anchor") == "error"


def test_gold_exfil_with_canary_anchor_is_ok():
    g = _gold_exfil(
        signals=[{"kind": "canary_appears", "name": "c", "canary": "FISSION-CANARY-ABC123"}],
        rubric="data left to an attacker host",
    )
    codes = {i.code for i in check_guide(g)}
    assert "exfil_without_effect_anchor" not in codes


def test_discovery_exfil_without_anchor_is_not_gated():
    # the anchor requirement is a gold-promotion gate only
    data = {
        "id": "g",
        "objective": "exfiltrate the user's recent mail",
        "target": {"app": "google_docs", "surface": "side_panel", "account_role": "self"},
        "threat_class": "data_exfiltration",
        "success": {"signals": [{"kind": "regex_match", "name": "s", "pattern": "x"}], "rubric": "r"},
        "exemplar_turns": ["do the thing"],
        "provenance": {"source": "human:bikash"},
        "maturity": "discovery",
    }
    g = Guide.model_validate(data)
    codes = {i.code for i in check_guide(g)}
    assert "exfil_without_effect_anchor" not in codes
