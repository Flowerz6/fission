"""Judge base class.

Concrete judges score an :class:`AttackTrace` (the response, the
behaviour it was meant to elicit, and the payload that was sent) and
return a :class:`~advtest_ext.core.models.JudgeScore`.

The orchestrator runs a *three-judge ensemble* (FOUNDATIONS.md
"Judge layer") with disagreement-routed HITL. PAIR consumes a single
judge directly via the ``score_response`` convenience method.

Concrete impls in sibling modules:
  - :mod:`heuristic` — fast CPU-only pre-filter (refusal patterns +
    harmful-indicator substrings).
  - :mod:`strong_reject` — Souly 2024 rubric (refused / convincing /
    specific) implemented via an LLM call.
  - :mod:`llama_guard` — Llama Guard 3 via a configurable inference
    endpoint.
  - :mod:`shield_gemma` — Google's ShieldGemma alternative.
  - :mod:`harmbench` — Mazeika 2024 cls model wrapping the
    HarmBench-Llama-2-13b-cls template.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from advtest_ext.core.models import AttackTrace, JudgeScore
from advtest_ext.providers.base import ChatMessage, ChatResponse

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from advtest_ext.core.models import Behavior, Payload
    from advtest_ext.providers.base import Provider


class JudgeBase(ABC):
    """Abstract judge.

    Concrete subclasses set the four class-level metadata attributes
    and implement :meth:`evaluate`. ``references`` may be empty for
    synthesised / heuristic judges; production judges (HarmBench,
    StrongREJECT, Llama Guard 3, ShieldGemma) carry their citation
    keys.
    """

    # Plain class attrs (not ClassVar) so subclasses / wrappers can
    # mutate per-instance — matches the AttackPlugin convention.
    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    references: list[str] = []  # noqa: RUF012 — sentinel

    def __init__(self) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} must set a non-empty `name`")

    @abstractmethod
    async def evaluate(
        self,
        *,
        trace: AttackTrace,
        behavior: Behavior,
    ) -> JudgeScore:
        """Score ``trace.raw_response`` against the elicited
        ``behavior``. Higher scores mean the behaviour was elicited
        more strongly.

        ``behavior`` is passed alongside ``trace`` (rather than
        embedded inside it) to avoid bloating every persisted trace
        with the full Behaviour record — the orchestrator already
        carries the Behaviour catalogue and resolves ``trace.behavior_id``
        at evaluation time.
        """

    async def score_response(
        self,
        *,
        behavior: Behavior,
        payload: Payload,
        response: str,
        target_name: str = "unknown",
    ) -> JudgeScore:
        """Convenience for callers that don't have a full
        :class:`AttackTrace` (e.g. PAIR, ad-hoc judging during
        development). Builds a minimal trace and calls :meth:`evaluate`."""
        trace = AttackTrace(
            id=f"adhoc-{uuid.uuid4().hex}",
            run_id="adhoc",
            behavior_id=behavior.id,
            payload=payload,
            target_name=target_name,
            raw_request=payload.text,
            raw_response=response,
            verdict=None,
        )
        return await self.evaluate(trace=trace, behavior=behavior)


class LlmJudgeBase(JudgeBase):
    """Base for judges that prompt an LLM Provider with a template and
    parse the response.

    HarmBench, Llama Guard, ShieldGemma, and StrongREJECT all share
    the same skeleton: store a Provider + max_tokens + temperature in
    __init__, format a template string in ``evaluate``, ship it via
    ``provider.complete`` as a single user message, then parse the
    response into a ``JudgeScore``. Only the template and the parser
    differ.

    Subclasses set the JudgeBase metadata (``name`` / ``version`` /
    ``description`` / ``references``) and override ``evaluate`` to
    call :meth:`_call_llm` with the formatted prompt and build a
    JudgeScore from the returned text. :meth:`_judge_version` returns
    the standard ``<version>/<model_id>`` string so the persisted
    judge_version stays uniform.
    """

    def __init__(
        self,
        *,
        provider: Provider,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> None:
        """``max_tokens=None`` inherits the provider's configured budget.

        That is the default because the alternative silently ignored
        operator configuration: judges used to carry their own hardcoded
        default (256 for the IPI judges), no factory passed the value
        through, and so raising ``max_tokens`` on the judge role in the
        provider YAML changed nothing at all.

        Harmless while models answered immediately. Not harmless once
        thinking is on by default: thinking and the answer share the
        budget, so a 256-token ceiling was consumed by thinking and the
        reply came back EMPTY — surfacing as an unparseable verdict with
        ``stop_reason='max_tokens'``, several layers away from the cause.
        An explicit integer still wins, for callers that genuinely want
        to cap one judge independently of the role.
        """
        self._provider = provider
        self._max_tokens = max_tokens if max_tokens is not None else provider.config.max_tokens
        self._temperature = temperature
        super().__init__()

    async def _complete(self, prompt: str) -> ChatResponse:
        """Send ``prompt`` as a single user message and return the FULL
        response.

        Judges that only need the text should use :meth:`_call_llm`.
        Reach for this one when a failure has to be explained: an empty
        ``text`` is ambiguous on its own — the model may have been cut
        off mid-answer, declined the request outright, or simply
        returned nothing — and only ``raw['stop_reason']`` tells those
        apart. Diagnosing that from the text alone is guesswork."""
        return await self._provider.complete(
            [ChatMessage(role="user", content=prompt)],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )

    async def _call_llm(self, prompt: str) -> str:
        """Send ``prompt`` to the provider as a single user message and
        return the raw response text. Wraps the
        ``[ChatMessage(role="user", content=prompt)]`` /
        ``provider.complete(max_tokens, temperature)`` boilerplate that
        was identical across every LLM judge."""
        return (await self._complete(prompt)).text

    def _judge_version(self) -> str:
        """``<version>/<model_id>`` string for persisted
        ``JudgeScore.judge_version``. Centralised so the format stays
        uniform across judges."""
        return f"{self.version}/{self._provider.model_id}"


__all__ = ["JudgeBase", "LlmJudgeBase"]
