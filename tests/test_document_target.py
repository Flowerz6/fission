"""Tests for ``StubDocumentTarget`` — render preserves hidden markers,
invoke surfaces a compliance signal, and (gpu-marked) realism mode
calls whatever local backend ``configs/dev.yaml`` points at (vLLM,
Ollama, LM Studio — anything OpenAI-API-compatible).

The realism test uses the ``gpu`` and ``integration`` markers; both
are strict + opt-in via the project's pytest config, so day-to-day
test runs skip it cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest

from advtest_ext.document import (
    AbstractDocument,
    Comment,
    HeadingBlock,
    HiddenContent,
    Reply,
    StubDocumentTarget,
    TextBlock,
)
from advtest_ext.providers import load_provider_from_yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _doc_with_hidden(technique_id: str = "zero_width_unicode") -> AbstractDocument:
    return AbstractDocument(
        id="doc-1",
        title="t",
        body_blocks=[
            HeadingBlock(text="Quarterly review", level=1),
            TextBlock(
                text="visible body",
                hidden=HiddenContent(
                    technique_id=technique_id,
                    payload_id="p-1",
                    references=["boucher2022badchars"],
                ),
            ),
        ],
        comments=[
            Comment(
                author="alice",
                anchor="block:b",
                body="comment",
                replies=[
                    Reply(
                        author="bob",
                        body="reply",
                        hidden=HiddenContent(
                            technique_id="comment_injection",
                            payload_id="p-2",
                            references=["promptware_nassi2025"],
                        ),
                    )
                ],
            ),
        ],
    )


def _doc_benign() -> AbstractDocument:
    return AbstractDocument(
        id="doc-clean",
        title="t",
        body_blocks=[TextBlock(text="hello world")],
    )


# ---- render_document --------------------------------------------------


async def test_render_preserves_hidden_markers() -> None:
    target = StubDocumentTarget(seed=0)
    await target.setup()

    ctx = await target.render_document(_doc_with_hidden())
    rendered = ctx.rendered
    assert isinstance(rendered, dict)

    # The hidden marker should be present somewhere in the rendered tree —
    # the stub doesn't strip it; format-specific subclasses are the ones
    # that translate it into a native trick.
    serialised = str(rendered)
    assert "zero_width_unicode" in serialised
    assert "p-1" in serialised
    # Sub-element hidden markers (in comment replies) survive too.
    assert "comment_injection" in serialised


async def test_render_assigns_unique_context_ids() -> None:
    target = StubDocumentTarget(seed=0)
    a = await target.render_document(_doc_with_hidden())
    b = await target.render_document(_doc_with_hidden())
    assert a.context_id != b.context_id


# ---- invoke compliance signal -----------------------------------------


async def test_invoke_complies_when_probability_is_one() -> None:
    target = StubDocumentTarget(compliance_probability=1.0, seed=0)
    await target.setup()
    ctx = await target.render_document(_doc_with_hidden())
    resp = await target.invoke(ctx, "summarize the doc")

    assert resp.complied is True
    # The compliance signal mentions which technique IDs were honoured.
    techniques = resp.raw["hidden_techniques"]
    assert "zero_width_unicode" in techniques
    assert "comment_injection" in techniques
    assert "stub-comply" in resp.text


async def test_invoke_refuses_when_probability_is_zero() -> None:
    target = StubDocumentTarget(compliance_probability=0.0, seed=0)
    await target.setup()
    ctx = await target.render_document(_doc_with_hidden())
    resp = await target.invoke(ctx, "summarize the doc")

    assert resp.complied is False
    # Hidden techniques are still detected even though we declined to act.
    assert resp.raw["hidden_techniques"]
    assert "stub-refuse" in resp.text


async def test_invoke_benign_document_never_complies() -> None:
    """If there's nothing hidden, ``complied`` is unambiguously False
    regardless of ``compliance_probability``."""
    target = StubDocumentTarget(compliance_probability=1.0, seed=0)
    await target.setup()
    ctx = await target.render_document(_doc_benign())
    resp = await target.invoke(ctx, "what does this say?")

    assert resp.complied is False
    assert resp.raw["hidden_techniques"] == []
    assert "stub-benign" in resp.text


async def test_invoke_seed_makes_runs_deterministic() -> None:
    """Probabilistic compliance must be reproducible given a seed."""
    a = StubDocumentTarget(compliance_probability=0.5, seed=42)
    b = StubDocumentTarget(compliance_probability=0.5, seed=42)

    await a.setup()
    await b.setup()

    decisions_a: list[bool | None] = []
    decisions_b: list[bool | None] = []
    for _ in range(20):
        ctx_a = await a.render_document(_doc_with_hidden())
        ctx_b = await b.render_document(_doc_with_hidden())
        decisions_a.append((await a.invoke(ctx_a, "x")).complied)
        decisions_b.append((await b.invoke(ctx_b, "x")).complied)
    assert decisions_a == decisions_b


# ---- capabilities + supported_techniques ------------------------------


def test_capabilities_advertise_full_block_coverage() -> None:
    caps = StubDocumentTarget().capabilities()
    assert {"text", "heading", "table", "image", "text_box", "citation"} <= caps.block_types
    assert caps.formatting and caps.comments and caps.multimodal


def test_supported_techniques_wildcard() -> None:
    assert StubDocumentTarget().supported_techniques() == {"*"}


# ---- realism mode (gpu) -----------------------------------------------


@pytest.mark.gpu
@pytest.mark.integration
async def test_realism_mode_against_dev_backend() -> None:
    """Smoke test: route StubDocumentTarget through whatever
    ``configs/dev.yaml`` points at (TARGET_MODEL_DEV).

    Source-of-truth-wise, we read the dev config rather than hardcoding
    an endpoint here — that way swapping vLLM for Ollama (or vice
    versa) by editing the YAML automatically picks up here.

    Skipped by default (``gpu``/``integration`` markers); run with
    ``pytest -m gpu`` against a host with the dev backend running.
    Assertion is intentionally minimal — non-empty response text and
    the compliance signal alongside it.

    If the user opts in with ``-m gpu`` but the backend isn't
    reachable, we skip rather than fail: ``LocalVllmProvider`` surfaces
    connection failures as ``RuntimeError`` with a recognisable
    message.
    """
    cfg_path = REPO_ROOT / "configs" / "dev.yaml"
    provider = load_provider_from_yaml(cfg_path, "TARGET_MODEL_DEV")
    target = StubDocumentTarget(provider=provider, seed=0)
    await target.setup()

    ctx = await target.render_document(_doc_with_hidden())
    try:
        resp = await target.invoke(ctx, "Briefly summarise the document.")
    except RuntimeError as exc:
        msg = str(exc)
        if "Could not connect" in msg or "timed out" in msg:
            endpoint = getattr(provider, "_endpoint", "the configured endpoint")
            pytest.skip(f"dev backend not reachable at {endpoint}: {exc}")
        raise

    assert isinstance(resp.text, str) and resp.text.strip()
    assert resp.complied in {True, False}
    assert "hidden_techniques" in resp.raw
