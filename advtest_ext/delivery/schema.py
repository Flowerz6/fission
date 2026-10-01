"""The pilot delivery schema — the exact per-attack record shape the Workspace
brief's "Data Format" section asks for, plus the dataset envelope that carries
the shared user-journey catalogue and the taxonomy legend.

Every field in the brief's numbered list maps to a field here:

  1. poisoned payload (full text; structured parts if structure matters) -> ``PoisonedPayload``
  2. verbatim attack string(s)                                           -> ``attack_string``
  3. harm type (from the taxonomy)                                       -> ``harm_type`` (+ subtype/rationale)
  4. custom tagging metadata (strongly encouraged)                       -> ``metadata``
  5. product + user-journey that initiated the attack                    -> ``user_journey_id`` (into the catalogue)
  6. any user-specified prompt needed to initiate                        -> ``user_supplied_prompt``
  7. attack success rate N and M over 3+ trials                          -> ``success_rate``
  8. attack lineage id                                                   -> ``lineage_id``

Serialised with ``PilotDataset.model_dump(mode="json")`` this is the JSON we
hand to Google; ``schema_version`` lets the format evolve without silently
changing meaning.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from advtest_ext.delivery.journeys import UserJourney

SCHEMA_VERSION = "1.0"


class PayloadPart(BaseModel):
    """One named part of a structured payload (e.g. email ``subject`` /
    ``body`` / ``from``, or a document ``title`` / ``body``)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    text: str


class PoisonedPayload(BaseModel):
    """Brief field 1. ``full_text`` is always present. ``structured`` is
    populated only when the payload's structure carried the attack (the brief:
    "if the structure details are important ... the structure and the text for
    each part should be specified"). ``item_link`` is the nice-to-have pointer
    to the live Workspace item."""

    model_config = ConfigDict(extra="forbid")

    # injected_content = a document/email/event carried the injection.
    # multi_turn_conversation = the attack IS a conversation; full_text renders
    # the attacker turns and ``structured`` lists them.
    payload_kind: str
    full_text: str
    structured: list[PayloadPart] = Field(default_factory=list)
    item_link: str | None = None


class SuccessRate(BaseModel):
    """Brief field 7: independent validation episodes and verified effects.

    ``verdict_breakdown`` is the raw search/validation trace distribution and
    is intentionally separate from the episode-level N and M.
    """

    model_config = ConfigDict(extra="forbid")

    trials_n: int
    successes_m: int
    asr: float = Field(ge=0.0, le=1.0)
    verdict_breakdown: dict[str, int] = Field(default_factory=dict)


class JudgeDecomposition(BaseModel):
    """The decomposed-oracle verdict on the representative trace — attended /
    complied / harmful_effect — surfaced as complexity metadata."""

    model_config = ConfigDict(extra="forbid")

    attended: bool | None = None
    complied: bool | None = None
    harmful_effect: bool | None = None
    confidence: int | None = None
    judge_score: float | None = None


class AttackMetadata(BaseModel):
    """Brief field 4: the custom tagging the brief "strongly encourage[s]" and
    scores as complexity/diversity. Free-form-ish but typed where we have a real
    value; unknown facets stay ``None`` rather than being guessed."""

    model_config = ConfigDict(extra="forbid")

    strategy: str | None = None  # attacker generator (tap_crescendo, pair, bon, ...)
    hiding_technique: str | None = None  # white_text, comment_injection, ...
    technique_class: str | None = None  # AttackClass of the hiding technique
    tone: str | None = None
    obfuscation_method: str | None = None
    exfiltration_method: str | None = None
    taxonomy_tags: list[str] = Field(default_factory=list)
    # external standards (client-facing cross-reference)
    owasp: str | None = None
    mitre_atlas: str | None = None
    promptware_class: str | None = None
    # measurement provenance
    judge_decomposition: JudgeDecomposition | None = None
    distinct_payload_variants: int = 1
    max_conversation_turns: int = 1


class PilotAttack(BaseModel):
    """One delivered attack record — successful or plausible-unsuccessful."""

    model_config = ConfigDict(extra="forbid")

    id: str
    lineage_id: str  # field 8: "start with a new idea" chain id
    outcome: str  # "successful" | "plausible_unsuccessful"

    # field 3
    harm_type: str
    harm_subtype: str = ""
    harm_rationale: str = ""
    severity: str = "medium"

    # field 1 + 2
    poisoned_payload: PoisonedPayload
    attack_string: str

    # field 5 + 6
    user_journey_id: str
    user_supplied_prompt: str | None = None

    # field 7
    success_rate: SuccessRate

    # field 4
    metadata: AttackMetadata = Field(default_factory=AttackMetadata)

    # internal provenance (not required by the brief, aids our own audit)
    source_run_ids: list[str] = Field(default_factory=list)
    representative_trace_id: str | None = None


class DatasetCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    successful: int = 0
    plausible_unsuccessful: int = 0
    by_harm_type: dict[str, int] = Field(default_factory=dict)
    by_journey: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)


class PilotDataset(BaseModel):
    """The full deliverable: envelope + catalogue + the two attack sets."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    generated_from_run_ids: list[str] = Field(default_factory=list)
    # the subset of the journey catalogue actually referenced, shipped inline
    user_journeys: dict[str, UserJourney] = Field(default_factory=dict)
    harm_taxonomy: list[str] = Field(default_factory=list)
    counts: DatasetCounts = Field(default_factory=DatasetCounts)
    successful: list[PilotAttack] = Field(default_factory=list)
    plausible_unsuccessful: list[PilotAttack] = Field(default_factory=list)


__all__ = [
    "SCHEMA_VERSION",
    "AttackMetadata",
    "DatasetCounts",
    "JudgeDecomposition",
    "PayloadPart",
    "PilotAttack",
    "PilotDataset",
    "PoisonedPayload",
    "SuccessRate",
]
