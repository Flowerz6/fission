"""Scripted observer for CI — no live tenant, no browser.

:class:`MockWorkspaceObserver` returns pre-scripted :class:`TenantState`
snapshots so the whole observe layer + Part-C scorers are testable offline.
:func:`bridge_call_log` turns the contract's mock target
(``adapters.tool_use_agent`` — a fake Google Workspace assistant with fake
``send_email`` / ``share_document`` tools and a ``CallLog``) into observed deltas,
mirroring how the live layer infers tool calls from tenant state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from uuid import uuid4

from advtest_ext.observe.model import (
    CommentItem,
    DraftItem,
    FileCreated,
    FileShare,
    MailItem,
    ObservedItem,
    SinkHit,
    TenantState,
)
from advtest_ext.observe.outcome import classify_failure_kind


def state(
    *,
    drafts: Iterable[DraftItem] = (),
    mail_sent: Iterable[MailItem] = (),
    files_shared: Iterable[FileShare] = (),
    files_created: Iterable[FileCreated] = (),
    comments: Iterable[CommentItem] = (),
    sink_hits: Iterable[SinkHit] = (),
    failed: Mapping[str, BaseException] | None = None,
    unavailable: Iterable[str] = (),
) -> TenantState:
    """Build a :class:`TenantState` for tests.

    ``failed`` maps a channel → the exception that broke its read (classified via
    ``classify_error_kind``). ``unavailable`` names channels with no path.
    """
    snap = TenantState.empty(observed=True)
    for draft in drafts:
        snap.add(draft)
    for mail in mail_sent:
        snap.add(mail)
    for share in files_shared:
        snap.add(share)
    for created in files_created:
        snap.add(created)
    for comment in comments:
        snap.add(comment)
    for hit in sink_hits:
        snap.add(hit)
    for channel, exc in (failed or {}).items():
        snap.mark(channel, "failed", error_kind=classify_failure_kind(exc))
    for channel in unavailable:
        snap.mark(channel, "unavailable")
    return snap


@dataclass
class MockWorkspaceObserver:
    """Returns successive scripted snapshots (pre, then post, then repeats post)."""

    snapshots: list[TenantState] = field(default_factory=list)
    _idx: int = 0

    @classmethod
    def from_states(cls, pre: TenantState, post: TenantState) -> MockWorkspaceObserver:
        return cls(snapshots=[pre, post])

    def snapshot(self) -> TenantState:
        if not self.snapshots:
            return TenantState.empty()
        snap = self.snapshots[min(self._idx, len(self.snapshots) - 1)]
        self._idx += 1
        return snap


def _msg_id_from_result(result: str) -> str:
    marker = "message-id="
    if marker in result:
        return result.split(marker, 1)[1].split(";", 1)[0].strip()
    return f"msg-{uuid4().hex[:12]}"


def bridge_call_log(calls: Iterable[object], *, base: TenantState | None = None) -> TenantState:
    """Turn a ``tool_use_agent`` CallLog (any iterable of records with
    ``tool_name`` + ``args``) into a post-run :class:`TenantState`.

    ``send_email`` → :class:`MailItem`; ``share_document`` → :class:`FileShare`.
    Tools with no observable Workspace delta (calendar, delete) are ignored — the
    live layer likewise can only see created/shared/sent state.
    """
    snap = base if base is not None else TenantState.empty(observed=True)
    for record in calls:
        tool_name = str(getattr(record, "tool_name", ""))
        args = getattr(record, "args", {}) or {}
        result = str(getattr(record, "result", ""))
        if not isinstance(args, Mapping):
            continue
        item = _record_to_item(tool_name, args, result)
        if item is not None:
            snap.add(item)
    return snap


def _record_to_item(tool_name: str, args: Mapping[str, object], result: str) -> ObservedItem | None:
    if tool_name == "send_email":
        return MailItem(
            message_id=_msg_id_from_result(result),
            recipient=str(args.get("to", "")),
            subject=str(args.get("subject", "")),
        )
    if tool_name == "share_document":
        return FileShare(
            file_id=str(args.get("doc_id", "")),
            principal=str(args.get("with_email", "")),
            scope=str(args.get("role", "")),
        )
    return None


__all__ = ["MockWorkspaceObserver", "bridge_call_log", "state"]
