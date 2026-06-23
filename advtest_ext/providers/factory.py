"""Provider factory and YAML loader.

Top-level YAML shape (see ``configs/dev.yaml`` and ``configs/demo.yaml``):

    ATTACKER_MODEL:
      provider_type: local_vllm
      endpoint: http://localhost:11434/v1
      model_id: qwen2.5:7b-instruct
      max_tokens: 1024
      temperature: 0.7
    JUDGE_MODEL:
      ...
    TARGET_MODEL_DEV:
      ...

Use :func:`load_provider_from_yaml` to materialise one role at a time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from advtest_ext.providers.anthropic import AnthropicProvider
from advtest_ext.providers.base import Provider, ProviderConfig, ProviderType
from advtest_ext.providers.gemini import GeminiProvider
from advtest_ext.providers.local_vllm import (
    LocalVllmFrontierProvider,
    LocalVllmProvider,
)
from advtest_ext.providers.openai_provider import OpenAIProvider

_REGISTRY: dict[ProviderType, type[Provider]] = {
    "local_vllm": LocalVllmProvider,
    "local_vllm_frontier": LocalVllmFrontierProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}


def get_provider(config: ProviderConfig) -> Provider:
    """Construct the concrete Provider for ``config.provider_type``."""
    try:
        cls = _REGISTRY[config.provider_type]
    except KeyError as exc:
        raise ValueError(f"unknown provider_type: {config.provider_type!r}") from exc
    return cls(config)


def load_provider_from_yaml(path: str | Path, role: str) -> Provider:
    """Load ``role`` (e.g. ``ATTACKER_MODEL``) from a YAML config file."""
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict) or role not in data:
        raise KeyError(f"role {role!r} not found in {path}")
    raw: dict[str, Any] = data[role]
    return get_provider(ProviderConfig(**raw))
