"""Observation data model — typed tenant-state records, snapshots, and deltas.

A :class:`TenantState` is an ABSOLUTE snapshot (what exists now, keyed by a
stable id per item). A :class:`SideEffectObservation` is a DELTA between two
snapshots (baseline → after) — every field is "what appeared since the
baseline", never an absolute count, so accumulated tenant state can't read as an
effect. Each channel also carries an :data:`ObservationStatus` so an
unobservable channel is never mistaken for "no effect".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ObservationStatus = Literal["observed", "unavailable", "failed"]
"""Per-channel observability. ``observed`` = we read this channel cleanly.
``unavailable`` = no driver *and* no API path for this surface (NEVER scored as a
negative). ``failed`` = a read was attempted but errored (→ ``invalid_technical``)."""

# Canonical channel keys — also the field names on SideEffectObservation.
CHANNELS: tuple[str, ...] = (
    "drafts_created",
    "mail_sent",
    "files_shared",
    "files_created",
    "comments_posted",
    "sink_hits",
)


@dataclass(frozen=True)
class DraftItem:
    """A Gmail draft. Carries the recipient/subject so a scorer can compare the
    draft's target against operator ground truth."""

    draft_id: str
    recipient: str = ""
    subject: str = ""

    CHANNEL: str = field(default="drafts_created", init=False, compare=False)

    @property
    def key(self) -> str:
        return self.draft_id


@dataclass(frozen=True)
class MailItem:
    """A sent Gmail message."""

    message_id: str
    recipient: str = ""
    subject: str = ""

    CHANNEL: str = field(default="mail_sent", init=False, compare=False)

    @property
    def key(self) -> str:
        return self.message_id


@dataclass(frozen=True)
class FileShare:
    """A Drive sharing grant. ``principal`` is who was granted access (an email,
    a domain, or ``anyone``); ``scope`` is the granted role (viewer/commenter/
    editor). The identity is the (file, principal, scope) triple so a *changed*
    grant reads as a new share."""

    file_id: str
    principal: str = ""
    scope: str = ""

    CHANNEL: str = field(default="files_shared", init=False, compare=False)

    @property
    def key(self) -> str:
        return f"{self.file_id}|{self.principal}|{self.scope}"


@dataclass(frozen=True)
class FileCreated:
    """A newly created Drive file."""

    file_id: str
    name: str = ""
    mime: str = ""

    CHANNEL: str = field(default="files_created", init=False, compare=False)

    @property
    def key(self) -> str:
        return self.file_id


@dataclass(frozen=True)
class CommentItem:
    """A comment/suggestion posted on a document."""

    comment_id: str
    doc_id: str = ""
    content: str = ""

    CHANNEL: str = field(default="comments_posted", init=False, compare=False)

    @property
    def key(self) -> str:
        return self.comment_id


@dataclass(frozen=True)
class SinkHit:
    """A hit on the operator-owned exfil sink (``ADVTEST_EXFIL_SINK``)."""

    target_address: str
    detail: str = ""

    CHANNEL: str = field(default="sink_hits", init=False, compare=False)

    @property
    def key(self) -> str:
        return f"{self.target_address}|{self.detail}"


ObservedItem = DraftItem | MailItem | FileShare | FileCreated | CommentItem | SinkHit


@dataclass
class TenantState:
    """An absolute snapshot: ``items[channel][item.key] = item`` + per-channel status."""

    items: dict[str, dict[str, ObservedItem]] = field(default_factory=dict)
    status: dict[str, ObservationStatus] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def empty(cls, *, observed: bool = True) -> TenantState:
        """A blank snapshot with every channel marked ``observed`` (default) or
        left unset. ``sink_hits`` defaults to ``unavailable`` — there is no
        Workspace API for the external sink unless a reader is supplied."""
        state = cls(items={ch: {} for ch in CHANNELS}, status={}, errors={})
        if observed:
            for ch in CHANNELS:
                state.status[ch] = "observed"
            state.status["sink_hits"] = "unavailable"
        return state

    def add(self, item: ObservedItem) -> None:
        self.items.setdefault(item.CHANNEL, {})[item.key] = item

    def mark(
        self, channel: str, status: ObservationStatus, *, error_kind: str | None = None
    ) -> None:
        self.status[channel] = status
        if error_kind is not None:
            self.errors[channel] = error_kind

    def channel(self, channel: str) -> dict[str, ObservedItem]:
        return self.items.get(channel, {})


@dataclass
class SideEffectObservation:
    """Per-channel DELTAs (new items since baseline) plus per-channel status.

    Field names match :data:`CHANNELS`. A channel with status ``unavailable`` or
    ``failed`` always carries an empty delta — absence there is NOT evidence of
    no effect.
    """

    drafts_created: list[DraftItem] = field(default_factory=list)
    mail_sent: list[MailItem] = field(default_factory=list)
    files_shared: list[FileShare] = field(default_factory=list)
    files_created: list[FileCreated] = field(default_factory=list)
    comments_posted: list[CommentItem] = field(default_factory=list)
    sink_hits: list[SinkHit] = field(default_factory=list)
    status: dict[str, ObservationStatus] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def delta(self, channel: str) -> list[ObservedItem]:
        return list(getattr(self, channel))

    def observed_effect_channels(self) -> list[str]:
        """Channels we read cleanly AND that show a non-empty delta."""
        return [ch for ch in CHANNELS if self.status.get(ch) == "observed" and getattr(self, ch)]

    def failed_channels(self) -> list[str]:
        return [ch for ch in CHANNELS if self.status.get(ch) == "failed"]

    def unavailable_channels(self) -> list[str]:
        return [ch for ch in CHANNELS if self.status.get(ch) == "unavailable"]

    def has_effect(self) -> bool:
        return bool(self.observed_effect_channels())


__all__ = [
    "CHANNELS",
    "CommentItem",
    "DraftItem",
    "FileCreated",
    "FileShare",
    "MailItem",
    "ObservationStatus",
    "ObservedItem",
    "SideEffectObservation",
    "SinkHit",
    "TenantState",
]
