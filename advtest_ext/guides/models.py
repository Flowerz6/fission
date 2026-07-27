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


_App = Literal["google_docs", "gmail", "google_sheets", "outlook", "tool_use_agent", "agentdojo"]
_AccountRole = Literal["recipient", "attacker_sender", "attacker", "victim", "self"]

# Guide app name -> Recipe-Runner operation-registry region key. The runner
# registers ops under short region keys ("docs"), not the guide's app enum.
_APP_REGION: dict[str, str] = {
    "google_docs": "docs",
    "gmail": "gmail",
    "google_sheets": "sheets",
    "outlook": "outlook",
    "tool_use_agent": "tool_use_agent",
    "agentdojo": "agentdojo",
}


class GuideTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: _App
    surface: Literal["side_panel", "help_me_write", "summarize", "compose", "api"] = "side_panel"
    # Which account the disclosure/interaction lands on, and a free-text label
    # of the targeted Workspace tenancy (subscription tier, product enabled).
    account_role: _AccountRole = "recipient"
    workspace: str | None = None


class GuideStep(BaseModel):
    """One out-of-band operational step the attacker runs to STAGE the attack —
    plant an email into the target's Gmail, open a benign carrier doc, drive the
    side panel. Each step is bound to a REAL Recipe-Runner operation
    ``(app-region, operation)`` so the playbook is executable/replayable, not
    just prose. ``params`` use the same field names the operation handler reads
    (e.g. gmail.send_email → to/subject/body) and may reference an earlier
    step's output via ``{{step.<id>.<field>}}`` (the Runner's substitution)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    intent: str  # one-line operator-vocabulary description
    app: _App
    account_role: _AccountRole = "recipient"
    operation: str  # must resolve to a registered (region, operation) — see validate_setup_operations
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def region(self) -> str:
        """The operation-registry region key for this step's app."""
        return _APP_REGION.get(self.app, self.app)


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
    # Short, human-readable playbook title for the UI / listings (e.g. "Sheets
    # skill-manifest disclosure"). Falls back to a prettified id when absent.
    title: str | None = None
    threat_class: str | None = None
    strategy: str | None = None
    exemplar_turns: list[str] = Field(default_factory=list)  # a worked example → seed_chain
    seed_prompts: list[str] = Field(default_factory=list)
    key_moves: list[str] = Field(default_factory=list)
    # Out-of-band operational steps to STAGE the attack (plant email, open
    # carrier doc, drive side panel), each bound to a real Runner operation so
    # the playbook can be replayed. Empty for pure single-surface guides.
    setup_steps: list[GuideStep] = Field(default_factory=list)

    # --- generalisation bounds (report Table 2: what makes automated variant
    # generation safe — gate G1 checks a variant against these) ---
    # The facets the Explorer MAY vary when producing a Variant of this
    # playbook (phrasing, turn order, persona labels, carrier text). Empty
    # means the playbook has not opted any facet in — nothing is mutable.
    mutation_surface: list[str] = Field(default_factory=list)
    # What must remain true for a Variant to still BE this attack (the frame,
    # the probe, the effect). A variant that breaks an invariant is a
    # different attack, not a variant of this one.
    invariants: list[str] = Field(default_factory=list)

    # --- grounding knobs the attacker/judge already accept ---
    entity: str | None = None
    canary: str | None = None
    focus: str | None = None

    # --- housekeeping ---
    provenance: GuideProvenance
    tags: list[str] = Field(default_factory=list)


def validate_setup_operations(guide: Guide, registry: Any = None) -> None:
    """Assert every ``setup_step`` names a REAL registered Recipe-Runner
    operation — this is what makes a guide's staging steps executable rather
    than decorative. Raises ``ValueError`` on the first unknown operation.

    The default operation registry is built lazily (it imports the product
    adapters) so guides without ``setup_steps`` never pay that import cost."""
    if not guide.setup_steps:
        return
    if registry is None:
        from advtest_ext.core.builtin_operations import default_operations

        registry = default_operations()
    for step in guide.setup_steps:
        if not registry.has(step.region, step.operation):
            available = ", ".join(f"{a}:{o}" for a, o in registry.registered_pairs())
            raise ValueError(
                f"guide {guide.id!r} setup step {step.id!r}: "
                f"unknown operation {step.region}:{step.operation!r}; registered: {available}"
            )


def content_hash(guide: Guide) -> str:
    """Stable 16-hex content hash of a guide's *attack content* — excludes
    ``id``, ``title`` and ``provenance`` (identity/cosmetic/credit, not attack
    content) so two guides with identical content from different sources hash
    the same (dedup), and any change to the attack itself changes the hash.
    Recorded on a run so a finding is attributable to exactly which guide
    (+ version) produced it."""
    payload = guide.model_dump(mode="json", exclude={"id", "title", "provenance"})
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def display_title(guide: Guide) -> str:
    """A short human title for the playbook: the explicit ``title`` when set,
    else the id prettified ("sheets_skill_leak" → "Sheets skill leak")."""
    if guide.title:
        return guide.title
    return guide.id.replace("_", " ").replace("-", " ").strip().capitalize()


__all__ = [
    "Guide",
    "GuideProvenance",
    "GuideStep",
    "GuideSuccess",
    "GuideTarget",
    "content_hash",
    "display_title",
    "validate_setup_operations",
]
