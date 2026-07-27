"""The vendored calibration-dataset registry.

Parses ``configs/calibration_datasets.yaml`` into typed source
descriptors. The registry is the single source of truth for *what*
external benchmarks Fission knows about and *how* to map each labelled
one onto the ``CalibrationExample`` schema — so ingestion is
config-driven, not a pile of per-dataset scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Repo-root-relative default: advtest_ext/calibration/datasets.py ->
# parents[2] is the repo root that holds configs/.
_DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "configs" / "calibration_datasets.yaml"


@dataclass(frozen=True)
class LabelSpec:
    """How to turn a source row's raw label into a Fission verdict."""

    column: str
    success_values: list[Any]
    refused_values: list[Any]


@dataclass(frozen=True)
class CalibrationDatasetSource:
    """One registry entry. ``labeled=False`` entries are dynamic-env
    benches (success is decided at run time); they have no ``field_map``
    / ``label`` and cannot be ingested through the static JSONL path."""

    name: str
    display_name: str
    attack_type: str
    urls: list[str]
    description: str
    labeled: bool
    fmt: str | None = None
    field_map: dict[str, str] = field(default_factory=dict)
    label: LabelSpec | None = None
    keep_meta: list[str] = field(default_factory=list)

    @property
    def ingestable(self) -> bool:
        """True when a downloaded dump can be normalised via this path
        (a static label mapping exists)."""
        return self.labeled and self.label is not None and "input" in self.field_map


def load_dataset_registry(
    path: str | Path | None = None,
) -> dict[str, CalibrationDatasetSource]:
    """Load the registry, keyed by dataset slug. Raises on an unknown
    verdict in a label spec so a malformed registry fails loudly rather
    than silently ingesting garbage verdicts."""
    registry_path = Path(path) if path is not None else _DEFAULT_REGISTRY
    raw = yaml.safe_load(registry_path.read_text())
    vocab = set(raw.get("verdict_vocab", ["success", "refused", "partial"]))
    out: dict[str, CalibrationDatasetSource] = {}
    for entry in raw.get("datasets", []):
        ingest = entry.get("ingest") or {}
        label_spec: LabelSpec | None = None
        if "label" in ingest:
            lbl = ingest["label"]
            label_spec = LabelSpec(
                column=lbl["column"],
                success_values=list(lbl.get("success_values", [])),
                refused_values=list(lbl.get("refused_values", [])),
            )
        source = CalibrationDatasetSource(
            name=entry["name"],
            display_name=entry.get("display_name", entry["name"]),
            attack_type=entry["attack_type"],
            urls=list(entry.get("urls", [])),
            description=entry.get("description", "").strip(),
            labeled=bool(entry.get("labeled", False)),
            fmt=ingest.get("format"),
            field_map=dict(ingest.get("field_map", {})),
            label=label_spec,
            keep_meta=list(ingest.get("keep_meta", [])),
        )
        out[source.name] = source
    # Sanity: the registry declares its vocab; verdicts we emit later
    # must stay inside it (belt-and-braces with the repo-level check).
    _ = vocab
    return out
