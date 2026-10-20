"""Attackers: turn an assembled prompt into ``(analysis, injection)``.

:class:`ProviderAttacker` wraps any fission
:class:`~advtest_ext.providers.base.Provider` (point it at the
``CLAUDE_ATTACKER`` / ``CLAUDE_PLANNER`` role). :class:`ScriptedAttacker`
replays canned responses for tests and offline smoke runs — no provider,
no network.
"""

from __future__ import annotations

from typing import Protocol

from advtest_ext.providers.base import ChatMessage, Provider
from advtest_ext.strategy.promptbuild import parse_attempt


class Attacker(Protocol):
    async def propose(self, *, system: str, user: str) -> tuple[str, str]: ...


class ScriptedAttacker:
    """Replays a fixed list of attacker responses (each must contain an
    ``<injection>`` block). The last entry repeats if the loop asks for
    more iters than scripts provided."""

    def __init__(self, scripts: list[str]) -> None:
        if not scripts:
            raise ValueError("ScriptedAttacker needs at least one script")
        self._scripts = list(scripts)
        self._i = 0
        self.seen: list[tuple[str, str]] = []  # (system, user) per call, for asserts

    async def propose(self, *, system: str, user: str) -> tuple[str, str]:
        self.seen.append((system, user))
        text = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return parse_attempt(text)


class ProviderAttacker:
    """Drives a real LLM backend. The system prompt pins the
    ``<analysis>`` + ``<injection>`` contract; the user prompt is the
    assembled strategy + sample + history message."""

    def __init__(
        self,
        provider: Provider,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self._provider = provider
        # None => let the provider config's own value stand (the 5.x
        # temperature-drop logic lives in AnthropicProvider).
        self._overrides: dict[str, object] = {}
        if max_tokens is not None:
            self._overrides["max_tokens"] = max_tokens
        if temperature is not None:
            self._overrides["temperature"] = temperature

    async def propose(self, *, system: str, user: str) -> tuple[str, str]:
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
        ]
        # One retry: 5.x models with adaptive thinking occasionally spend the
        # whole token budget on thinking and return empty text. A second call
        # (with the budget nudged up) usually lands a parseable attempt.
        last_text = ""
        for attempt in range(2):
            overrides = dict(self._overrides)
            if attempt == 1:
                overrides["max_tokens"] = max(int(overrides.get("max_tokens", 1024)), 2048) * 2
            resp = await self._provider.complete(messages, **overrides)
            last_text = resp.text
            try:
                return parse_attempt(last_text)
            except ValueError:
                continue
        return parse_attempt(last_text)  # re-raise the ValueError with context
