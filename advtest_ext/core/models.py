"""Core advtest data models.

Behaviours, payloads, hiding-technique descriptors, attack traces,
judge scores, replay bundles, run configs, and run results — the
narrow set of types that flow through every level of the orchestrator
and must round-trip through JSON without loss.

Behaviour-taxonomy heritage: zhan2024injecagent (direct-harm vs.
data-exfiltration) extended with output-manipulation, unauthorized-
action, and prompt-leakage categories drawn from GeminiJack
(geminijack_2025), Promptware (promptware_nassi2025), and the
HarmBench standard (mazeika2024harmbench). See FOUNDATIONS.md.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from advtest_ext.document import AbstractDocument

# ----------------------------------------------------------------------
# Enumerations (Literal for cheap JSON round-trip + strict validation)
# ----------------------------------------------------------------------

Severity = Literal["low", "med", "high", "critical"]

BehaviorCategory = Literal[
    "exfiltration",
    "instruction_override",
    "output_manipulation",
    "unauthorized_action",
    "prompt_leakage",
]

PayloadModality = Literal["text", "image", "audio", "document_block"]

AttackClass = Literal[
    "zero_width",
    "white_text",
    "structural",
    "encoded",
    "role_mimicry",
    "rendering_exfil",
]

Verdict = Literal["success", "partial", "refused", "error"]


# ----------------------------------------------------------------------
# Behaviour / Payload / HidingTechnique
# ----------------------------------------------------------------------


class Behavior(BaseModel):
    """A single test behaviour: what we're trying to elicit and why
    that's bad, with severity and a citation key for the paper /
    disclosure that motivates it. The seed catalogue lives in
    ``seed_behaviors.yaml``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    severity: Severity
    success_criteria: str
    source_citation: str
    category: BehaviorCategory


class Payload(BaseModel):
    """A concrete attack payload. ``parent_payload_id`` records the
    ancestor payload it descended from — PAIR rounds, BoN sampling
    variants, and MSJ shot-permutations all form trees and we want to
    keep them re-traversable for ablations."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    modality: PayloadModality = "text"
    generator_name: str
    parent_payload_id: str | None = None
    source_citation: str | None = None


class HidingTechnique(BaseModel):
    """Descriptor for a document-level hiding technique. ``applies_to``
    enumerates the Block discriminator types or AbstractDocument
    field names the technique knows how to operate on (e.g.
    ``["text", "heading", "comments"]``); ``requires_capability``
    enumerates the DocumentCapabilities flags the target must support
    (e.g. ``["formatting", "comments"]``)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    applies_to: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    attack_class: AttackClass
    requires_capability: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Judge + AttackTrace
# ----------------------------------------------------------------------


class JudgeScore(BaseModel):
    """One judge's verdict on one trace."""

    model_config = ConfigDict(extra="forbid")

    judge_name: str
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    flagged: bool = False
    judge_version: str = "unknown"


class AttackTrace(BaseModel):
    """One attempt: behaviour + payload + (optional) hiding technique
    + the actual request / response strings + judge verdicts.

    ``raw_request`` and ``raw_response`` are the on-the-wire payloads
    — they may carry secrets pulled from the target environment, so
    treat them as redactable when rendering reports."""

    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    behavior_id: str
    payload: Payload
    technique: HidingTechnique | None = None
    target_name: str
    raw_request: str
    raw_response: str
    judge_scores: list[JudgeScore] = Field(default_factory=list)
    verdict: Verdict | None = None
    """Final verdict. ``None`` while the trace is still pre-judging
    (the orchestrator constructs traces with verdict=None, runs the
    judge ensemble, then aggregates ``judge_scores`` into a verdict).
    Required values for finished traces are
    ``success | partial | refused | error``."""
    raw_response_data: dict[str, Any] = Field(default_factory=dict)
    """Structured side data accompanying ``raw_response``. For
    tool-using targets this carries the agent's CallLog
    (``tool_calls``, ``rounds``, ``hidden_techniques``); for chat-only
    targets it's typically empty. ``raw_response`` stays text-only —
    this field is what makes "did the tool actually fire?" answerable
    from a persisted replay bundle alone (FOUNDATIONS.md
    "Reproducibility infrastructure")."""
    parent_id: str | None = None
    """First-class column for tree/chain reconstruction. References
    the trace or attack-internal node-id this attempt descended from
    (TAP child → its parent node, future Crescendo/PAIR turn → the
    prior turn). ``None`` for single-step attacks. Hoisted from
    attack-namespace metadata at trace-build time so the dashboard
    can reconstruct chains without digging through ``metadata`` JSON
    or paying for a join via ``payload.parent_payload_id``."""
    depth: int = 0
    """First-class column. ``0`` for a single-step attack or the
    root of a multi-step chain. Hoisted from
    ``metadata['tap']['depth']`` for TAP, ``metadata['pair']['round']``
    for PAIR, ``metadata['crescendo']['n_turns']`` for Crescendo at
    trace-build time. The dashboard plots per-depth ASR and chain
    length distributions from this column directly."""
    metadata: dict[str, Any] = Field(default_factory=dict)
    """Free-form attack-namespace metadata carried over from the
    ``AttackAttempt`` that produced this trace. Persisted into the
    ``traces.extra_metadata`` JSON column so the dashboard can show
    per-attack details that don't have a first-class column —
    PAIR's per-round ``cover_story`` and ``attacker_reasoning``,
    TAP's branch / depth chain, MSJ's shot count, etc. Keys are
    attack-namespaced (``"pair"``, ``"indirect_injection"``,
    ``"tap"``, …) so multiple namespaces co-exist without
    clobbering."""
    replay_bundle_path: Path | None = None


# ----------------------------------------------------------------------
# ReplayBundle — self-contained on-disk artefact
# ----------------------------------------------------------------------


class ReplayBundle(BaseModel):
    """A self-contained reproduction kit for a single trace. Persisted
    to a directory containing:

    * ``manifest.json``           — bundle id and metadata
    * ``abstract_document.json``  — the source AbstractDocument
    * ``rendered_native.txt``     — native form (when retrievable)
    * ``prompt.txt``              — user prompt sent alongside
    * ``response.txt``            — captured AI response (text only)
    * ``response_raw.json``       — structured side data for the
                                    response (tool_calls, agent
                                    rounds, hidden techniques —
                                    written only if non-empty)
    * ``judge_scores.json``       — list[JudgeScore]
    * ``replay.py``               — script that re-runs the attack
                                    given target credentials

    Reproducibility infrastructure is one of the open problems we
    explicitly pitch (FOUNDATIONS.md "open problems" #4); every
    finding ships one of these. ``response_raw`` is what makes
    "did the tool actually fire?" answerable from disk alone.
    """

    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    abstract_document: AbstractDocument
    rendered_native: str | None = None
    prompt: str
    response: str
    response_raw: dict[str, Any] = Field(default_factory=dict)
    judge_scores: list[JudgeScore] = Field(default_factory=list)
    replay_script: str = ""

    # ClassVar so Pydantic doesn't treat these as private model attrs;
    # they're plain string constants describing on-disk filenames.
    _MANIFEST: ClassVar[str] = "manifest.json"
    _ABSTRACT: ClassVar[str] = "abstract_document.json"
    _RENDERED: ClassVar[str] = "rendered_native.txt"
    _PROMPT: ClassVar[str] = "prompt.txt"
    _RESPONSE: ClassVar[str] = "response.txt"
    _RESPONSE_RAW: ClassVar[str] = "response_raw.json"
    _JUDGE_SCORES: ClassVar[str] = "judge_scores.json"
    _REPLAY: ClassVar[str] = "replay.py"

    def write(self, directory: Path) -> Path:
        """Materialise the bundle to ``directory`` (created if missing).
        Returns the directory for chaining."""
        directory.mkdir(parents=True, exist_ok=True)
        (directory / self._MANIFEST).write_text(json.dumps({"bundle_id": self.bundle_id}, indent=2))
        (directory / self._ABSTRACT).write_text(self.abstract_document.model_dump_json(indent=2))
        if self.rendered_native is not None:
            (directory / self._RENDERED).write_text(self.rendered_native)
        (directory / self._PROMPT).write_text(self.prompt)
        (directory / self._RESPONSE).write_text(self.response)
        if self.response_raw:
            (directory / self._RESPONSE_RAW).write_text(
                json.dumps(self.response_raw, indent=2, default=str)
            )
        (directory / self._JUDGE_SCORES).write_text(
            json.dumps(
                [s.model_dump() for s in self.judge_scores],
                indent=2,
            )
        )
        (directory / self._REPLAY).write_text(self.replay_script)
        return directory

    @classmethod
    def read(cls, directory: Path) -> ReplayBundle:
        manifest = json.loads((directory / cls._MANIFEST).read_text())
        rendered_path = directory / cls._RENDERED
        rendered_native = rendered_path.read_text() if rendered_path.exists() else None
        replay_path = directory / cls._REPLAY
        replay_script = replay_path.read_text() if replay_path.exists() else ""
        response_raw_path = directory / cls._RESPONSE_RAW
        response_raw = (
            json.loads(response_raw_path.read_text()) if response_raw_path.exists() else {}
        )
        return cls(
            bundle_id=manifest["bundle_id"],
            abstract_document=AbstractDocument.model_validate_json(
                (directory / cls._ABSTRACT).read_text()
            ),
            rendered_native=rendered_native,
            prompt=(directory / cls._PROMPT).read_text(),
            response=(directory / cls._RESPONSE).read_text(),
            response_raw=response_raw,
            judge_scores=[
                JudgeScore.model_validate(s)
                for s in json.loads((directory / cls._JUDGE_SCORES).read_text())
            ],
            replay_script=replay_script,
        )


# ----------------------------------------------------------------------
# RunConfig + RunResult
# ----------------------------------------------------------------------


class Budget(BaseModel):
    """Optional bounds for a run. Any ``None`` field is "no limit"."""

    model_config = ConfigDict(extra="forbid")

    max_queries: int | None = None
    max_seconds: float | None = None
    max_cost_usd: float | None = None


class RunPhase(BaseModel):
    """One ordered phase of a multi-phase run.

    A phased run executes phases sequentially under a SINGLE ``run_id``:
    each phase has its own target, attacks, attack_options, and
    behaviours, and its traces are tagged with the phase so the dashboard
    can render one tree per phase, in order. Use this for a unified
    campaign — e.g. phase 1 drives the Gmail→Docs interaction, then phase
    2 the Docs→Docs interaction, all in one run.

    Judges, signals, budget, and output_dir are run-level (set on the
    parent :class:`RunConfig`) and shared across phases.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    """Human-readable phase name shown in the dashboard, e.g.
    ``"Gmail → Docs"``."""
    target_name: str
    target_options: dict[str, Any] = Field(default_factory=dict)
    attacks: list[str] = Field(default_factory=list)
    attack_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    behaviors: list[str] = Field(default_factory=list)


class RunConfig(BaseModel):
    """Shape of a run: target, attack plugins, behaviours, judges, and
    where to write artefacts. ``attacks`` and ``judges`` are name
    strings resolved by their respective registries elsewhere.

    Two shapes are accepted:

    * **Single-phase** (the default): set ``target_name`` + ``attacks`` +
      ``behaviors`` at the top level. Unchanged from before.
    * **Multi-phase**: set ``phases`` (an ordered list of
      :class:`RunPhase`); the top-level ``target_name`` / ``attacks`` /
      ``behaviors`` are then omitted. The run executes each phase in turn
      under one ``run_id``.

    ``judges``, ``signals``, ``budget``, ``output_dir`` are always
    run-level and shared across phases.
    """

    model_config = ConfigDict(extra="forbid")

    # Optional in multi-phase mode (the phases carry these instead).
    target_name: str | None = None
    target_options: dict[str, Any] = Field(default_factory=dict)
    """Constructor-kwargs forwarded to the target factory.

    Keys / value types are target-specific — the CLI's factory
    wrapper validates them at resolve time, not here, because the
    schema lives next to the target class. For ``google_docs`` the
    keys are ``mode`` (``"api_mode"`` / ``"ui_mode"``) and
    ``ui_output_dir`` (path). Default ``{}`` reproduces the
    pre-existing behaviour (no overrides → target defaults).
    """
    attacks: list[str] = Field(default_factory=list)
    attack_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Per-attack constructor-kwargs, keyed by attack name.

    ``attack_options["pair_indirect"]["indirect_techniques"]``,
    for example, restricts the technique cross-product to a named
    subset of the 25-technique catalogue — useful for live demos
    where running every technique against a slow delivery surface
    (ui_mode) is infeasible. Unknown keys raise at the factory
    resolution layer.
    """
    behaviors: list[str] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    judges: list[str] = Field(default_factory=list)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    """Optional Phase-3 typed signal stack, evaluated per trace
    alongside the legacy judges.

    Each entry is a config-spec dict consumed by
    :func:`advtest_ext.judges.signals.build_signal`. Shape::

        signals:
          - kind: token_appears
            name: bazinga_appears
            token: bazinga
            where: full_response
          - kind: regex_match
            name: tool_name_leak
            pattern: "(docs_agent:editor|gmail_agent:draft_reply)"
            where: full_response

    Configs without a ``signals:`` block continue running with
    the legacy single-judge path unchanged. Configs that declare
    signals get them evaluated in addition — both surfaces
    persist to storage, both render on the trace detail page.
    """
    account_pool: list[dict[str, Any]] = Field(default_factory=list)
    """Optional Phase-4 multi-account Chrome profile pool.

    Each entry is a slot binding one role to one Chrome
    persistent-context directory. Shape::

        account_pool:
          - role: recipient
            profile_dir: ~/.advtest/profile-A
            label: alice@example.com
          - role: attacker_sender
            profile_dir: ~/.advtest/profile-B
            label: malory@example.com

    Configs without an ``account_pool:`` block continue running
    on the legacy single-profile path
    (``~/.advtest/playwright-profile``) unchanged. Multi-account
    Recipes (V1-style cross-account chains) require this block.
    """
    phases: list[RunPhase] = Field(default_factory=list)
    """Optional ordered phases for a unified multi-phase run. When
    non-empty, the run executes each phase sequentially under one
    ``run_id`` and the top-level ``target_name`` / ``attacks`` /
    ``behaviors`` must be omitted (the phases carry them). Empty (the
    default) → single-phase run, unchanged."""
    output_dir: Path
    max_concurrency: int = Field(default=1, gt=0)
    db_url: str | None = None
    """Optional SQLAlchemy URL. When set, the orchestrator persists
    Run / Trace / Finding rows here in parallel with the JSON sink
    so the dashboard sees the run as it streams. CLI defaults this
    to the ``ADVTEST_DB_URL`` env var or ``sqlite:///./advtest.db``
    so a bare ``advtest run`` just shows up in ``advtest_dashboard``
    without extra wiring."""

    @model_validator(mode="after")
    def _check_phase_mode(self) -> RunConfig:
        """Enforce single-phase XOR multi-phase, and that judges exist.

        Single-phase needs a top-level ``target_name``; multi-phase needs
        ``phases`` and forbids the top-level target/attacks (which would
        be silently ignored otherwise)."""
        if self.phases:
            if self.target_name is not None or self.attacks:
                raise ValueError(
                    "multi-phase config: set target_name/attacks inside each "
                    "phase, not at the top level"
                )
            if len(self.phases) < 1:
                raise ValueError("phases must be non-empty when provided")
        else:
            if self.target_name is None:
                raise ValueError("single-phase config requires target_name (or use phases:)")
        return self


class AsrBreakdownRow(BaseModel):
    """One row of the ASR breakdown matrix: attack x technique x
    behaviour. ``technique_id`` is None for attacks that don't use a
    document-level hiding technique (direct prompts, MSJ-only)."""

    model_config = ConfigDict(extra="forbid")

    attack_name: str
    technique_id: str | None
    behavior_id: str
    successes: int = 0
    attempts: int = 0
    asr: float = Field(default=0.0, ge=0.0, le=1.0)


class CarrierAsrRow(BaseModel):
    """Per-carrier ASR rollup: one row per hiding technique (carrier),
    aggregated across all attacks and behaviours in a run. ``attack_class``
    is the carrier family (white_text / structural / zero_width / encoded /
    role_mimicry / rendering_exfil). This is the OpenRAG-Soc 'which carrier
    actually lands' view — direct-prompt traces (no technique) are excluded."""

    model_config = ConfigDict(extra="forbid")

    carrier_id: str
    attack_class: str | None = None
    successes: int = 0
    attempts: int = 0
    asr: float = Field(default=0.0, ge=0.0, le=1.0)


class SummaryStats(BaseModel):
    """Aggregate metrics for a run. ``judge_agreement_rates`` is keyed
    by ``"judgeA|judgeB"`` (sorted alphabetically) so the dict is
    canonical — the value is the simple agreement rate (fraction of
    traces where both judges agree on flagged-vs-not)."""

    model_config = ConfigDict(extra="forbid")

    total_traces: int = 0
    successful_traces: int = 0
    overall_asr: float = Field(default=0.0, ge=0.0, le=1.0)
    asr_breakdown: list[AsrBreakdownRow] = Field(default_factory=list)
    # Per-carrier (hiding-technique) ASR, sorted most-effective first.
    asr_by_carrier: list[CarrierAsrRow] = Field(default_factory=list)
    judge_agreement_rates: dict[str, float] = Field(default_factory=dict)


class RunResult(BaseModel):
    """Top-level artefact produced by a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    config: RunConfig
    traces: list[AttackTrace] = Field(default_factory=list)
    summary_stats: SummaryStats = Field(default_factory=SummaryStats)


# ----------------------------------------------------------------------
# Seed catalogue loader
# ----------------------------------------------------------------------


_SEED_BEHAVIORS_PATH = Path(__file__).resolve().parent / "seed_behaviors.yaml"


def load_seed_behaviors(path: Path | None = None) -> list[Behavior]:
    """Load and validate the seed Behaviour catalogue.

    Defaults to ``advtest_ext/core/seed_behaviors.yaml``. Returns a
    list of validated ``Behavior`` instances; raises Pydantic
    ``ValidationError`` on any malformed entry. Citation-resolution
    against ``references.bib`` is enforced by tests, not at load time
    — the catalogue should be loadable on systems that don't ship the
    bib file.
    """
    target = path or _SEED_BEHAVIORS_PATH
    raw = yaml.safe_load(target.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{target} must contain a YAML list at the top level")
    return [Behavior.model_validate(item) for item in raw]


__all__ = [
    "AsrBreakdownRow",
    "AttackClass",
    "AttackTrace",
    "Behavior",
    "BehaviorCategory",
    "Budget",
    "CarrierAsrRow",
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
