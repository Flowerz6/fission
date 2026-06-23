"""Gemini provider implementation backed by ``google-genai``.

Used as the target backend for the Workspace adapters
(:class:`GoogleDocsTarget` / :class:`GmailTarget` /
:class:`SheetsTarget`) — the "AI side panel" leg of an indirect-
prompt-injection round-trip. The SDK is an optional extra
(``poetry install --extras gemini``) because not every advtest run
needs a paid backend.

This provider expects ``ProviderConfig.api_key_env`` to name an env
var holding the API key (default-reading ``GEMINI_API_KEY`` if the
config doesn't set it). Tool use is not yet wired through — when
the Workspace adapters need agent-shaped invocation we'll layer
that on; until then a ``tools=`` kwarg raises so the caller fails
loudly rather than silently dropping the schema.
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

_DEFAULT_API_KEY_ENV = "GEMINI_API_KEY"


class GeminiProvider(Provider):
    """Concrete Gemini provider over the ``google-genai`` SDK."""

    expected_provider_type = "gemini"

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> ChatResponse:
        if tools is not None:
            raise NotImplementedError(
                "GeminiProvider does not yet support tool use; "
                "use a local_vllm/Ollama backend for tool-using runs."
            )
        # Lazy import keeps the module importable without the
        # ``gemini`` extra installed (e.g. unit tests that never
        # actually call .complete still pass).
        from google import genai
        from google.genai import types as genai_types

        api_key = self.resolve_api_key(default_env_name=_DEFAULT_API_KEY_ENV)
        client = genai.Client(api_key=api_key)
        contents = self._messages_to_contents(messages, genai_types)
        # Pull system prompts out and pass via ``system_instruction``
        # (Gemini's first-class slot for them) — putting a system
        # role into the contents list confuses the SDK, which doesn't
        # know that role.
        system_instruction = "\n\n".join(m.content for m in messages if m.role == "system") or None

        config_kwargs: dict[str, Any] = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        max_tokens = overrides.get("max_tokens", self.config.max_tokens)
        if max_tokens is not None:
            config_kwargs["max_output_tokens"] = max_tokens
        temperature = overrides.get("temperature", self.config.temperature)
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        # ``seed`` is honoured by the SDK when supplied.
        if "seed" in overrides:
            config_kwargs["seed"] = overrides["seed"]

        config: Any = genai_types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        # ``client.models.generate_content`` is sync; ``aio`` is the
        # async sibling on modern google-genai. Prefer the async
        # path so we don't block the event loop on the network call.
        response = await client.aio.models.generate_content(
            model=self.config.model_id,
            contents=contents,
            config=config,
        )
        text = self._extract_text(response)
        usage = self._extract_usage(response)
        return ChatResponse(
            text=text,
            usage=usage,
            provider_type="gemini",
            model_id=self.config.model_id,
            raw=self._extract_raw(response),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _messages_to_contents(messages: Sequence[ChatMessage], genai_types: Any) -> list[Any]:
        """Translate the orchestrator's ``ChatMessage`` list into
        Gemini's ``Content`` list. ``system`` messages are pulled
        into the system_instruction slot upstream; user/assistant
        roles map directly (Gemini calls assistant ``model``)."""
        out: list[Any] = []
        for message in messages:
            if message.role == "system":
                continue
            role = "model" if message.role == "assistant" else "user"
            out.append(
                genai_types.Content(
                    role=role,
                    parts=[genai_types.Part.from_text(text=message.content)],
                )
            )
        return out

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Walk the ``candidates`` tree to find the first textual
        part. The SDK exposes ``response.text`` as a convenience
        but it raises when no candidate has text; the tree walk is
        the fallback that survives a finish_reason='SAFETY' response
        with no parts (we want to surface an empty string and let
        the judge see it, rather than crash the run)."""
        try:
            text = response.text
            if text is not None:
                return str(text)
        except (ValueError, AttributeError):
            pass
        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "text", None):
                    return str(part.text)
        return ""

    @staticmethod
    def _extract_usage(response: Any) -> Usage:
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return Usage()
        prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
        completion = int(getattr(meta, "candidates_token_count", 0) or 0)
        total = int(getattr(meta, "total_token_count", prompt + completion) or 0)
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        )

    @staticmethod
    def _extract_raw(response: Any) -> dict[str, Any]:
        """A small structured snapshot for the trace replay layer.
        We don't dump the whole SDK response (it isn't JSON-clean
        across versions) — only the fields that have stable
        meanings."""
        candidates = getattr(response, "candidates", None) or []
        finish_reasons = [str(getattr(c, "finish_reason", "")) for c in candidates]
        return {
            "finish_reasons": finish_reasons,
            "n_candidates": len(candidates),
        }


__all__ = ["GeminiProvider"]
