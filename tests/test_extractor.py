"""Tests for the Phase-6 Recipe Extractor.

No real LLM calls — we inject a stub Provider whose ``complete``
returns whatever JSON we want the LLM to have "produced". That
lets us assert the Extractor's parsing, validation, error
handling, and Recipe-construction logic without API access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from advtest_ext.agents.extractor import Extractor, ExtractorError
from advtest_ext.core.builtin_operations import default_operations
from advtest_ext.core.seed import Seed

# ----------------------------------------------------------------------
# Stub provider
# ----------------------------------------------------------------------


@dataclass
class _StubResponse:
    text: str
    provider_type: str = "stub"
    model_id: str = "stub-model"
    usage: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.usage is None:
            self.usage = {}


class _StubProvider:
    """Returns a pre-canned response text. Records the prompts it
    received so tests can assert the prompt shape."""

    name = "stub"
    model_id = "stub-model"
    provider_type = "stub"

    def __init__(self, response_text: str) -> None:
        self._response = response_text
        self.calls: list[list[Any]] = []

    async def complete(self, messages, **kwargs: Any) -> _StubResponse:
        self.calls.append(list(messages))
        return _StubResponse(text=self._response)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _make_seed() -> Seed:
    return Seed(
        id="seed-test-1",
        kind="human_chat",
        title="V1 - Test",
        source_format="docx_report",
        raw_artefact_path="/tmp/test.docx",
        taxonomy_hints=["persona_forgery"],
        submitted_by="ingest_job:test",
        payload={
            "variant_number": 1,
            "variant_title": "Test",
            "sections": {"overview": ["test"]},
            "attachments": [],
        },
    )


def _well_shaped_recipe_response() -> str:
    return json.dumps(
        {
            "name": "V1 - Test extraction",
            "taxonomy": {"attack_class": ["persona_forgery"]},
            "steps": [
                {
                    "id": "open_doc",
                    "intent": "open the carrier doc",
                    "surface": {
                        "app": "docs",
                        "region": "main",
                        "account_role": "recipient",
                    },
                    "operation": "open_document",
                    "params": {"title": "Test Doc"},
                    "success_signals": [],
                    "on_fail": "abort",
                },
                {
                    "id": "ask",
                    "intent": "type a prompt",
                    "surface": {
                        "app": "docs",
                        "region": "sidebar",
                        "account_role": "recipient",
                    },
                    "operation": "type_prompt",
                    "params": {
                        "document_id": "{{step.open_doc.document_id}}",
                        "prompt": "summarise",
                    },
                    "success_signals": [],
                    "on_fail": "abort",
                },
            ],
            "signals": [
                {
                    "kind": "token_appears",
                    "name": "test_token",
                    "token": "engineer",
                    "where": "full_response",
                },
            ],
            "mutation_surface": {
                "preserve": [],
                "allowed": [{"step": "ask", "fields": ["prompt"]}],
                "free_text_fields": [],
            },
        }
    )


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extractor_parses_well_shaped_response() -> None:
    provider = _StubProvider(_well_shaped_recipe_response())
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    seed = _make_seed()
    recipe = await extractor.extract(seed)

    assert recipe.seed_id == seed.id
    assert recipe.name == "V1 - Test extraction"
    assert len(recipe.steps) == 2
    assert recipe.steps[0].id == "open_doc"
    assert recipe.steps[1].operation == "type_prompt"
    assert recipe.signals[0]["name"] == "test_token"
    assert recipe.content_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_extractor_assigns_fresh_id_per_recipe() -> None:
    provider = _StubProvider(_well_shaped_recipe_response())
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    seed = _make_seed()
    r1 = await extractor.extract(seed)
    r2 = await extractor.extract(seed)
    assert r1.id != r2.id
    # But content_hash is stable for identical input.
    assert r1.content_hash == r2.content_hash


@pytest.mark.asyncio
async def test_extractor_tolerates_markdown_fence() -> None:
    """LLMs often wrap JSON in ```json...``` fences despite our
    instructions. The Extractor should tolerate that."""
    fenced = f"Here is the recipe:\n```json\n{_well_shaped_recipe_response()}\n```\nDone."
    provider = _StubProvider(fenced)
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    recipe = await extractor.extract(_make_seed())
    assert recipe.name == "V1 - Test extraction"


# ----------------------------------------------------------------------
# Error paths
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extractor_raises_on_non_json_response() -> None:
    provider = _StubProvider("not json, just prose about Recipes")
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    with pytest.raises(ExtractorError, match="could not extract"):
        await extractor.extract(_make_seed())


@pytest.mark.asyncio
async def test_extractor_raises_on_missing_name_field() -> None:
    bad = json.dumps({"steps": []})  # no name
    provider = _StubProvider(bad)
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    with pytest.raises(ExtractorError, match="name"):
        await extractor.extract(_make_seed())


@pytest.mark.asyncio
async def test_extractor_raises_on_missing_steps_field() -> None:
    bad = json.dumps({"name": "x"})  # no steps
    provider = _StubProvider(bad)
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    with pytest.raises(ExtractorError, match="steps"):
        await extractor.extract(_make_seed())


@pytest.mark.asyncio
async def test_extractor_raises_on_malformed_step_surface() -> None:
    bad = json.dumps(
        {
            "name": "x",
            "steps": [
                {
                    "id": "s1",
                    "surface": {"app": "made_up_app", "region": "x", "account_role": "x"},
                    "operation": "type_prompt",
                    "params": {},
                },
            ],
        }
    )
    provider = _StubProvider(bad)
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    with pytest.raises(ExtractorError):
        await extractor.extract(_make_seed())


@pytest.mark.asyncio
async def test_extractor_attaches_raw_response_to_error() -> None:
    provider = _StubProvider("garbage")
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    with pytest.raises(ExtractorError) as exc_info:
        await extractor.extract(_make_seed())
    assert exc_info.value.raw_response == "garbage"


# ----------------------------------------------------------------------
# Prompt construction
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extractor_includes_seed_payload_in_prompt() -> None:
    provider = _StubProvider(_well_shaped_recipe_response())
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    seed = _make_seed()
    await extractor.extract(seed)
    sent_message = provider.calls[0][0].content
    assert seed.id in sent_message
    assert seed.title in sent_message


@pytest.mark.asyncio
async def test_extractor_lists_available_operations_in_prompt() -> None:
    """Operators should be able to inspect the prompt and see what
    operations the Extractor was told about — useful for
    debugging Recipes that name nonexistent operations."""
    provider = _StubProvider(_well_shaped_recipe_response())
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    await extractor.extract(_make_seed())
    sent_message = provider.calls[0][0].content
    # The four bundled operations should all appear.
    assert "(gmail, send_email)" in sent_message
    assert "(gmail, reply)" in sent_message
    assert "(docs, open_document)" in sent_message
    assert "(docs, type_prompt)" in sent_message


@pytest.mark.asyncio
async def test_extractor_lists_signal_kinds_in_prompt() -> None:
    provider = _StubProvider(_well_shaped_recipe_response())
    extractor = Extractor(provider=provider, operations=default_operations())  # type: ignore[arg-type]
    await extractor.extract(_make_seed())
    sent_message = provider.calls[0][0].content
    # Operator-facing signal kinds (not LlmJudgeVerdict, which is
    # registry-internal).
    for kind in ("token_appears", "regex_match", "canary_appears", "http_response_shape"):
        assert kind in sent_message
