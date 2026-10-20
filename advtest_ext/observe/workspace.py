"""The live Workspace observer — Playwright/UI-first over the existing session.

The product is UI-mode only: the attack drives Gemini in a real browser session
signed into the operator's test tenant. This observer reads tenant-state deltas
through that **same** session, so the account it observes is exactly the account
the agent acted in — no second browser, no separate credentials. It opens tabs
in the warm context via ``UiModeSession.new_page()`` (a new tab, NOT a new
session/context) and scrapes the relevant surfaces.

The read-only Google API (``adapters._auth.get_service``) is an **optional
accelerator**: when the operator has provisioned matching read scopes and passes
an ``api`` accelerator, a channel prefers it over scraping. A surface with
neither a reachable driver path nor an API path is reported ``unavailable`` —
never a false negative. A read that errors is classified via
``classify_error_kind`` and reported ``failed`` (→ ``invalid_technical``).

Scraping selectors are best-effort and defensive; the CI acceptance tests
exercise the mock (:mod:`advtest_ext.observe.mock`). This class's tested
invariant is that it reuses the existing context and never opens a second
session.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from advtest_ext.observe.model import (
    CommentItem,
    DraftItem,
    FileShare,
    MailItem,
    SinkHit,
    TenantState,
)
from advtest_ext.observe.outcome import classify_failure_kind

_GMAIL_SENT_URL = "https://mail.google.com/mail/u/0/#sent"
_GMAIL_DRAFTS_URL = "https://mail.google.com/mail/u/0/#drafts"


class ReusableSession(Protocol):
    """The subset of ``UiModeSession`` this observer relies on — never a
    constructor, so the observer can never open a new browser session."""

    @property
    def context(self) -> Any: ...

    async def new_page(self) -> Any: ...


class ApiAccelerator(Protocol):
    """Optional structured-read accelerator (operator-provisioned)."""

    def drafts(self) -> list[DraftItem]: ...
    def sent(self) -> list[MailItem]: ...
    def shares(self, doc_ids: Sequence[str]) -> list[FileShare]: ...
    def comments(self, doc_ids: Sequence[str]) -> list[CommentItem]: ...


class SharesReader(Protocol):
    """A narrow read-only share-grant enumerator (sub-step 0b's
    ``observe.shares_api.SharesApiAccelerator``)."""

    def shares(self, doc_ids: Sequence[str]) -> list[FileShare]: ...


class WorkspaceObserver:
    """Observe tenant deltas by reusing the attack's Playwright session."""

    def __init__(
        self,
        *,
        session: ReusableSession | None = None,
        page: Any | None = None,
        watched_doc_ids: Sequence[str] = (),
        api: ApiAccelerator | None = None,
        shares_accelerator: SharesReader | None = None,
        sink_reader: Callable[[], list[SinkHit]] | None = None,
    ) -> None:
        self._session = session
        self._page = page
        self.watched_doc_ids = list(watched_doc_ids)
        self._api = api
        self._shares_accel = shares_accelerator
        self._sink_reader = sink_reader

    async def _tab(self) -> Any:
        """A page in the EXISTING context — the provided page, else a fresh tab
        in the warm session. Never launches a browser."""
        if self._page is not None:
            return self._page
        if self._session is not None:
            return await self._session.new_page()
        raise RuntimeError("WorkspaceObserver has no reusable page/session to observe through")

    async def snapshot(self) -> TenantState:
        state = TenantState(items={}, status={}, errors={})
        await self._observe_drafts(state)
        await self._observe_sent(state)
        await self._observe_shares(state)
        await self._observe_comments(state)
        self._observe_sink(state)
        return state

    # -- Gmail (reuse the signed-in mail session) -------------------------
    async def _observe_drafts(self, state: TenantState) -> None:
        try:
            if self._api is not None:
                for draft in self._api.drafts():
                    state.add(draft)
            else:
                page = await self._tab()
                for item in await _scrape_gmail_list(page, _GMAIL_DRAFTS_URL, draft=True):
                    state.add(item)
            state.mark("drafts_created", "observed")
        except Exception as exc:
            state.mark("drafts_created", "failed", error_kind=classify_failure_kind(exc))

    async def _observe_sent(self, state: TenantState) -> None:
        try:
            if self._api is not None:
                for mail in self._api.sent():
                    state.add(mail)
            else:
                page = await self._tab()
                for item in await _scrape_gmail_list(page, _GMAIL_SENT_URL, draft=False):
                    state.add(item)
            state.mark("mail_sent", "observed")
        except Exception as exc:
            state.mark("mail_sent", "failed", error_kind=classify_failure_kind(exc))

    # -- Drive shares -----------------------------------------------------
    async def _observe_shares(self, state: TenantState) -> None:
        # Reliable share-scope detection needs the read-only Drive permissions
        # accelerator (sub-step 0b); UI scraping of the share dialog per file is
        # not dependable. Without an accelerator this channel is 'unavailable'.
        accel: SharesReader | None = self._shares_accel or self._api
        if accel is None:
            state.mark("files_shared", "unavailable")
            return
        try:
            for share in accel.shares(self.watched_doc_ids):
                state.add(share)
            state.mark("files_shared", "observed")
        except Exception as exc:
            state.mark("files_shared", "failed", error_kind=classify_failure_kind(exc))

    # -- Docs comments ----------------------------------------------------
    async def _observe_comments(self, state: TenantState) -> None:
        try:
            if self._api is not None:
                for comment in self._api.comments(self.watched_doc_ids):
                    state.add(comment)
                state.mark("comments_posted", "observed")
                return
            if not self.watched_doc_ids:
                state.mark("comments_posted", "unavailable")
                return
            page = await self._tab()
            for doc_id in self.watched_doc_ids:
                for item in await _scrape_doc_comments(page, doc_id):
                    state.add(item)
            state.mark("comments_posted", "observed")
        except Exception as exc:
            state.mark("comments_posted", "failed", error_kind=classify_failure_kind(exc))

    # -- Exfil sink (external; operator-owned) ----------------------------
    def _observe_sink(self, state: TenantState) -> None:
        if self._sink_reader is None:
            state.mark("sink_hits", "unavailable")
            return
        try:
            for hit in self._sink_reader():
                state.add(hit)
            state.mark("sink_hits", "observed")
        except Exception as exc:
            state.mark("sink_hits", "failed", error_kind=classify_failure_kind(exc))


# ---------------------------------------------------------------------------
# Best-effort scrapers (reused-page; defensive). Untested live by design.
# ---------------------------------------------------------------------------


async def _scrape_gmail_list(page: Any, url: str, *, draft: bool) -> list[Any]:
    """Navigate the reused page to a Gmail list and read row recipient/subject.

    Gmail rows carry the recipient in ``span[email]`` and the subject in the row
    text; ids come from the row's ``data-legacy-thread-id`` / ``id``. Best-effort.
    """
    await page.goto(url)
    rows = await page.query_selector_all("tr.zA")
    items: list[Any] = []
    for row in rows:
        row_id = (
            (await row.get_attribute("data-legacy-thread-id"))
            or (await row.get_attribute("id"))
            or ""
        )
        recipient_el = await row.query_selector("span[email]")
        recipient = (await recipient_el.get_attribute("email")) if recipient_el else ""
        subject_el = await row.query_selector(".bog")
        subject = (await subject_el.inner_text()) if subject_el else ""
        if not row_id:
            continue
        if draft:
            items.append(
                DraftItem(draft_id=str(row_id), recipient=recipient or "", subject=subject)
            )
        else:
            items.append(
                MailItem(message_id=str(row_id), recipient=recipient or "", subject=subject)
            )
    return items


async def _scrape_doc_comments(page: Any, doc_id: str) -> list[CommentItem]:
    """Open a Doc in the reused context and read rendered comment threads."""
    await page.goto(f"https://docs.google.com/document/d/{doc_id}/edit")
    nodes = await page.query_selector_all("div.docos-anchoreddocoview-content")
    items: list[CommentItem] = []
    for i, node in enumerate(nodes):
        content = await node.inner_text()
        cid = (await node.get_attribute("id")) or f"{doc_id}:comment:{i}"
        items.append(CommentItem(comment_id=str(cid), doc_id=doc_id, content=content))
    return items


async def snapshot_baseline_async(observer: WorkspaceObserver) -> TenantState:
    """Async baseline capture for the live (Playwright) observer."""
    return await observer.snapshot()


__all__ = [
    "ApiAccelerator",
    "ReusableSession",
    "SharesReader",
    "WorkspaceObserver",
    "snapshot_baseline_async",
]
