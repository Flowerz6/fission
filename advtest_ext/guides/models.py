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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    weights: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _needs_an_oracle(self) -> GuideSuccess:
        if not self.signals and not self.rubric:
            raise ValueError("success needs at least one signal or a rubric")
        signal_names = {str(spec.get("name") or spec.get("kind")) for spec in self.signals}
        unknown = set(self.weights) - signal_names
        if unknown:
            raise ValueError(f"weights reference unknown signals: {sorted(unknown)}")
        if any(not 0.0 <= weight <= 1.0 for weight in self.weights.values()):
            raise ValueError("signal weights must be in [0, 1]")
        return self


_App = Literal[
    "google_docs",
    "gmail",
    "google_sheets",
    "google_slides",
    "google_calendar",
    "google_drive",
    "google_studio",
    "outlook",
    "tool_use_agent",
    "agentdojo",
]
_AccountRole = Literal[
    "recipient", "attacker_sender", "outlook_sender", "attacker", "victim", "self"
]
_MutationDimension = Literal[
    # Revised taxonomy (docs/MUTATION_AXES_TAXONOMY.md).
    "persuasion_principle",
    "pretext_scenario",
    "persona",
    "escalation_trajectory",
    "format_mimicry",
    "carrier_placement",
    "wording",
    # Legacy axes — retained until the Signature/DB clean-break migration.
    "framing",
    "turn_order",
    "escalation_speed",
]

# Guide app name -> Recipe-Runner operation-registry region key. The runner
# registers ops under short region keys ("docs"), not the guide's app enum.
_APP_REGION: dict[str, str] = {
    "google_docs": "docs",
    "gmail": "gmail",
    "google_sheets": "sheets",
    "google_slides": "slides",
    "google_calendar": "calendar",
    "google_drive": "drive",
    "google_studio": "studio",
    "outlook": "outlook",
    "tool_use_agent": "tool_use_agent",
    "agentdojo": "agentdojo",
}


class GuideTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: _App
    surface: Literal[
        "side_panel", "help_me_write", "summarize", "compose", "api", "agent"
    ] = "side_panel"
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
    operation: (
        str  # must resolve to a registered (region, operation) — see validate_setup_operations
    )
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


class GuideEvidence(BaseModel):
    """A source artefact retained for verification, never attacker steering."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["screenshot", "transcript", "payload_source", "finding", "control"]
    path: str
    description: str
    evidence_only: Literal[True] = True


class GuideSetupVerification(BaseModel):
    """Fail-closed carrier checks run after staging and before TAP."""

    model_config = ConfigDict(extra="forbid")

    required_outputs: list[str] = Field(default_factory=list)
    probe_prompt: str | None = None
    expected_pattern: str | None = None
    attempts: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def _probe_is_complete(self) -> GuideSetupVerification:
        if bool(self.probe_prompt) != bool(self.expected_pattern):
            raise ValueError("probe_prompt and expected_pattern must be supplied together")
        return self


class GuideMutationOperator(BaseModel):
    """One explicit, mechanism-preserving mutation operator."""

    model_config = ConfigDict(extra="forbid")

    name: str
    dimension: _MutationDimension
    description: str
    mutable_fields: list[str] = Field(default_factory=list)
    # Which part of the planted carrier this operator's proposal supplies. With
    # "cover" the attacker generates ONLY a new benign document and the guide's
    # carrier_payload is re-appended deterministically; with "payload" it
    # generates ONLY a new injected instruction and the seed's benign cover is
    # kept. Either way the planted document is assembled in code, so a weak
    # attacker model can never drop the payload. "full" (the default) keeps the
    # legacy behaviour: the proposal is the whole carrier field verbatim.
    carrier_role: Literal["full", "cover", "payload"] = "full"
    allowed_transformations: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    dependent_invariants: list[str] = Field(default_factory=list)
    cell_values: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _is_actionable(self) -> GuideMutationOperator:
        if not self.name.strip() or not self.description.strip():
            raise ValueError("mutation operator name and description must be non-empty")
        if self.dimension == "carrier_placement" and not self.mutable_fields:
            raise ValueError("carrier_placement operators require mutable_fields")
        return self


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
    # The Seeder Handbook §3 harm type(s) this attack targets, from the CLOSED
    # pilot taxonomy (delivery.harm_taxonomy.PILOT_HARM_TYPES). Authored on the
    # guide — not inferred downstream — so coverage/diversity read directly off
    # the corpus and the delivery exporter can trust the label instead of
    # re-deriving it with classify_harm. ``threat_class`` stays as the free-text
    # elaboration. Empty is allowed (the exporter then falls back to inference).
    harm_types: list[str] = Field(default_factory=list)
    strategy: str | None = None
    exemplar_turns: list[str] = Field(default_factory=list)  # a worked example → seed_chain
    seed_prompts: list[str] = Field(default_factory=list)
    key_moves: list[str] = Field(default_factory=list)
    # Out-of-band operational steps to STAGE the attack (plant email, open
    # carrier doc, drive side panel), each bound to a real Runner operation so
    # the playbook can be replayed. Empty for pure single-surface guides.
    setup_steps: list[GuideStep] = Field(default_factory=list)
    setup_verification: GuideSetupVerification | None = None

    # Exact carrier/payload material; source preserves byte-for-byte originals.
    carrier_payload: str | None = None
    carrier_payload_source: str | None = None

    # Tree-search controls beyond the binary success oracle. Partial signals
    # steer pruning but are never promoted into final-success signals.
    partial_success: GuideSuccess | None = None
    branch_suggestions: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    refusal_conditions: list[str] = Field(default_factory=list)

    # --- generalisation bounds (report Table 2: what makes automated variant
    # generation safe — gate G1 checks a variant against these) ---
    # The facets the Explorer MAY vary when producing a Variant of this
    # playbook (phrasing, turn order, persona labels, carrier text). Empty
    # means the playbook has not opted any facet in — nothing is mutable.
    mutation_surface: list[str] = Field(default_factory=list)
    mutation_operators: list[GuideMutationOperator] = Field(default_factory=list)
    # Per-pretext environment content, keyed by a ``pretext_scenario`` cell value
    # (e.g. "incident-response") → the host-artifact content for that framing
    # ({"rows": [...]} for sheets, {"body": "..."} for docs). When a branch
    # mutates the pretext, the staged sheet/doc is re-seeded to match so what the
    # attacker says and what's on screen stay consistent. Empty = no re-seeding.
    environment_variants: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # What must remain true for a Variant to still BE this attack (the frame,
    # the probe, the effect). A variant that breaks an invariant is a
    # different attack, not a variant of this one.
    invariants: list[str] = Field(default_factory=list)

    @field_validator("harm_types")
    @classmethod
    def _harm_types_in_taxonomy(cls, v: list[str]) -> list[str]:
        """Every entry must be in the closed pilot harm taxonomy. Deferred import
        of ``PILOT_HARM_TYPES`` avoids a guides↔delivery import cycle at load."""
        if not v:
            return v
        from advtest_ext.delivery.harm_taxonomy import PILOT_HARM_TYPES

        allowed = set(PILOT_HARM_TYPES)
        bad = [h for h in v if h not in allowed]
        if bad:
            raise ValueError(
                f"harm_types {bad} not in the pilot taxonomy ({sorted(allowed)})"
            )
        return v

    @model_validator(mode="after")
    def _mutation_operator_names_are_unique(self) -> Guide:
        names = [operator.name for operator in self.mutation_operators]
        if len(names) != len(set(names)):
            raise ValueError("mutation operator names must be unique")
        known = set(self.invariants)
        unknown = {
            invariant
            for operator in self.mutation_operators
            for invariant in operator.dependent_invariants
            if invariant not in known
        }
        if unknown:
            raise ValueError(
                f"mutation operators reference undeclared invariants: {sorted(unknown)}"
            )
        step_ids = {step.id for step in self.setup_steps}
        for operator in self.mutation_operators:
            if operator.dimension == "carrier_placement" and not self.setup_steps:
                raise ValueError("carrier_placement operators require executable setup_steps")
            for path in operator.mutable_fields:
                tokens = path.split(".")
                if tokens[0] not in type(self).model_fields:
                    raise ValueError(f"mutation operator references unknown Guide field: {path}")
                if tokens[0] == "setup_steps" and (len(tokens) < 2 or tokens[1] not in step_ids):
                    raise ValueError(f"mutation operator references unknown setup step: {path}")
        return self

    # --- grounding knobs the attacker/judge already accept ---
    entity: str | None = None
    canary: str | None = None
    focus: str | None = None
    # Benign, stable strings a FAITHFUL summary/response would reflect if the assistant
    # actually read our planted carrier (e.g. a ticket ref, a party name). The grounding
    # gate uses these to tell "defended" (grounded, no success) from "inconclusive" (the
    # assistant never read our content) — so staging failures don't count as defences.
    grounding_anchors: list[str] = Field(default_factory=list)

    # --- lifecycle tier ---
    # "discovery": a new/unproven attack — success is judged by the LLM rubric, its
    # deterministic signal may still be a coarse hypothesis, and results are
    # candidates until reproduced. "gold": a trusted attack — a human-authored real
    # capture, or a discovery guide that reproduced across multiple fresh sessions
    # (confirm_n signal-verified successes) with a precise signal. New guides default
    # to discovery and are held to relaxed completeness; gold guides are held strictly.
    maturity: Literal["discovery", "gold"] = "discovery"

    # --- housekeeping ---
    provenance: GuideProvenance
    tags: list[str] = Field(default_factory=list)

    evidence: list[GuideEvidence] = Field(default_factory=list)


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
    payload = guide.model_dump(mode="json", exclude={"id", "title", "provenance", "maturity"})
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
    "GuideEvidence",
    "GuideMutationOperator",
    "GuideProvenance",
    "GuideSetupVerification",
    "GuideStep",
    "GuideSuccess",
    "GuideTarget",
    "content_hash",
    "display_title",
    "validate_setup_operations",
]
