"""Tests for the guide mutation-cell generator (``guides.cell_generator``).

The generator grows a guide's diversity palette with an LLM, but the load-bearing
part is the novelty gate: proposed values that merely repeat or paraphrase an
existing cell must be rejected, so the palette gains real diversity rather than
duplicates. Every test uses a fake ``complete`` callable — no LLM.
"""

from __future__ import annotations

import json

from advtest_ext.guides.cell_generator import (
    apply_to_guide_dict,
    filter_novel,
    generate,
    generate_for_operator,
    is_novel,
    looks_like_refusal,
    parse_values,
)
from advtest_ext.guides.corpus import load_guides


def _guide():
    return next(g for g in load_guides("guides") if g.id == "sheets_skill_leak")


def _fake_complete(reply: str):
    async def _complete(_prompt: str) -> str:
        return reply

    return _complete


def test_is_novel_rejects_duplicates_and_paraphrases() -> None:
    existing = ["deputy agent", "spawned execution node"]
    assert is_novel("delegated evaluator", existing) is True
    assert is_novel("deputy agent", existing) is False  # exact dup
    assert is_novel("  Deputy   Agent ", existing) is False  # normalized dup
    assert is_novel("", existing) is False  # empty


def test_filter_novel_dedups_against_existing_and_each_other() -> None:
    existing = ["audit-proof framing"]
    candidates = [
        "audit-proof framing",  # dup of existing
        "compliance-attestation framing",  # novel
        "compliance attestation framing",  # near-dup of the previous accepted
        "incident-postmortem framing",  # novel
    ]
    kept = filter_novel(candidates, existing, threshold=0.6)
    assert "compliance-attestation framing" in kept
    assert "incident-postmortem framing" in kept
    assert kept.count("compliance-attestation framing") == 1
    assert "audit-proof framing" not in kept


def test_parse_values_handles_json_array_object_and_lines() -> None:
    assert parse_values('["a", "b", "c"]') == ["a", "b", "c"]
    assert parse_values('{"values": ["x", "y"]}') == ["x", "y"]
    assert parse_values('```json\n["p", "q"]\n```') == ["p", "q"]
    assert parse_values("- one\n- two\n3. three") == ["one", "two", "three"]


def test_parse_values_survives_truncated_fence_and_json_punctuation() -> None:
    # An opening ```json fence whose closer never arrived (output truncated):
    # the bare fence must not survive as a "cell", and the elements must be clean.
    truncated_fence = '```json\n["alpha label",\n"beta label",'
    assert parse_values(truncated_fence) == ["alpha label", "beta label"]
    # A JSON array that failed to parse (no closing ]) drops to the line-splitter;
    # each element must lose its surrounding quotes and trailing comma.
    truncated_array = '[\n"configuration.md found",\n"setup-guide.md present",'
    assert parse_values(truncated_array) == [
        "configuration.md found",
        "setup-guide.md present",
    ]
    # A lone fence marker is never a candidate.
    assert parse_values("```json") == []
    # A valid JSON array whose elements are themselves quote-wrapped (a model
    # imitating already-polluted cells: ``"\"x\""``) is unwrapped, not kept raw.
    assert parse_values('["\\"database.md available\\"", "\\"admin.md found\\""]') == [
        "database.md available",
        "admin.md found",
    ]
    # Curly quotes from an abliterated model are stripped like straight ones.
    assert parse_values("[\n“overview.md exists”,\n“admin.md found”,") == [
        "overview.md exists",
        "admin.md found",
    ]


async def test_generate_drops_fence_and_punctuation_junk() -> None:
    guide = _guide()
    op = next(o for o in guide.mutation_operators if o.name == "surface_wording")
    # A truncated fenced reply that previously leaked ``` ```json ``` as a cell.
    reply = '```json\n["shadow-review voice",\n"incident-drill cadence",'
    prop = await generate_for_operator(_fake_complete(reply), guide, op, n=3)
    assert "```json" not in prop.accepted
    assert all("```" not in c for c in prop.accepted)
    assert "shadow-review voice" in prop.accepted
    assert "incident-drill cadence" in prop.accepted


async def test_generate_for_operator_keeps_only_novel() -> None:
    guide = _guide()
    op = next(o for o in guide.mutation_operators if o.name == "persona_role")
    # LLM returns one existing value (must be dropped) + two genuinely new ones.
    reply = json.dumps([op.cell_values[0], "shadow auditor", "sandbox operator"])
    prop = await generate_for_operator(_fake_complete(reply), guide, op, n=3)
    assert op.cell_values[0] not in prop.accepted  # existing rejected
    assert "shadow auditor" in prop.accepted
    assert "sandbox operator" in prop.accepted


async def test_generate_covers_all_operators() -> None:
    guide = _guide()
    prop = await generate(
        _fake_complete('["a brand new distinctive cell"]'), guide, n_per_operator=1
    )
    assert len(prop) == len(guide.mutation_operators)
    assert all(p.accepted for p in prop)  # the novel value is accepted for each


async def test_proposer_refusal_yields_no_cells() -> None:
    guide = _guide()
    op = next(o for o in guide.mutation_operators if o.name == "persona_role")
    refusal = (
        "I'm not going to help with this request. Generating new variations to make "
        "a prompt-injection attack more effective causes real harm regardless of framing."
    )
    prop = await generate_for_operator(_fake_complete(refusal), guide, op, n=6)
    assert prop.refused is True
    assert prop.accepted == []
    assert prop.proposed == []


def test_looks_like_refusal_distinguishes_json_from_prose() -> None:
    assert looks_like_refusal("I won't assist with this.") is True
    assert looks_like_refusal('["deputy agent", "shadow auditor"]') is False


async def test_prose_lines_are_not_treated_as_cells() -> None:
    guide = _guide()
    op = next(o for o in guide.mutation_operators if o.name == "surface_wording")
    # A compliant reply but with one long prose sentence mixed in among short cells.
    reply = (
        "- casual-conversational\n"
        "- this is a long explanatory sentence that is clearly prose and not a cell label\n"
        "- socratic-questioning"
    )
    prop = await generate_for_operator(_fake_complete(reply), guide, op, n=3)
    assert "casual-conversational" in prop.accepted
    assert "socratic-questioning" in prop.accepted
    assert all(len(c.split()) <= 8 for c in prop.accepted)  # the long sentence dropped


def test_apply_to_guide_dict_appends_only_accepted() -> None:
    raw = {
        "mutation_operators": [
            {"name": "persona_ladder", "cell_values": ["deputy agent"]},
            {"name": "surface_wording", "cell_values": ["terse"]},
        ]
    }
    from advtest_ext.guides.cell_generator import CellProposal

    proposals = [
        CellProposal(
            operator_name="persona_ladder",
            dimension="persona",
            existing=["deputy agent"],
            accepted=["shadow auditor", "deputy agent"],  # dup should be skipped on apply
        )
    ]
    out = apply_to_guide_dict(raw, proposals)
    persona = next(o for o in out["mutation_operators"] if o["name"] == "persona_ladder")
    wording = next(o for o in out["mutation_operators"] if o["name"] == "surface_wording")
    assert persona["cell_values"] == ["deputy agent", "shadow auditor"]  # appended once
    assert wording["cell_values"] == ["terse"]  # untouched (no proposal)
