"""Dashboard run mutations, with explicit evidence cleanup for SQLite too."""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from pydantic import BaseModel, StringConstraints
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from advtest_ext.storage.models import (
    Finding,
    GoldLabel,
    HitlItem,
    JudgeScore,
    Run,
    SignalRecord,
    Trace,
)


class RenameRun(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class DeleteRun(BaseModel):
    confirmation: str


def require_same_origin(request: Request) -> None:
    """Reject browser requests initiated by another site, including localhost attacks."""
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and urlsplit(origin).netloc != request.url.netloc
    ):
        raise HTTPException(403, "Run actions must originate from this dashboard")


def delete_run_results(session: Session, run_id: str) -> None:
    """Remove run evidence atomically; retain standalone human-labelled snapshots.

    Explicit cleanup also works for existing SQLite connections where foreign
    key enforcement is disabled. No filesystem paths are removed.
    """
    trace_ids = select(Trace.id).where(Trace.run_id == run_id)
    session.execute(update(GoldLabel).where(GoldLabel.run_id == run_id).values(run_id=None))
    session.execute(
        update(GoldLabel).where(GoldLabel.trace_id.in_(trace_ids)).values(trace_id=None)
    )
    # Queue entries from another run may refer to one of these traces.
    session.execute(delete(HitlItem).where(HitlItem.run_id == run_id))
    session.execute(update(HitlItem).where(HitlItem.trace_id.in_(trace_ids)).values(trace_id=None))
    session.execute(
        delete(Finding).where((Finding.run_id == run_id) | Finding.trace_id.in_(trace_ids))
    )
    for model in (JudgeScore, SignalRecord):
        session.execute(delete(model).where(model.trace_id.in_(trace_ids)))
    session.execute(delete(Trace).where(Trace.run_id == run_id))
    session.execute(delete(Run).where(Run.id == run_id))
