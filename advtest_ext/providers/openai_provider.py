"""OpenAI provider stub (paid fallback)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from advtest_ext.providers.base import (
    ChatMessage,
    ChatResponse,
    Provider,
)


class OpenAIProvider(Provider):
    expected_provider_type = "openai"

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> ChatResponse:
        if tools is not None:
            raise NotImplementedError(
                "OpenAIProvider does not yet support tool use; "
                "use a local_vllm/Ollama backend for tool-using runs."
            )
        raise NotImplementedError("OpenAIProvider.complete is a stub")
