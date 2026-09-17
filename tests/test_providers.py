"""Provider abstraction tests (no network)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from advtest_ext.providers import (
    ChatMessage,
    Provider,
    ProviderConfig,
    get_provider,
    load_provider_from_yaml,
)
from advtest_ext.providers.anthropic import AnthropicProvider
from advtest_ext.providers.base import Usage
from advtest_ext.providers.cost import CostLedger, estimate_paid_cost_usd
from advtest_ext.providers.gemini import GeminiProvider
from advtest_ext.providers.local_vllm import (
    LocalVllmFrontierProvider,
    LocalVllmProvider,
)
from advtest_ext.providers.openai_provider import OpenAIProvider

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("ptype", "cls"),
    [
        ("local_vllm", LocalVllmProvider),
        ("local_vllm_frontier", LocalVllmFrontierProvider),
        ("anthropic", AnthropicProvider),
        ("openai", OpenAIProvider),
        ("gemini", GeminiProvider),
    ],
)
def test_factory_dispatch(ptype: str, cls: type[Provider]) -> None:
    cfg = ProviderConfig(provider_type=ptype, model_id="x")  # type: ignore[arg-type]
    provider = get_provider(cfg)
    assert isinstance(provider, cls)
    assert provider.provider_type == ptype
    assert provider.model_id == "x"


def test_chat_message_validates() -> None:
    msg = ChatMessage(role="user", content="hi")
    assert msg.role == "user"


async def test_anthropic_complete_requires_api_key(
    vllm_config: ProviderConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an API key in the env, ``AnthropicProvider.complete``
    fails fast with a message naming the env var — better than a
    deep SDK ``AuthenticationError`` from the first network call."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = ProviderConfig(provider_type="anthropic", model_id="claude-sonnet-4-6")
    provider = get_provider(cfg)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        await provider.complete([ChatMessage(role="user", content="hi")])


async def test_anthropic_complete_rejects_tool_use(
    vllm_config: ProviderConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider docs claim no tool_use yet — lock that contract
    with a test so a future stealth-merge of half-finished tool
    plumbing doesn't silently drop the schema."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = ProviderConfig(provider_type="anthropic", model_id="claude-sonnet-4-6")
    provider = get_provider(cfg)
    with pytest.raises(NotImplementedError, match="tool use"):
        await provider.complete(
            [ChatMessage(role="user", content="hi")],
            tools=[{"name": "noop", "input_schema": {"type": "object"}}],
        )


def test_anthropic_messages_to_api_lifts_system_and_drops_role() -> None:
    """``system`` messages must NOT land in the messages list — the
    Anthropic API rejects ``role=system`` there. Pulled into the
    top-level ``system`` arg by the caller, dropped from the list
    by the helper."""
    out = AnthropicProvider._messages_to_api(
        [
            ChatMessage(role="system", content="you are a careful judge"),
            ChatMessage(role="user", content="rate this"),
            ChatMessage(role="assistant", content="ok"),
        ]
    )
    assert [m["role"] for m in out] == ["user", "assistant"]
    assert all("system" not in m["role"] for m in out)


def test_load_dev_yaml_claude_judge_role() -> None:
    """The dev YAML must carry a ``CLAUDE_JUDGE`` role pointing at the
    Anthropic provider. Locks the contract so a future dev.yaml
    refactor doesn't silently break ``StrongRejectJudge`` wiring."""
    p = load_provider_from_yaml(REPO_ROOT / "configs" / "dev.yaml", "CLAUDE_JUDGE")
    assert p.provider_type == "anthropic"
    assert p.model_id.startswith("claude-")
    # Judging wants determinism — anything > 0 here is almost
    # certainly a copy/paste from the attacker config.
    assert p.config.temperature == 0.0


def test_load_dev_yaml() -> None:
    """The dev YAML should load as a local_vllm provider with a
    populated model_id and endpoint. We deliberately don't assert the
    exact model name — the dev backend is whatever is convenient
    locally (vLLM Qwen3-32B, Ollama qwen2.5:7b-instruct, …)."""
    p = load_provider_from_yaml(REPO_ROOT / "configs" / "dev.yaml", "ATTACKER_MODEL")
    assert p.provider_type == "local_vllm"
    assert p.model_id  # any non-empty model id is fine
    assert p.config.endpoint and p.config.endpoint.startswith("http")


def test_load_demo_yaml_target() -> None:
    p = load_provider_from_yaml(REPO_ROOT / "configs" / "demo.yaml", "TARGET_MODEL")
    assert p.provider_type == "gemini"


def test_cost_ledger_aggregates() -> None:
    ledger = CostLedger()
    ledger.record("anthropic", Usage(prompt_tokens=10, completion_tokens=5, cost_usd=0.1))
    ledger.record("local_vllm", Usage(prompt_tokens=20, completion_tokens=10, gpu_seconds=2.5))
    assert ledger.total_cost_usd == pytest.approx(0.1)
    assert ledger.total_gpu_seconds == pytest.approx(2.5)
    assert ledger.total_prompt_tokens == 30
    assert "advtest run cost summary" in ledger.summary()


def test_estimate_paid_cost_known_model() -> None:
    cost = estimate_paid_cost_usd("anthropic", "claude-sonnet-latest", 1000, 500)
    # 1k prompt @ 0.003 + 0.5k completion @ 0.015 = 0.003 + 0.0075
    assert cost == pytest.approx(0.0105)


def test_estimate_paid_cost_unknown_model_returns_zero() -> None:
    assert estimate_paid_cost_usd("openai", "gpt-unobtainium", 1000, 500) == 0.0


def test_anthropic_usage_includes_estimated_cost() -> None:
    provider = AnthropicProvider(
        ProviderConfig(provider_type="anthropic", model_id="claude-sonnet-test")
    )
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100))
    usage = provider._extract_usage(response)
    assert usage.prompt_tokens == 1000
    assert usage.completion_tokens == 100
    assert usage.cost_usd > 0
