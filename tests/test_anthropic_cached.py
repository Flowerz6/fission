"""Smoke tests for the cached Anthropic provider — no real API calls."""

from advtest_ext.providers.anthropic_cached import (
    PRICING,
    build_msj_messages,
    build_pair_attacker_messages,
    build_strong_reject_messages,
    compute_cost,
)


def test_cost_haiku_no_cache():
    cost = compute_cost("claude-haiku-4-5", input_tokens=1000, output_tokens=500)
    # 1000 * $1/1M + 500 * $5/1M = $0.001 + $0.0025 = $0.0035
    assert abs(cost.total_usd - 0.0035) < 1e-9
    assert cost.savings_vs_uncached_usd == 0.0


def test_cost_haiku_with_cache_read_saves_money():
    no_cache = compute_cost("claude-haiku-4-5", input_tokens=10_000, output_tokens=500)
    cached = compute_cost(
        "claude-haiku-4-5",
        input_tokens=200,
        output_tokens=500,
        cache_read_tokens=9800,
    )
    assert cached.total_usd < no_cache.total_usd
    assert cached.savings_vs_uncached_usd > 0


def test_pair_messages_have_cache_breakpoints():
    system, messages = build_pair_attacker_messages(
        pair_system_prompt="You are a red-team researcher.",
        behavior_description="Test behavior.",
        history=[],
        latest_judge_feedback="None.",
    )
    assert len(system) == 2
    assert all("cache_control" in block for block in system)
    assert len(messages) == 1


def test_msj_messages_cache_demonstrations_not_query():
    _system, messages = build_msj_messages(
        demonstrations_block="A" * 1000,
        target_query="real query",
    )
    user_content = messages[0]["content"]
    assert any("cache_control" in c for c in user_content)
    assert any("cache_control" not in c for c in user_content)


def test_strong_reject_caches_rubric():
    system, _ = build_strong_reject_messages(
        rubric="rubric text",
        behavior="behavior",
        response_to_judge="response",
    )
    assert system[0].get("cache_control") == {"type": "ephemeral"}


def test_unknown_model_raises():
    import pytest

    with pytest.raises(ValueError):
        compute_cost("claude-mystery-9", input_tokens=10, output_tokens=10)


def test_pricing_table_has_required_keys():
    for _model, p in PRICING.items():
        assert {"input", "output", "cache_write", "cache_read", "min_cache_tokens"} <= p.keys()
