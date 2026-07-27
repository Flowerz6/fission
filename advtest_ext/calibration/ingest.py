"""Normalise a downloaded benchmark dump into ``CalibrationExample``
rows via a dataset's registry mapping.

Only static-label datasets (``source.ingestable``) go through here;
dynamic-env benches (AgentDojo, MCPTox) define success by executing a
tool call at run time and must be driven through a live adapter instead.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from advtest_ext.calibration.datasets import CalibrationDatasetSource, LabelSpec

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

__all__ = [
    "CalibrationRecord",
    "ingest_records",
    "normalize_verdict",
    "records_from_jsonl",
]


@dataclass(frozen=True)
class CalibrationRecord:
    """A normalised external example, ready to persist."""

    id: str
    dataset: str
    attack_type: str
    input_text: str
    expected_verdict: str
    external_id: str | None = None
    response_text: str = ""
    label_raw: str = ""
    label_meta: dict[str, Any] = field(default_factory=dict)


def normalize_verdict(raw: Any, spec: LabelSpec) -> str:
    """Map a source row's raw label value onto a Fission verdict.

    Comparison is done on the string form too, so a boolean ``True`` in
    JSON and a literal ``"true"`` both match a spec listing either. A
    value in neither the success nor refused set raises — an
    unrecognised label must never be silently coerced into a verdict the
    calibration numbers would then trust.
    """
    candidates = {raw}
    if isinstance(raw, str):
        candidates.add(raw.strip().lower())
    else:
        candidates.add(str(raw).lower())

    def _match(values: list[Any]) -> bool:
        normalised = {v if not isinstance(v, str) else v.strip().lower() for v in values}
        return bool(candidates & normalised)

    if _match(spec.success_values):
        return "success"
    if _match(spec.refused_values):
        return "refused"
    raise ValueError(
        f"label value {raw!r} matches neither success_values nor "
        f"refused_values for column {spec.column!r}"
    )


def _stable_id(dataset: str, external_id: str | None, input_text: str) -> str:
    """Deterministic id so re-ingesting the same dump updates in place.
    Prefers the source's own id; falls back to a content hash."""
    if external_id:
        return f"cal-{dataset}-{external_id}"
    digest = hashlib.sha1(input_text.encode("utf-8")).hexdigest()[:16]
    return f"cal-{dataset}-{digest}"


def records_from_jsonl(
    path: str | Path,
    source: CalibrationDatasetSource,
) -> list[CalibrationRecord]:
    """Read a JSONL dump and normalise each line via ``source``'s
    registry mapping. Rows missing the input or label column are skipped
    (returned count reflects only usable rows)."""
    if not source.ingestable:
        raise ValueError(
            f"dataset {source.name!r} is not statically ingestable "
            f"(labeled={source.labeled}, has_label={source.label is not None}); "
            "drive it through a live adapter instead"
        )
    assert source.label is not None  # guaranteed by ingestable
    input_col = source.field_map["input"]
    response_col = source.field_map.get("response")
    ext_col = source.field_map.get("external_id")

    records: list[CalibrationRecord] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if input_col not in row or source.label.column not in row:
            continue
        raw_label = row[source.label.column]
        verdict = normalize_verdict(raw_label, source.label)
        external_id = str(row[ext_col]) if ext_col and row.get(ext_col) is not None else None
        input_text = str(row[input_col])
        meta = {k: row[k] for k in source.keep_meta if k in row}
        records.append(
            CalibrationRecord(
                id=_stable_id(source.name, external_id, input_text),
                dataset=source.name,
                attack_type=source.attack_type,
                input_text=input_text,
                expected_verdict=verdict,
                external_id=external_id,
                response_text=str(row[response_col])
                if response_col and response_col in row
                else "",
                label_raw=str(raw_label),
                label_meta=meta,
            )
        )
    return records


def ingest_records(session: Session, records: Iterable[CalibrationRecord]) -> int:
    """Persist normalised records (idempotent per id). Returns the count
    of genuinely new rows (already-present ids are skipped, not counted)
    so a re-ingest of an overlapping dump reports 0 rather than inflating."""
    from advtest_ext.storage import insert_calibration_example
    from advtest_ext.storage.models import CalibrationExample

    inserted = 0
    for r in records:
        if session.get(CalibrationExample, r.id) is not None:
            continue
        insert_calibration_example(
            session,
            id=r.id,
            dataset=r.dataset,
            attack_type=r.attack_type,
            input_text=r.input_text,
            expected_verdict=r.expected_verdict,
            external_id=r.external_id,
            response_text=r.response_text,
            label_raw=r.label_raw,
            label_meta=r.label_meta,
        )
        inserted += 1
    return inserted
