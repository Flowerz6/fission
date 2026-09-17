"""Multi-turn Calendar side-panel target — the Calendar peer of
``sheets.ui_mode.multi_turn_target.GeminiSheetsMultiTurnTarget``.

tap_crescendo / tap_chain hold ONE target per run and drive a multi-turn
Crescendo conversation: ``render_document`` mints (or adopts) one host event
and caches it; ``invoke`` sends one turn, resetting the panel on the first
turn and continuing it thereafter, so the persona accumulates across turns;
``reset()`` between tree nodes starts a fresh conversation on a fresh event
while keeping the warm Chrome session.

The scratch event is just a control surface — for a system-prompt/skill leak
the payload rides in the conversation, not the event. Browser-mint mode
(``browser_mint=True``) opens one blank event through the logged-in browser
with no API/OAuth, which is the simplest live path.
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from advtest_ext.document.target import (
    DocumentCapabilities,
    DocumentTarget,
    NativeContext,
    Response,
)

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.document import AbstractDocument


class GeminiCalendarMultiTurnTarget(DocumentTarget):
    """A multi-turn ``DocumentTarget`` for the Calendar Gemini side panel.

    Collaborators (all overridable for tests):
      * ``session`` — a ``UiModeSession``-shaped object (``ensure_page``,
        ``close``). Created lazily from the profile when ``None``.
      * ``invoke_fn`` — the side-panel driver; default
        ``calendar.ui_mode.ui_mode_invoke``. Must accept
        ``event_id`` / ``user_prompt`` / ``trace_id`` / ``output_dir`` /
        ``external_page`` / ``reset_conversation`` and return a ``Response``.
      * ``event_factory`` / ``event_deleter`` — create/delete an event
        (default: the Calendar API).
    """

    def __init__(
        self,
        *,
        output_dir: Path | str,
        profile_dir: Path | None = None,
        event_id: str | None = None,
        event_ids: list[str] | None = None,
        browser_mint: bool = False,
        trace_prefix: str | None = None,
        session: Any = None,
        invoke_fn: Any = None,
        event_factory: Any = None,
        event_deleter: Any = None,
    ) -> None:
        super().__init__()
        self._output_dir = Path(output_dir)
        self._profile_dir = profile_dir
        self._event_id = event_id
        self._event_ids = [str(s) for s in event_ids] if event_ids else None
        self._event_cursor = 0
        self._browser_mint = browser_mint
        self._browser_event_id: str | None = None
        self._trace_prefix = trace_prefix or f"crescendo-{uuid.uuid4().hex[:6]}"
        self._session = session
        self._owns_session = session is None
        self._invoke_fn = invoke_fn
        self._event_factory = event_factory
        self._event_deleter = event_deleter
        self._ctx: NativeContext | None = None
        self._owns_event = False
        self._turn = 0
        # Monotonic conversation id keeps screenshots and intercepted traffic
        # unique when reset() starts another tree node.
        self._conversation = 1
        self._created_events: list[str] = []

    @property
    def context(self) -> NativeContext | None:
        return self._ctx

    # ----- DocumentTarget surface -------------------------------------

    async def setup(self) -> None:
        if self._session is None:
            from advtest_ext.adapters._ui_mode_common import UiModeSession
            from advtest_ext.adapters._ui_mode_common.config import resolve_profile_dir

            self._session = UiModeSession(profile_dir=self._profile_dir or resolve_profile_dir())

    async def render_document(self, doc: AbstractDocument) -> NativeContext:
        """Idempotent: create (or adopt) ONE host event and cache it, so the
        whole escalation runs in one conversation on one event. ``doc`` is a
        blank control surface and is ignored."""
        del doc
        if self._ctx is not None:
            return self._ctx
        if self._event_ids:
            event_id = self._event_ids[self._event_cursor % len(self._event_ids)]
            self._owns_event = False
        elif self._event_id:
            event_id = self._event_id
            self._owns_event = False
        elif self._browser_mint:
            if self._browser_event_id is None:
                self._browser_event_id = await self._browser_create_event()
            event_id = self._browser_event_id
            self._owns_event = False
        else:
            event_id = await self._create_event()
            self._owns_event = True
            self._created_events.append(event_id)
        self._ctx = NativeContext(context_id=event_id, rendered={"event_id": event_id})
        return self._ctx

    async def invoke(self, context: NativeContext, user_prompt: str) -> Response:
        """Send one turn to the live side panel. The FIRST invoke resets to a
        fresh conversation; later invokes continue it on the same warm page."""
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()

        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        event_id = str(rendered.get("event_id", ""))
        if not event_id:
            raise RuntimeError("render_document did not record an event_id on the context")

        reset = self._turn == 0
        trace_id = f"{self._trace_prefix}-c{self._conversation}-t{self._turn + 1}"
        invoke_fn = self._invoke_fn or self._default_invoke_fn()
        response: Response = await invoke_fn(
            event_id=event_id,
            user_prompt=user_prompt,
            trace_id=trace_id,
            output_dir=self._output_dir,
            external_page=page,
            reset_conversation=reset,
        )
        self._turn += 1
        try:
            response.raw["turn"] = self._turn
            response.raw["is_multi_turn"] = True
        except Exception:
            pass
        return response

    async def teardown(self, context: NativeContext) -> None:
        if not self._owns_event:
            return
        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        event_id = rendered.get("event_id")
        if not event_id:
            return
        await self._delete_event(str(event_id))
        if str(event_id) in self._created_events:
            self._created_events.remove(str(event_id))
        self._owns_event = False
        self._ctx = None

    def reset(self) -> None:
        """Fresh conversation + fresh scratch event on the next invoke,
        keeping the warm Chrome session. The turn counter goes back to 0 (next
        invoke passes ``reset_conversation=True``)."""
        self._turn = 0
        self._conversation += 1
        if self._event_ids:
            self._event_cursor += 1
            self._ctx = None
            self._owns_event = False
        elif self._event_id is None and not self._browser_mint:
            self._ctx = None
            self._owns_event = False

    async def close(self) -> None:
        """Run-level teardown: delete every scratch event we minted, and close
        the Chrome session we created. Idempotent; leaves caller-supplied
        events / sessions alone."""
        for event_id in self._created_events:
            try:
                await self._delete_event(event_id)
            except Exception:
                pass
        self._created_events = []
        self._owns_event = False
        self._ctx = None
        if self._session is not None and self._owns_session:
            try:
                await self._session.close()
            finally:
                self._session = None

    def bind_carrier(self, **bindings: Any) -> None:
        event_id = bindings.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Calendar carrier binding requires event_id")
        self._event_id = event_id
        self._event_ids = None
        self._ctx = None
        self._owns_event = False
        self.reset()

    async def cleanup_bound_carrier(self, **bindings: Any) -> bool:
        event_id = bindings.get("event_id")
        if not isinstance(event_id, str) or not event_id or self._event_deleter is None:
            return False
        result = self._event_deleter(event_id)
        if inspect.isawaitable(result):
            await result
        return True

    def capabilities(self) -> DocumentCapabilities:
        return DocumentCapabilities(multi_turn=True)

    def supported_techniques(self) -> set[str]:
        return {"*"}

    # ----- default (real) collaborators — overridden in tests --------

    def _default_invoke_fn(self) -> Any:
        from advtest_ext.adapters.calendar.ui_mode import ui_mode_invoke

        return ui_mode_invoke

    async def _browser_create_event(self) -> str:
        """Mint a blank host event through the logged-in BROWSER — no API, no
        OAuth. Navigate to the Calendar event-create endpoint (opens the event
        editor) and capture the ``eid`` from the URL. NOTE: Calendar encodes
        ``eid`` as a base64 blob (not the raw event id) and only assigns it
        after the event is saved — LIVE VALIDATION REQUIRED before a real run."""
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()
        await page.goto("https://calendar.google.com/calendar/u/0/r/eventedit")
        for _ in range(40):  # ~12s
            m = re.search(r"[?&]eid=([A-Za-z0-9_-]{10,})", getattr(page, "url", "") or "")
            if m:
                return m.group(1)
            await asyncio.sleep(0.3)
        raise RuntimeError(
            "browser-mint: no ?eid=<id> URL after navigating to "
            "/calendar/u/0/r/eventedit — is the Playwright profile logged into "
            "Google, and has the event been saved? (Calendar assigns eid on save.)"
        )

    async def _create_event(self) -> str:
        if self._event_factory is not None:
            return str(self._event_factory())
        from advtest_ext.adapters._auth import get_service
        from advtest_ext.adapters.calendar.target import CALENDAR_SCOPES

        calendar = get_service("calendar", "v3", CALENDAR_SCOPES)
        today = datetime.date.today()
        tomorrow = today + datetime.timedelta(days=1)
        created = (
            calendar.events()
            .insert(
                calendarId="primary",
                body={
                    "summary": f"Scratch {uuid.uuid4().hex[:6]}",
                    "start": {"date": today.isoformat()},
                    "end": {"date": tomorrow.isoformat()},
                },
            )
            .execute()
        )
        return str(created["id"])

    async def _delete_event(self, event_id: str) -> None:
        if self._event_deleter is not None:
            self._event_deleter(event_id)
            return
        from advtest_ext.adapters._auth import get_service
        from advtest_ext.adapters.calendar.target import CALENDAR_SCOPES

        calendar = get_service("calendar", "v3", CALENDAR_SCOPES)
        calendar.events().delete(calendarId="primary", eventId=event_id).execute()


__all__ = ["GeminiCalendarMultiTurnTarget"]
