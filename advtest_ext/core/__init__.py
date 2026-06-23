"""Core abstractions: behaviours, payloads, hiding-technique
descriptors, attack traces, judge scores, replay bundles, run configs,
and run results — the narrow set of types that flow through every
level of the orchestrator.

Behaviour taxonomy heritage: zhan2024injecagent (direct-harm vs.
data-exfiltration) extended with Workspace-specific categories drawn
from GeminiJack and Promptware. See FOUNDATIONS.md.
"""

from advtest_ext.core.models import (
    AsrBreakdownRow,
    AttackClass,
    AttackTrace,
    Behavior,
    BehaviorCategory,
    Budget,
    HidingTechnique,
    JudgeScore,
    Payload,
    PayloadModality,
    ReplayBundle,
    RunConfig,
    RunResult,
    Severity,
    SummaryStats,
    Verdict,
    load_seed_behaviors,
)

__citations__: list[str] = [
    "zhan2024injecagent",  # direct-harm vs. data-exfil Behavior taxonomy
    "mazeika2024harmbench",  # HarmBench-aligned ASR / behavior set
    "promptware_nassi2025",  # Workspace-specific behaviours
    "geminijack_2025",  # Workspace-specific behaviours
    "slackai_promptarmor_2024",  # rendering-exfil chain
    "gemini_trifecta_2025",  # additional Workspace evidence
    "greshake2023ipi",  # foundational IPI taxonomy
    "zhang2025asb",  # tool-response-mimicry / spoof-system taxonomy
]

__all__ = [
    "AsrBreakdownRow",
    "AttackClass",
    "AttackTrace",
    "Behavior",
    "BehaviorCategory",
    "Budget",
    "HidingTechnique",
    "JudgeScore",
    "Payload",
    "PayloadModality",
    "ReplayBundle",
    "RunConfig",
    "RunResult",
    "Severity",
    "SummaryStats",
    "Verdict",
    "load_seed_behaviors",
]
