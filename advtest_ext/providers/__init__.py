"""LLM provider abstraction.

Single OpenAI-API-compatible surface across:
  - local_vllm           (dev, Qwen3-32B FP8 on a single H200)
  - local_vllm_frontier  (demo, DeepSeek V3.2 on multi-GPU)
  - anthropic            (sanity checks, headline judge)
  - openai               (paid fallback)
  - gemini               (target during Workspace testing)

Cost / GPU-hour accounting hooks live in ``cost.py``; per-run
summaries are emitted at run end.
"""

# Pure infrastructure — no academic citations. Listed empty to keep the
# Prompt-4 aggregator's contract uniform across submodules.
__citations__: list[str] = []

from advtest_ext.providers.base import (
    ChatMessage,
    ChatResponse,
    Provider,
    ProviderConfig,
    ProviderType,
    Usage,
)
from advtest_ext.providers.factory import get_provider, load_provider_from_yaml

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "Provider",
    "ProviderConfig",
    "ProviderType",
    "Usage",
    "get_provider",
    "load_provider_from_yaml",
]
