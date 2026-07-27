"""Anthropic provider implementation backed by the official ``anthropic`` SDK.

Used as the judge / attacker backend when a run config wires
``CLAUDE_JUDGE`` or ``CLAUDE_ATTACKER`` (see ``configs/dev.yaml``).
The SDK is already a hard dep via ``advtest_ext.providers.anthropic_cached``,
so no extras gating is needed here.

This provider expects ``ProviderConfig.api_key_env`` to name an env var
holding the API key (default-reading ``ANTHROPIC_API_KEY`` if the config
doesn't set it). System messages are pulled into Anthropic's first-class
``system`` slot — putting a ``system`` role into ``messages`` is rejected
by the API. Tool use is not yet wired through; passing ``tools=`` raises
so the caller fails loudly rather than silently dropping the schema.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from advtest_ext.providers.base import (
    ChatMessage,
    ChatResponse,
    Provider,
    Usage,
)

_DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"

# Models that reject the ``temperature`` parameter (Anthropic returns
# HTTP 400 "`temperature` is deprecated for this model"). Opus 4.7 is
# the first family to do this; the API expects callers to omit the
# kwarg entirely rather than passing a default. Keep this list narrow
# and exact — silently dropping temperature for a model that DOES
# accept it would mask attacker_temperature=1.0 / judge_temperature=0.0
# tuning intent.
#
# Sonnet 5 is subtler than the Opus entries: it rejects a NON-DEFAULT
# temperature and accepts the default. Our judge roles pin 0.0 (the
# whole point — deterministic-by-rubric scoring), which is non-default,
# so it lands on the reject path and belongs here. Steer judges by
# rubric wording rather than sampling on these models.
_TEMPERATURE_REJECTING_MODELS: frozenset[str] = frozenset(
    {
        "claude-opus-4-7",
        "claude-opus-4-7-1m",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
    }
)


def _model_accepts_temperature(model_id: str) -> bool:
    return model_id not in _TEMPERATURE_REJECTING_MODELS


class AnthropicProvider(Provider):
    """Concrete Anthropic provider over the official ``anthropic`` SDK."""

    expected_provider_type = "anthropic"

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> ChatResponse:
        if tools is not None:
            raise NotImplementedError(
                "AnthropicProvider does not yet support tool use; "
                "use a local_vllm/Ollama backend for tool-using runs."
            )
        # Lazy import: the SDK is a hard dep but lifting it to module
        # scope would couple every Provider import (e.g. the local_vllm
        # path) to anthropic, which a future split could regret.
        from anthropic import AsyncAnthropic

        api_key = self.resolve_api_key(default_env_name=_DEFAULT_API_KEY_ENV)

        system_text = "\n\n".join(m.content for m in messages if m.role == "system") or None
        chat_messages = self._messages_to_api(messages)

        kwargs: dict[str, Any] = {
            "model": self.config.model_id,
            "max_tokens": overrides.get("max_tokens", self.config.max_tokens),
            "messages": chat_messages,
        }
        temperature = overrides.get("temperature", self.config.temperature)
        if temperature is not None and _model_accepts_temperature(self.config.model_id):
            kwargs["temperature"] = temperature
        if system_text is not None:
            kwargs["system"] = system_text

        # ``async with`` so the client's connection pool is closed
        # deterministically. Leaving it to the garbage collector defers
        # teardown to whenever the object is finalised — often after the
        # event loop has already closed, which surfaces as a stack of
        # "Task exception was never retrieved / Event loop is closed"
        # tracebacks that look like a crash but are only cleanup noise.
        async with AsyncAnthropic(api_key=api_key) as client:
            response = await client.messages.create(**kwargs)
        return ChatResponse(
            text=self._extract_text(response),
            usage=self._extract_usage(response),
            provider_type="anthropic",
            model_id=self.config.model_id,
            raw=self._extract_raw(response),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _messages_to_api(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        """Map the orchestrator's ``ChatMessage`` list into Anthropic's
        ``messages`` shape. ``system`` is filtered (lifted to the
        top-level ``system`` arg by the caller); everything else maps
        role→role with the content under a single ``text`` block."""
        out: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                continue
            # The Anthropic API only accepts ``user`` and ``assistant``
            # for the messages list. ``tool`` results would need
            # tool_use/tool_result blocks, which are gated behind the
            # ``tools=`` parameter we already reject above.
            role = "assistant" if message.role == "assistant" else "user"
            out.append({"role": role, "content": message.content})
        return out

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Concatenate any ``text`` blocks in the response. Non-text
        blocks (tool_use, etc.) are ignored — once tool use is wired
        in, the raw blocks land on ``ChatResponse.raw`` for the
        caller to consume."""
        chunks: list[str] = []
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                if text:
                    chunks.append(str(text))
        return "".join(chunks)

    @staticmethod
    def _extract_usage(response: Any) -> Usage:
        meta = getattr(response, "usage", None)
        if meta is None:
            return Usage()
        prompt = int(getattr(meta, "input_tokens", 0) or 0)
        completion = int(getattr(meta, "output_tokens", 0) or 0)
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        )

    @staticmethod
    def _extract_raw(response: Any) -> dict[str, Any]:
        """A small structured snapshot for the trace replay layer.
        Keeps stop_reason (the closest Anthropic equivalent to
        ``finish_reason``) and the per-block type tags so a future
        tool-use wiring can recover the structure without us dumping
        the whole SDK object."""
        block_types = [getattr(b, "type", "") for b in (getattr(response, "content", None) or [])]
        return {
            "stop_reason": getattr(response, "stop_reason", None),
            "block_types": block_types,
        }


__all__ = ["AnthropicProvider"]
