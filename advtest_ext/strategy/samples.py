"""Load :class:`Sample` rows from a JSONL file.

Accepts PIMiner's ``*_injection_steps.jsonl`` shape directly (its field
names match :class:`Sample`), and maps the benchmark's ``visible_vectors``
onto ``injection_candidates`` when the explicit field is absent. Unknown
extra keys are preserved (``Sample`` allows extras) but never read by the
loop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from advtest_ext.strategy.models import Sample


def _coerce(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    if "injection_candidates" not in row and "visible_vectors" in row:
        vv = row.get("visible_vectors") or []
        if isinstance(vv, list):
            row["injection_candidates"] = vv
    return row


def load_samples(path: str | Path, *, limit: int | None = None) -> list[Sample]:
    out: list[Sample] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(Sample.model_validate(_coerce(json.loads(line))))
            if limit is not None and len(out) >= limit:
                break
    return out
