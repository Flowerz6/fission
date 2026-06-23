"""Provider base class and shared models.

All concrete providers expose an OpenAI-Chat-API-compatible surface so
that PyRIT prompt targets (and our adapters) can switch backends by
config alone. This is the same uniform-surface idea PyRIT uses for its
prompt targets — see ``pyrit/prompt_target/`` upstream.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderType = Literal[
    "local_vllm",
    "local_vllm_frontier",
    "anthropic",
    "openai",
    "gemini",
]
"""The five backends advtest supports today. Add a new literal *and* a
factory branch in ``factory.py`` to extend."""

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    """Single chat-completions message (OpenAI-compatible shape)."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str
    name: str | None = None


class Usage(BaseModel):
    """Token accounting returned by a provider call.

    ``cost_usd`` is populated only for paid providers; ``gpu_seconds``
    is an *estimate* recorded for local providers so that cost summaries
    are uniform at run end (see ``cost.py``).
    """

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    gpu_seconds: float = 0.0


class ChatResponse(BaseModel):
    """Result of a single chat completion."""

    model_config = ConfigDict(extra="forbid")

    text: str
    raw: dict[str, Any] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    provider_type: ProviderType
    model_id: str


class ProviderConfig(BaseModel):
    """Backend-agnostic provider config. One per logical role
    (ATTACKER_MODEL / JUDGE_MODEL / TARGET_MODEL[_DEV])."""

    model_config = ConfigDict(extra="forbid")

    provider_type: ProviderType
    endpoint: str | None = None
    """Base URL for OpenAI-compatible servers (vLLM, OpenAI, OpenRouter, ...).
    Required for ``local_vllm`` / ``local_vllm_frontier`` / ``openai``;
    ignored by SDKs that take their own endpoints (anthropic, gemini)."""

    model_id: str
    """The model name passed in the request body
    (e.g. ``Qwen/Qwen3-32B``, ``deepseek-ai/DeepSeek-V3.2``,
    ``claude-sonnet-latest``, ``gpt-4o``, ``gemini-2.0-flash``)."""

    api_key_env: str | None = None
    """Environment variable to read the API key from. Never put secrets
    in YAML. Example: ``ANTHROPIC_API_KEY``."""

    max_tokens: int = 1024
    temperature: float = 0.7
    extra_kwargs: dict[str, Any] = Field(default_factory=dict)
    """Provider-specific tuning passed through to the underlying SDK."""


class Provider(ABC):
    """Abstract LLM backend.

    Concrete subclasses live in this package and are wired up via
    ``factory.get_provider``. Implementations should be I/O-stubbed in
    this scaffold pass; only the shape is fixed here.
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> ChatResponse:
        """Run a chat completion. ``overrides`` may shadow
        ``max_tokens``/``temperature``/etc. from ``self.config``.

        ``tools`` is the OpenAI function-calling tool list — when set,
        providers that support it forward the schema to the model and
        any returned tool calls land in ``ChatResponse.raw`` (under
        ``choices[0].message.tool_calls`` for OpenAI-compatible
        backends). Providers that don't support tool use yet raise
        ``NotImplementedError`` when ``tools`` is non-empty rather
        than silently dropping it.
        """

    @property
    def provider_type(self) -> ProviderType:
        return self.config.provider_type

    @property
    def model_id(self) -> str:
        return self.config.model_id

    def resolve_api_key(self, *, default_env_name: str) -> str:
        """Look up the API key from the env var the config names
        (``ProviderConfig.api_key_env``), falling back to
        ``default_env_name`` when the config doesn't override.

        Raises ``RuntimeError`` with a clean operator-facing message
        when the key is missing, naming the env var the operator
        actually needs to set. Shared across paid providers so the
        error shape stays uniform.
        """
        env_name = self.config.api_key_env or default_env_name
        api_key = os.environ.get(env_name)
        if not api_key:
            raise RuntimeError(
                f"{type(self).__name__} needs {env_name!r} in the environment "
                "(set api_key_env in the provider config to override)"
            )
        return api_key
