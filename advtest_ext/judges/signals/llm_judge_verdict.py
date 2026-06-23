"""``LlmJudgeVerdict`` — wraps an existing
:class:`~advtest_ext.judges.base.JudgeBase` instance as one
signal kind.

The orchestrator uses this to lift every legacy judge
(``StrongRejectJudge``, ``LlamaGuard``, ``HarmBench`` …) into
the signal stack without changing the judge interface. From the
signal layer's perspective the judge call is opaque: it returns
a score; we compare the score to a threshold and that's the
fire/no-fire verdict.

**Back-compat.** Even when wrapped as a signal, the underlying
judge's :class:`JudgeScore` row is still persisted by the
orchestrator so the existing ``/findings`` and trace pages
keep rendering judge scores the way they always have. The
SignalRecord row is *additional* — both surfaces stay populated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from advtest_ext.judges.signals.base import (
    Signal,
    SignalContext,
    SignalResult,
)

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.judges.base import JudgeBase


class LlmJudgeVerdict(Signal):
    """Fires when the wrapped judge's score crosses ``threshold``.

    The judge's full :class:`JudgeScore` (numeric score, judge
    reasoning, judge version) is preserved in the signal's
    ``evidence`` dict so the dashboard can show the same level
    of detail the legacy judge surface shows.
    """

    KIND: ClassVar[str] = "llm_judge_verdict"

    def __init__(
        self,
        *,
        name: str,
        judge: JudgeBase,
        threshold: float = 0.5,
    ) -> None:
        super().__init__(name=name)
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [0, 1], got {threshold}")
        self._judge = judge
        self.threshold = threshold

    async def evaluate(self, *, context: SignalContext) -> SignalResult:
        score = await self._judge.evaluate(
            trace=context.trace,
            behavior=context.behavior,
        )
        fired = bool(score.score >= self.threshold)
        return SignalResult(
            fired=fired,
            reasoning=(
                f"judge {self._judge.name}: "
                f"score={score.score:.3f}, threshold={self.threshold}; "
                f"{score.reasoning}"
            ),
            evidence={
                "judge_name": self._judge.name,
                "judge_version": getattr(score, "judge_version", "unknown"),
                "score": float(score.score),
                "threshold": self.threshold,
                "flagged": bool(getattr(score, "flagged", False)),
                # Preserve the underlying judge's full reasoning so
                # the dashboard can render it under the signal row.
                "judge_reasoning": score.reasoning,
            },
        )

    @property
    def judge(self) -> Any:
        """The wrapped judge instance. Exposed so the orchestrator
        can also write the legacy JudgeScore row alongside the
        SignalRecord row, preserving back-compat with existing
        findings surfaces."""
        return self._judge
