"""``Recipe`` — the canonical unit of work, derived from a Seed.

A Recipe is a structured, versioned, replayable representation
of one attack. Each Recipe is a sequence of Steps; each Step
names a Surface, an operation, and the params the operation
takes. Steps can carry a ``mutation_surface`` declaring which
fields are operator-iterable (by the Phase-6 Explorer agent)
and which are preserved intent.

Two seed kinds compress into the Recipe shape:

- **Dataset-entry Recipes** are typically one or two Steps,
  produced mechanically from a corpus entry (HarmBench,
  AgentDojo, etc.) at the time the operator selects the entry
  for a Run.
- **Human-chat Recipes** are typically multi-step, produced by
  the Phase-6 Recipe Extractor agent from a docx / chat-export
  red-team report (e.g. the Google Red Team Report's V1 maps to
  a 4-step Recipe: B-composes-email, A-acks, A-opens-doc,
  A-types-compound-prompt).

This module defines the dataclasses. Storage lives in
:mod:`advtest_ext.storage.models`; runtime execution lives in
the Phase-5 Recipe Runner (not yet built).

Phase 1 of the :doc:`red_team_report_infra_plan`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from advtest_ext.core.surface import Surface


class MutationSurface(BaseModel):
    """Per-Recipe declaration of which fields the Explorer agent
    may mutate, and which Step-level fields must be preserved.

    The Explorer's job (Phase 6+) is to generate variant Recipes
    from a baseline. To do that safely it needs to know which
    fields define the *intent* of the Recipe (preserved across
    variants) vs. which are *delivery details* it's free to
    paraphrase / swap.

    Default: nothing is mutable (operator must opt in per Recipe).
    This is conservative — the alternative (everything mutable
    by default) risks the Explorer producing variants that no
    longer test the original behaviour.
    """

    model_config = ConfigDict(extra="forbid")

    preserve: list[str] = Field(
        default_factory=list,
        description=(
            "Field paths (``step.<step_id>.<field>``) that the "
            "Explorer must NOT mutate. Defines the Recipe's "
            "intent."
        ),
    )
    allowed: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Per-Step list of mutable fields. Each entry is "
            "``{step: <step_id>, fields: [<field_name>, ...]}``. "
            "Empty list means nothing is mutable."
        ),
    )
    free_text_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Field names that the Explorer may rewrite as free "
            "text (paraphrase, persona-swap, etc.) within a "
            "mutable Step. Listed separately from ``allowed`` "
            "because they get a different mutation strategy "
            "than e.g. a structured ``to:`` recipient field."
        ),
    )


class Step(BaseModel):
    """One operation in a Recipe.

    Steps name **what** happens (intent + operation), **where**
    (surface), and **what params** the operation takes. They do
    NOT carry the params' execution shape (Playwright calls,
    API requests) — that's the Replayer's job to derive from
    ``(surface, operation)``.

    ``id`` is the operator-supplied step identifier, used as
    the address for mutation-surface entries and for inter-step
    references (e.g. Step 4 reads ``{{step.plant_email.thread_id}}``
    set by Step 1).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        description=(
            "Operator-supplied step identifier. Stable across "
            "Recipe versions, used for mutation_surface "
            "references and inter-step variable substitution."
        ),
    )
    intent: str = Field(
        description=(
            "One-line description of what this Step is trying "
            "to do, in operator vocabulary. Preserved by the "
            "Explorer when mutating other fields."
        ),
    )
    surface: Surface
    operation: str = Field(
        description=(
            "App/region-specific operation name "
            "(``send_email``, ``type_prompt``, ``open_document``, "
            "``reply``, ``insert_text``). The adapter for the "
            "Step's surface looks up how to execute it."
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Operation parameters. Values may contain template "
            "references to outputs of earlier Steps "
            "(``{{step.<id>.<field>}}``)."
        ),
    )
    success_signals: list[str] = Field(
        default_factory=list,
        description=(
            "Names of Phase-3 signals (see "
            ":mod:`advtest_ext.judges.signals`) that must fire "
            "for this Step to count as successful. Empty means "
            "this Step has no per-Step success criterion — the "
            "Recipe-level signals are the only verdict."
        ),
    )
    on_fail: str = Field(
        default="abort",
        description=(
            "What the Runner does when this Step's success_signals "
            "don't fire. ``abort`` stops the Recipe; ``retry`` "
            "retries the Step (bounded); ``continue`` proceeds "
            "to the next Step regardless."
        ),
    )


class Recipe(BaseModel):
    """A versioned, replayable representation of one attack.

    Derived from one Seed (via :class:`advtest_ext.core.seed.Seed`).
    Composed of Steps. Carries a ``mutation_surface`` declaring
    which fields the Explorer agent may vary. Identified by
    ``id``; uniqueness across the catalogue is enforced by
    ``content_hash`` (sha256 of the canonicalised
    Steps + Signals + MutationSurface).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    seed_id: str = Field(
        description=(
            "The Seed this Recipe was derived from. One Recipe "
            "traces to exactly one Seed; Variants stay under "
            "their parent Recipe's lineage."
        ),
    )
    version: int = Field(
        default=1,
        ge=1,
        description=(
            "Recipe version. Edits made via the operator review "
            "queue land as a new version, never overwriting the "
            "prior one."
        ),
    )
    name: str = Field(
        description=("Human-readable label, e.g. 'V1 - Persona Forgery via Inbox Grounding'."),
    )
    content_hash: str = Field(
        description=(
            "sha256 of canonicalised steps + signals + "
            "mutation_surface. Catalogue commit enforces "
            "uniqueness on this; Variants that hash to an "
            "existing Recipe increment its rediscovered_count "
            "instead of creating a duplicate."
        ),
    )
    rediscovered_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Times the Explorer produced a variant that "
            "canonicalised to this same Recipe's content_hash. "
            "Useful as a signal in its own right — repeatedly "
            "rediscovered Recipes are likely robust attacks."
        ),
    )
    steps: list[Step] = Field(default_factory=list)
    signals: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Recipe-level signal stack — same shape as "
            "``RunConfig.signals``. Each Recipe declares its own "
            "success criteria; the Recipe Runner evaluates them "
            "against the final Step's response."
        ),
    )
    mutation_surface: MutationSurface = Field(
        default_factory=MutationSurface,
        description=(
            "Declares which fields the Explorer may mutate when generating Variants of this Recipe."
        ),
    )
    taxonomy: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Multi-axis tag set: attack_class, technique, "
            "carrier_surface, target_surface, effect, "
            "threat_model, chain_role. Operator-supplied or "
            "Extractor-derived. Dashboard filters by any axis."
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @property
    def is_atomic(self) -> bool:
        """True for single-step Recipes (typical for dataset-
        entry seeds). False for multi-step chains (typical for
        human-chat seeds)."""
        return len(self.steps) == 1
