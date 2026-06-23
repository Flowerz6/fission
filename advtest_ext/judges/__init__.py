"""Judge layer (three-judge ensemble; disagreement-routed HITL).

Default judges (see FOUNDATIONS.md):
  - HarmBench classifier — fast, standard ASR measurement.
  - StrongREJECT — LLM-as-judge with rubric; catches hedged compliance.
  - Llama Guard 3 (default) or ShieldGemma (Google-aligned alt).

Plus :class:`HeuristicJudge` as a CPU-only pre-filter and the
ensemble + HITL queue infrastructure.

Don't trust ASR numbers produced with the heuristic alone — replace
with the LLM-judge ensemble before reporting.
"""

from advtest_ext.judges.base import JudgeBase
from advtest_ext.judges.ensemble import (
    AggregationStrategy,
    EnsembleAsJudge,
    EnsembleResult,
    JudgeEnsemble,
)
from advtest_ext.judges.harmbench import HarmBenchJudge
from advtest_ext.judges.heuristic import HeuristicJudge
from advtest_ext.judges.hitl import HITLItem, HITLQueue
from advtest_ext.judges.llama_guard import LlamaGuardJudge
from advtest_ext.judges.shield_gemma import ShieldGemmaJudge
from advtest_ext.judges.strong_reject import StrongRejectJudge

__citations__: list[str] = [
    "mazeika2024harmbench",
    "souly2024strongreject",
    "llamaguard3_2024",
    "shieldgemma_2024",
]

__all__ = [
    "AggregationStrategy",
    "EnsembleAsJudge",
    "EnsembleResult",
    "HITLItem",
    "HITLQueue",
    "HarmBenchJudge",
    "HeuristicJudge",
    "JudgeBase",
    "JudgeEnsemble",
    "LlamaGuardJudge",
    "ShieldGemmaJudge",
    "StrongRejectJudge",
]
