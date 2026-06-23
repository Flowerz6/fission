"""``Seed`` — the raw input that becomes a Recipe.

Two seed kinds are supported by Fission's architecture; both
compress into the same Recipe shape downstream:

- **Dataset entry seed.** Atomic, already-structured.
  Comes from a public corpus (HarmBench, AdvBench,
  JailbreakBench, StrongREJECT, AgentDojo, etc.) pinned by
  ``corpus_version``. Materialises into a Recipe lazily —
  only when the operator selects it for a Run, to keep
  catalogue inflation from 10K-entry imports manageable.

- **Human chat seed.** Multi-turn, free-form. Comes from a red-
  team report (docx / chat-export JSON / annotated markdown /
  HAR). Always materialises into a Recipe via the Phase-6
  Extractor agent, which produces a draft Recipe for
  operator review.

The Seed model is intentionally thin — most of the structure
lives in the per-kind ``payload`` dict whose shape varies by
``source_format``. Loaders for each format (docx_loader,
chat_export_loader, …) live in ``core/loaders/`` and normalise
all formats into this shape so the Extractor only ever sees
the Seed JSON.

Phase 1 of the :doc:`red_team_report_infra_plan`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SeedKind = Literal["dataset_entry", "human_chat"]


# Source format tags so loaders / dashboards can disambiguate
# different on-disk shapes. Each format has its own loader in
# ``core/loaders/`` that normalises to this Seed shape.
SourceFormat = Literal[
    "docx_report",  # red-team report docx (the Google Red Team Report shape)
    "chat_export_json",  # exported chat transcript JSON
    "annotated_markdown",  # markdown with ``[[fig]]`` / ``[[turn]]`` annotations
    "har",  # HTTP Archive capture
    "harmbench_jsonl",  # one entry per line from HarmBench
    "advbench_csv",  # AdvBench CSV format
    "agentdojo_jsonl",
    "jailbreakbench_jsonl",
    "strongreject_csv",
]


class Seed(BaseModel):
    """One ingested artefact awaiting (or backing) one Recipe.

    Identified by ``id``. Discriminated by ``kind``. The
    ``payload`` shape varies by ``kind`` + ``source_format`` —
    loaders produce it and the Extractor consumes it.
    Round-trippable: dumping a Seed to JSON and re-loading
    yields the same Seed.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: SeedKind
    title: str = Field(
        description=(
            "Human-readable label for the Seeds dashboard. For "
            "dataset entries, typically ``<corpus>:<entry_id>`` "
            "(``harmbench:HB-0042``). For human chats, the "
            "operator's section heading from the source "
            "artefact (``V1 - Subscriber to Engineer``)."
        ),
    )
    source_format: SourceFormat
    raw_artefact_path: str = Field(
        description=(
            "On-disk path to the original artefact. Preserved "
            "so the operator-review queue can show the source "
            "alongside the Extractor's draft Recipe."
        ),
    )
    taxonomy_hints: list[str] = Field(
        default_factory=list,
        description=(
            "Operator-supplied or loader-derived tags hinting "
            "at the attack class / technique. The Extractor "
            "uses these as priors when assigning the Recipe's "
            "final taxonomy."
        ),
    )
    submitted_by: str = Field(
        description=(
            "Who ingested this Seed — an operator handle, an "
            "ingest-job id, or a corpus version string for "
            "dataset entries."
        ),
    )
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Kind-specific structured content. For human-chat "
            "seeds: typically ``{extracted_turns: [...], "
            "declared_metadata: {...}}``. For dataset-entry "
            "seeds: the upstream corpus's fields verbatim "
            "(prompt, declared_target, declared_judge, etc.) "
            "plus a corpus_version stamp."
        ),
    )
