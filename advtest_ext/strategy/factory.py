"""Build the loop's LLM-backed agents from a fission provider config.

Thin wrappers over :func:`advtest_ext.providers.factory.load_provider_from_yaml`
so a driver can say "attacker = CLAUDE_ATTACKER from configs/dev.yaml"
without touching provider internals. Construction does no network I/O;
the API key is resolved lazily on the first ``complete`` call.
"""

from __future__ import annotations

from pathlib import Path

from advtest_ext.providers.factory import load_provider_from_yaml
from advtest_ext.strategy.attacker import ProviderAttacker
from advtest_ext.strategy.router import ProviderRouter


def attacker_from_config(
    config_path: str | Path,
    role: str = "CLAUDE_ATTACKER",
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> ProviderAttacker:
    provider = load_provider_from_yaml(config_path, role)
    return ProviderAttacker(provider, max_tokens=max_tokens, temperature=temperature)


def router_from_config(
    config_path: str | Path,
    role: str = "CLAUDE_ATTACKER",
    *,
    top_k: int = 3,
    max_tokens: int | None = 256,
    temperature: float | None = None,
) -> ProviderRouter:
    provider = load_provider_from_yaml(config_path, role)
    return ProviderRouter(provider, top_k=top_k, max_tokens=max_tokens, temperature=temperature)
