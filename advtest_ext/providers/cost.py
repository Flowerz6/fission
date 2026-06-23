"""Cost / GPU-hour accounting shared across providers.

Paid providers fill ``Usage.cost_usd`` directly using a small price
table; local providers record ``Usage.gpu_seconds`` from wall-clock
time spanning the call (multiplied by the configured GPU count).

Keep this thin: the goal is a single end-of-run summary, not a billing
system. Numbers are approximate and explicitly labelled as estimates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from advtest_ext.providers.base import ProviderType, Usage

# Per-1k-token USD prices for paid providers. Update in one place when
# rates change — this is intentionally a small dict, not a config.
# Numbers below are placeholders for the scaffold; replace with current
# pricing before any real run.
PAID_PRICES_USD_PER_1K: dict[str, dict[str, float]] = {
    # provider_type:model_id_prefix -> {"input": ..., "output": ...}
    "anthropic:claude-sonnet": {"input": 0.003, "output": 0.015},
    "anthropic:claude-haiku": {"input": 0.0008, "output": 0.004},
    "openai:gpt-4o": {"input": 0.0025, "output": 0.01},
    "openai:gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gemini:gemini-2.0-flash": {"input": 0.0, "output": 0.0},  # check current
}


def estimate_paid_cost_usd(
    provider_type: ProviderType, model_id: str, prompt_tokens: int, completion_tokens: int
) -> float:
    """Best-effort cost estimate from the small price table above."""
    for key, rates in PAID_PRICES_USD_PER_1K.items():
        ptype, prefix = key.split(":", 1)
        if ptype == provider_type and model_id.startswith(prefix):
            return (
                prompt_tokens * rates["input"] / 1000.0
                + completion_tokens * rates["output"] / 1000.0
            )
    return 0.0


@dataclass
class CostLedger:
    """Aggregates per-call ``Usage`` records for end-of-run reporting."""

    total_cost_usd: float = 0.0
    total_gpu_seconds: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    by_provider: dict[ProviderType, Usage] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record(self, provider_type: ProviderType, usage: Usage) -> None:
        with self._lock:
            self.total_cost_usd += usage.cost_usd
            self.total_gpu_seconds += usage.gpu_seconds
            self.total_prompt_tokens += usage.prompt_tokens
            self.total_completion_tokens += usage.completion_tokens
            agg = self.by_provider.setdefault(provider_type, Usage())
            agg.cost_usd += usage.cost_usd
            agg.gpu_seconds += usage.gpu_seconds
            agg.prompt_tokens += usage.prompt_tokens
            agg.completion_tokens += usage.completion_tokens
            agg.total_tokens += usage.total_tokens

    def summary(self) -> str:
        lines = [
            "=== advtest run cost summary ===",
            f"  total cost (paid):       ${self.total_cost_usd:,.4f}",
            f"  total GPU-seconds (est): {self.total_gpu_seconds:,.1f}",
            f"  total prompt tokens:     {self.total_prompt_tokens:,}",
            f"  total completion tokens: {self.total_completion_tokens:,}",
        ]
        for ptype, u in self.by_provider.items():
            lines.append(
                f"    [{ptype}] cost=${u.cost_usd:,.4f} "
                f"gpu_s={u.gpu_seconds:,.1f} "
                f"prompt={u.prompt_tokens:,} completion={u.completion_tokens:,}"
            )
        return "\n".join(lines)
