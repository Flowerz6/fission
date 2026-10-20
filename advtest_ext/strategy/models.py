"""Core data shapes for the strategy loop.

A :class:`Sample` is one (user_task, injection_task) test case — the same
shape PIMiner's benchmark adapters emit (``agentdojo_injection_steps.jsonl``),
kept benchmark-agnostic so a Workspace journey can populate it too. The
loop is keyed only off these fields, never off AgentDojo internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Sample(BaseModel):
    """One IPI test case.

    ``extra="allow"`` so a raw benchmark row (which carries heavy extra
    fields like ``messages``/``prompt_text``) round-trips without a
    bespoke loader — the loop only reads the named fields below.
    """

    model_config = ConfigDict(extra="allow")

    suite_name: str
    """Benchmark suite / Workspace product the case comes from (e.g.
    ``workspace``, ``slack``, or ``google_docs``)."""

    user_task_id: str
    injection_task_id: str

    user_task_prompt: str
    """The benign user request the agent is carrying out (verbatim)."""

    injection_task_goal: str
    """What the injection is trying to make the agent do (verbatim)."""

    context_with_placeholder: str
    """The polluted tool-output / retrieved-content slot the agent reads,
    with ``{INJECTION:<vec_id>}`` marking exactly where the attacker text
    lands."""

    injection_candidates: list[str] = Field(default_factory=list)
    """Vector ids the attacker text may be substituted into."""

    success_markers: list[str] = Field(default_factory=list)
    """Optional: substrings that must appear in the injection for the
    *offline* deterministic reward to count a hit (models "the canonical
    malicious arguments were embedded"). Real targets ignore this."""

    @property
    def sample_key(self) -> str:
        return f"{self.suite_name}/{self.user_task_id}/{self.injection_task_id}"


@dataclass
class RolloutResult:
    """What a target returns for one injection attempt — PIMiner's
    "reward" shape, generalised. ``security`` is the IPI-success signal
    (did the injected objective actually happen); ``utility`` is whether
    the benign user task still completed."""

    utility: bool
    security: bool
    final_output: str = ""
    """Black-box view: the agent's final response text + final tool
    call(s). This is all the attacker sees under the black-box threat
    model."""

    trajectory: str = ""
    """White-box view: the full serialized trajectory (tool calls + tool
    results). Empty under black-box."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Target-specific proof/metadata (e.g. oracle verdict breakdown,
    OOB canary hit, state-diff) — carried through for persistence."""


@dataclass
class Attempt:
    """One attacker iteration and its outcome against the target."""

    iter: int
    analysis: str
    injection: str
    utility: bool
    security: bool
    final_output: str = ""
    trajectory: str = ""
