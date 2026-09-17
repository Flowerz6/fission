"""Tests for the Phase-1 Recipe / Step / Surface / Seed data
models. Pure dataclass tests — no DB, no orchestrator."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from advtest_ext.core.recipe import MutationSurface, Recipe, Step
from advtest_ext.core.seed import Seed
from advtest_ext.core.surface import Surface

# ----------------------------------------------------------------------
# Surface
# ----------------------------------------------------------------------


def test_surface_construct_with_required_fields() -> None:
    s = Surface(app="docs", region="sidebar", account_role="recipient")
    assert s.app == "docs"
    assert s.region == "sidebar"
    assert s.account_role == "recipient"
    assert s.document_id is None


def test_surface_rejects_unknown_app() -> None:
    with pytest.raises(ValidationError):
        Surface(app="not_a_real_app", region="sidebar", account_role="recipient")  # type: ignore[arg-type]


def test_surface_rejects_unknown_region() -> None:
    with pytest.raises(ValidationError):
        Surface(app="docs", region="some_made_up_region", account_role="recipient")  # type: ignore[arg-type]


def test_surface_rejects_unknown_account_role() -> None:
    with pytest.raises(ValidationError):
        Surface(app="docs", region="sidebar", account_role="evil_overlord")  # type: ignore[arg-type]


def test_surface_str_repr_without_document_id() -> None:
    s = Surface(app="gmail", region="compose", account_role="attacker_sender")
    assert str(s) == "gmail/compose@attacker_sender"


def test_surface_str_repr_with_document_id() -> None:
    s = Surface(
        app="docs",
        region="sidebar",
        account_role="recipient",
        document_id="D1",
    )
    assert str(s) == "docs/sidebar@recipient#D1"


def test_surface_is_frozen() -> None:
    """Frozen so Surfaces can be used as dict keys / set members
    without worrying about mutation breaking the hash."""
    s = Surface(app="docs", region="sidebar", account_role="recipient")
    with pytest.raises(ValidationError):
        s.app = "gmail"


def test_surface_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Surface(  # type: ignore[call-arg]
            app="docs",
            region="sidebar",
            account_role="recipient",
            typo_field="oops",
        )


# ----------------------------------------------------------------------
# Step + MutationSurface
# ----------------------------------------------------------------------


def _surface_docs() -> Surface:
    return Surface(app="docs", region="sidebar", account_role="recipient")


def test_step_minimal_construction() -> None:
    step = Step(
        id="open_doc",
        intent="open the doc to host the sidebar conversation",
        surface=_surface_docs(),
        operation="open_document",
    )
    assert step.id == "open_doc"
    assert step.params == {}
    assert step.success_signals == []
    assert step.on_fail == "abort"


def test_step_with_params_and_signals() -> None:
    step = Step(
        id="type_prompt",
        intent="ask gemini to summarise",
        surface=_surface_docs(),
        operation="type_prompt",
        params={"prompt": "please summarise this document"},
        success_signals=["bazinga_appears", "tool_name_leak"],
        on_fail="retry",
    )
    assert step.params["prompt"] == "please summarise this document"
    assert "bazinga_appears" in step.success_signals
    assert step.on_fail == "retry"


def test_mutation_surface_defaults_to_immutable() -> None:
    """Conservative default — nothing is mutable until the
    operator opts in. Prevents the Explorer from over-paraphrasing
    Recipes whose authors didn't think about variation."""
    ms = MutationSurface()
    assert ms.preserve == []
    assert ms.allowed == []
    assert ms.free_text_fields == []


def test_mutation_surface_allowed_list_shape() -> None:
    ms = MutationSurface(
        preserve=["step.plant_email.intent"],
        allowed=[
            {"step": "plant_email", "fields": ["subject", "body"]},
            {"step": "compound_handshake", "fields": ["prompt"]},
        ],
        free_text_fields=["persona_phrase", "cover_story"],
    )
    assert len(ms.allowed) == 2
    assert ms.allowed[0]["step"] == "plant_email"


# ----------------------------------------------------------------------
# Recipe
# ----------------------------------------------------------------------


def _step(step_id: str = "s1") -> Step:
    return Step(
        id=step_id,
        intent="test step",
        surface=_surface_docs(),
        operation="noop",
    )


def test_recipe_minimal_construction() -> None:
    r = Recipe(
        id="r-1",
        seed_id="s-1",
        name="Test Recipe",
        content_hash="sha256:abc",
    )
    assert r.version == 1
    assert r.rediscovered_count == 0
    assert r.steps == []
    assert r.signals == []
    assert isinstance(r.mutation_surface, MutationSurface)


def test_recipe_atomic_when_single_step() -> None:
    r = Recipe(
        id="r-1",
        seed_id="s-1",
        name="atomic",
        content_hash="x",
        steps=[_step()],
    )
    assert r.is_atomic is True


def test_recipe_multi_step_is_not_atomic() -> None:
    r = Recipe(
        id="r-1",
        seed_id="s-1",
        name="chain",
        content_hash="x",
        steps=[_step("s1"), _step("s2"), _step("s3")],
    )
    assert r.is_atomic is False


def test_recipe_version_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Recipe(
            id="r",
            seed_id="s",
            name="x",
            content_hash="x",
            version=0,
        )


def test_recipe_rediscovered_count_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        Recipe(
            id="r",
            seed_id="s",
            name="x",
            content_hash="x",
            rediscovered_count=-1,
        )


def test_recipe_taxonomy_axes() -> None:
    """Multi-axis tag set — operator picks the dashboard pivot
    axis. Test that arbitrary string-list axes round-trip."""
    r = Recipe(
        id="r",
        seed_id="s",
        name="x",
        content_hash="x",
        taxonomy={
            "attack_class": ["persona_forgery", "indirect_prompt_injection"],
            "carrier_surface": ["inbox_email"],
            "target_surface": ["gemini_docs_sidebar"],
        },
    )
    assert "persona_forgery" in r.taxonomy["attack_class"]
    assert r.taxonomy["carrier_surface"] == ["inbox_email"]


# ----------------------------------------------------------------------
# Seed
# ----------------------------------------------------------------------


def test_seed_human_chat_construction() -> None:
    s = Seed(
        id="seed-1",
        kind="human_chat",
        title="V1 - Subscriber to Engineer",
        source_format="docx_report",
        raw_artefact_path="./seeds/v1.docx",
        taxonomy_hints=["persona_forgery", "indirect_prompt_injection"],
        submitted_by="red_teamer_a",
        payload={"extracted_turns": []},
    )
    assert s.kind == "human_chat"
    assert s.payload["extracted_turns"] == []


def test_seed_dataset_entry_construction() -> None:
    s = Seed(
        id="seed-2",
        kind="dataset_entry",
        title="harmbench:HB-0042",
        source_format="harmbench_jsonl",
        raw_artefact_path="./corpora/harmbench_v2.1.jsonl",
        submitted_by="ingest_job:harmbench_v2.1",
        payload={"corpus_version": "v2.1", "entry_id": "HB-0042"},
    )
    assert s.kind == "dataset_entry"
    assert s.payload["corpus_version"] == "v2.1"


def test_seed_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        Seed(
            id="x",
            kind="something_made_up",  # type: ignore[arg-type]
            title="x",
            source_format="docx_report",
            raw_artefact_path="x",
            submitted_by="x",
        )


def test_seed_rejects_unknown_source_format() -> None:
    with pytest.raises(ValidationError):
        Seed(
            id="x",
            kind="human_chat",
            title="x",
            source_format="not_a_format",  # type: ignore[arg-type]
            raw_artefact_path="x",
            submitted_by="x",
        )


def test_seed_taxonomy_hints_default_to_empty() -> None:
    s = Seed(
        id="x",
        kind="dataset_entry",
        title="x",
        source_format="harmbench_jsonl",
        raw_artefact_path="x",
        submitted_by="x",
    )
    assert s.taxonomy_hints == []
    assert s.payload == {}
