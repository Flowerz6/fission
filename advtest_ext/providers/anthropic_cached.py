"""advtest_ext/providers/anthropic_cached.py

Anthropic provider with prompt caching wired in. Aggressively caches the
parts of the prompt that repeat across PAIR rounds, MSJ attempts, and
StrongREJECT judgements.

Caching rules (from Anthropic docs, verified May 2026):
- Mark cache breakpoints with cache_control={"type": "ephemeral"}.
- Up to 4 cache breakpoints per request.
- Minimum cacheable block size: 1024 tokens for Sonnet/Opus, 2048 for
  Haiku. Smaller blocks silently fall through to non-cached pricing.
- Cache TTL: 5 minutes default, refreshed on each cache hit.
- Cached input cost: 10% of base input cost (90% savings).
- Cache write cost: 125% of base input cost on first write — so caching
  only pays off when the cached block is reused at least twice.

References:
- https://docs.claude.com/en/docs/build-with-claude/prompt-caching
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message, MessageParam, TextBlockParam
from structlog import get_logger

logger = get_logger(__name__)

# ---- Pricing (per million tokens, USD) ---------------------------------
# Verified May 2026. Update if Anthropic changes pricing.
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,  # 1.25x base input
        "cache_read": 0.10,  # 0.10x base input (90% off)
        "min_cache_tokens": 2048,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
        "min_cache_tokens": 1024,
    },
    "claude-opus-4-7": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,
        "cache_read": 0.50,
        "min_cache_tokens": 1024,
    },
}


@dataclass
class CallCost:
    """Cost breakdown for a single API call."""

    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    total_usd: float
    savings_vs_uncached_usd: float = 0.0


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> CallCost:
    """Compute USD cost for a call. All token counts in actual tokens."""
    if model not in PRICING:
        raise ValueError(f"Unknown model {model!r}; update PRICING table.")
    p = PRICING[model]

    total = (
        input_tokens * p["input"] / 1_000_000
        + output_tokens * p["output"] / 1_000_000
        + cache_creation_tokens * p["cache_write"] / 1_000_000
        + cache_read_tokens * p["cache_read"] / 1_000_000
    )

    # Compute savings vs the no-cache counterfactual.
    no_cache_input_total = (
        (input_tokens + cache_creation_tokens + cache_read_tokens) * p["input"] / 1_000_000
    )
    actual_input_total = total - (output_tokens * p["output"] / 1_000_000)
    savings = max(0.0, no_cache_input_total - actual_input_total)

    return CallCost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        total_usd=total,
        savings_vs_uncached_usd=savings,
    )


# ---- Prompt builders with cache breakpoints ----------------------------


def build_pair_attacker_messages(
    *,
    pair_system_prompt: str,
    behavior_description: str,
    history: list[dict[str, str]],
    latest_judge_feedback: str,
) -> tuple[list[TextBlockParam], list[MessageParam]]:
    """Build messages for one PAIR attacker round with caching.

    Cache structure (most stable -> least stable, per Anthropic guidance):

    1. SYSTEM: pair_system_prompt - identical across every PAIR run.
       Cached.
    2. SYSTEM: behavior_description - identical across all rounds for one
       behavior. Cached separately so we can swap behaviors without
       invalidating breakpoint 1.
    3. USER: history of previous rounds - grows monotonically; cache the
       prefix.
    4. USER: latest_judge_feedback - the new turn. Not cached.

    Returns (system_blocks, messages) ready for the SDK.
    """
    system_blocks: list[TextBlockParam] = [
        {
            "type": "text",
            "text": pair_system_prompt,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"Target behavior:\n{behavior_description}",
            "cache_control": {"type": "ephemeral"},
        },
    ]

    if history:
        history_text = "\n\n".join(
            f"[Round {i + 1}]\nAttacker: {h['attacker']}\n"
            f"Target: {h['target']}\nJudge score: {h['judge_score']}"
            for i, h in enumerate(history)
        )
        messages: list[MessageParam] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Conversation so far:\n{history_text}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Latest judge feedback: {latest_judge_feedback}\n\n"
                            "Propose your next refined attack prompt."
                        ),
                    },
                ],
            }
        ]
    else:
        messages = [
            {
                "role": "user",
                "content": ("Propose your first attack prompt for the target behavior."),
            }
        ]

    return system_blocks, messages


def build_msj_messages(
    *,
    demonstrations_block: str,
    target_query: str,
) -> tuple[list[TextBlockParam], list[MessageParam]]:
    """Build messages for one MSJ attempt with caching.

    The 256-demonstration block is the cost driver; identical across every
    variation that targets the same behavior. Cache it.
    """
    system_blocks: list[TextBlockParam] = [
        {"type": "text", "text": "You are a helpful assistant."},
    ]

    messages: list[MessageParam] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": demonstrations_block,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": target_query},
            ],
        }
    ]

    return system_blocks, messages


def build_strong_reject_messages(
    *,
    rubric: str,
    behavior: str,
    response_to_judge: str,
) -> tuple[list[TextBlockParam], list[MessageParam]]:
    """Build messages for StrongREJECT judging with caching.

    Rubric is identical across every judgement. Cache it.
    """
    system_blocks: list[TextBlockParam] = [
        {
            "type": "text",
            "text": rubric,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    messages: list[MessageParam] = [
        {
            "role": "user",
            "content": (
                f"Behavior under test:\n{behavior}\n\n"
                f"Response to evaluate:\n{response_to_judge}\n\n"
                "Apply the rubric and produce a JSON verdict."
            ),
        }
    ]

    return system_blocks, messages


# ---- Provider client ----------------------------------------------------


class CachedAnthropicProvider:
    """Thin wrapper around AsyncAnthropic that tracks cache hits and cost.

    Use one instance per logical workflow (PAIR attacker, judge, MSJ) so
    you can attribute cost cleanly.
    """

    def __init__(
        self,
        *,
        model: str = "claude-haiku-4-5",
        api_key: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        if model not in PRICING:
            raise ValueError(f"Unknown model {model!r}; update PRICING table.")
        self.model = model
        self.max_tokens = max_tokens
        self._client = AsyncAnthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        self._cum_cost_usd = 0.0
        self._cum_savings_usd = 0.0
        self._call_count = 0

    async def call(
        self,
        system: list[TextBlockParam],
        messages: list[MessageParam],
        **kwargs: Any,
    ) -> tuple[Message, CallCost]:
        """Make a call, return (message, cost). Cost is logged."""
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=kwargs.pop("max_tokens", self.max_tokens),
            system=system,
            messages=messages,
            **kwargs,
        )
        usage = response.usage
        cost = compute_cost(
            model=self.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

        self._cum_cost_usd += cost.total_usd
        self._cum_savings_usd += cost.savings_vs_uncached_usd
        self._call_count += 1

        logger.info(
            "anthropic.call",
            model=self.model,
            input_tokens=cost.input_tokens,
            output_tokens=cost.output_tokens,
            cache_write=cost.cache_creation_tokens,
            cache_read=cost.cache_read_tokens,
            usd=round(cost.total_usd, 6),
            saved_usd=round(cost.savings_vs_uncached_usd, 6),
        )

        return response, cost

    @property
    def total_cost_usd(self) -> float:
        return self._cum_cost_usd

    @property
    def total_savings_usd(self) -> float:
        return self._cum_savings_usd

    def cost_summary(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "calls": self._call_count,
            "total_usd": round(self._cum_cost_usd, 4),
            "saved_by_caching_usd": round(self._cum_savings_usd, 4),
            "effective_discount_pct": round(
                100 * self._cum_savings_usd / max(self._cum_cost_usd + self._cum_savings_usd, 1e-9),
                1,
            ),
        }
