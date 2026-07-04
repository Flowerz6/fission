"""The ``Guide`` — a normalized steering doc the LLM attacker consumes.

Heterogeneous source datasets (human transcripts, single prompts, red-team
reports, OSINT threads) are converted *into* this one shape by normalizers
(see :mod:`advtest_ext.guides.normalize`); the attacker never sees raw dataset
variance. Specificity-graded: only ``objective`` / ``target`` / ``success`` are
required; the rest is filled to whatever depth the source provided.

Design notes: `docs/FISSION_GUIDE_FORMAT.md`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GuideSuccess(BaseModel):
    """How you know the attack landed.

    ``signals`` are raw signal-spec dicts — the SAME shape the deterministic
    signal registry consumes (``{"kind": ..., "name": ..., ...kwargs}``), so a
    guide's oracle is runnable as-is and can't drift from a parallel schema
    (they're validated against the registry when the guide is mapped to a run).
    ``rubric`` is the LLM-judge fallback for fuzzy effects. At least one is
    required — a guide you can't score is not a guide."""

    model_config = ConfigDict(extra="forbid")

    signals: list[dict[str, Any]] = Field(default_factory=list)
    rubric: str | None = None

    @model_validator(mode="after")
    def _needs_an_oracle(self) -> GuideSuccess:
        if not self.signals and not self.rubric:
            raise ValueError("success needs at least one signal or a rubric")
        return self


class GuideTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["google_docs", "gmail", "google_sheets", "outlook", "tool_use_agent", "agentdojo"]
    surface: Literal["side_panel", "help_me_write", "summarize", "compose", "api"] = "side_panel"


class GuideProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str  # "human:bikash", "osint:reddit", "report:zettamine", …
    author: str | None = None
    created: str | None = None  # ISO date
    worked_on: str | None = None  # model/version it succeeded against
    license: str = "internal"


class Guide(BaseModel):
    """Normalized steering doc for the LLM attacker."""

    model_config = ConfigDict(extra="forbid")

    # --- required core ---
    id: str
    objective: str  # the end-effect to elicit (the invariant the attacker steers toward)
    target: GuideTarget
    success: GuideSuccess

    # --- optional, decreasing specificity ---
    threat_class: str | None = None
    strategy: str | None = None
    exemplar_turns: list[str] = Field(default_factory=list)  # a worked example → seed_chain
    seed_prompts: list[str] = Field(default_factory=list)
    key_moves: list[str] = Field(default_factory=list)

    # --- grounding knobs the attacker/judge already accept ---
    entity: str | None = None
    canary: str | None = None
    focus: str | None = None

    # --- housekeeping ---
    provenance: GuideProvenance
    tags: list[str] = Field(default_factory=list)


def content_hash(guide: Guide) -> str:
    """Stable 16-hex content hash of a guide's *attack content* — excludes
    ``id`` and ``provenance`` so two guides with identical content from
    different sources hash the same (dedup), and any change to the attack
    itself changes the hash. Recorded on a run so a finding is attributable to
    exactly which guide (+ version) produced it."""
    payload = guide.model_dump(mode="json", exclude={"id", "provenance"})
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


__all__ = ["Guide", "GuideProvenance", "GuideSuccess", "GuideTarget", "content_hash"]
