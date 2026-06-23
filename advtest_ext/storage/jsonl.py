"""JSONL backup writer.

The dashboard reads from SQLAlchemy; CI / log-shipping pipelines
prefer one-trace-per-line text. We write both — the database is the
queryable source of truth, the JSONL file is grep-friendly and
survives a database wipe.

Format: each line is the full :class:`AttackTrace` JSON dump.
Append-only; we never rewrite or rotate files (a run's JSONL file
matches its run_id one-to-one and grows monotonically until the run
ends). One file per run keeps cross-run analysis as
``cat run-*.jsonl | jq ...`` rather than a join over a single
shared file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.core.models import AttackTrace


class JsonlTraceWriter:
    """Append-only JSONL writer for ``AttackTrace`` records.

    Locks the file on each ``append`` so concurrent orchestrator
    workers (``max_concurrency > 1``) don't interleave partial
    lines. The lock is in-process only — running two orchestrator
    processes against the same JSONL file isn't supported (and the
    SQLAlchemy layer is the right primary of truth for cross-process
    runs anyway)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Touch on init so a freshly-created run shows an empty file
        # in artefact listings even before the first trace lands.
        self._path.touch(exist_ok=True)
        import threading

        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, trace: AttackTrace) -> None:
        line = trace.model_dump_json()
        with self._lock, self._path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")


def read_jsonl_traces(path: Path) -> list[AttackTrace]:
    """Read a JSONL file back into a list of ``AttackTrace`` objects.

    Used by tests + the dashboard's "rebuild from JSONL backup" code
    path (when the SQLite database is missing but the JSONL file is).
    Skips blank lines so a partially-written final entry from a
    crash mid-line doesn't poison the read.
    """
    from advtest_ext.core.models import AttackTrace as DomainTrace

    out: list[DomainTrace] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(DomainTrace.model_validate_json(line))
    return out


__all__ = ["JsonlTraceWriter", "read_jsonl_traces"]
