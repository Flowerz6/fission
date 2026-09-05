"""Multi-turn Sheets side-panel target — the Sheets peer of
``google_docs.ui_mode.multi_turn_target.GeminiDocsMultiTurnTarget``.

tap_crescendo / tap_chain hold ONE target per run and drive a multi-turn
Crescendo conversation: ``render_document`` mints (or adopts) one host Sheet
and caches it; ``invoke`` sends one turn, resetting the panel on the first
turn and continuing it thereafter, so the persona accumulates across turns;
``reset()`` between tree nodes starts a fresh conversation on a fresh Sheet
while keeping the warm Chrome session.

The scratch Sheet is just a control surface — for a system-prompt/skill leak
the payload rides in the conversation, not the sheet. Browser-mint mode
(``browser_mint=True``) opens one blank Sheet through the logged-in browser
with no API/OAuth, which is the simplest live path.
"""

from __future__ import annotations

import asyncio
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


class GeminiSheetsMultiTurnTarget(DocumentTarget):
    """A multi-turn ``DocumentTarget`` for the Sheets Gemini side panel.

    Collaborators (all overridable for tests):
      * ``session`` — a ``UiModeSession``-shaped object (``ensure_page``,
        ``close``). Created lazily from the profile when ``None``.
      * ``invoke_fn`` — the side-panel driver; default
        ``sheets.ui_mode.ui_mode_invoke``. Must accept
        ``spreadsheet_id`` / ``user_prompt`` / ``trace_id`` / ``output_dir`` /
        ``external_page`` / ``reset_conversation`` and return a ``Response``.
      * ``sheet_factory`` / ``sheet_deleter`` — create/delete a Sheet
        (default: the Sheets/Drive API).
    """

    def __init__(
        self,
        *,
        output_dir: Path | str,
        profile_dir: Path | None = None,
        sheet_id: str | None = None,
        sheet_ids: list[str] | None = None,
        browser_mint: bool = False,
        trace_prefix: str | None = None,
        session: Any = None,
        invoke_fn: Any = None,
        sheet_factory: Any = None,
        sheet_deleter: Any = None,
    ) -> None:
        super().__init__()
        self._output_dir = Path(output_dir)
        self._profile_dir = profile_dir
        self._sheet_id = sheet_id
        self._sheet_ids = [str(s) for s in sheet_ids] if sheet_ids else None
        self._sheet_cursor = 0
        self._browser_mint = browser_mint
        self._browser_sheet_id: str | None = None
        self._trace_prefix = trace_prefix or f"crescendo-{uuid.uuid4().hex[:6]}"
        self._session = session
        self._owns_session = session is None
        self._invoke_fn = invoke_fn
        self._sheet_factory = sheet_factory
        self._sheet_deleter = sheet_deleter
        self._ctx: NativeContext | None = None
        self._owns_sheet = False
        self._turn = 0
        # Monotonic conversation id keeps screenshots and intercepted traffic
        # unique when reset() starts another tree node.
        self._conversation = 1
        self._created_sheets: list[str] = []

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
        """Idempotent: create (or adopt) ONE host Sheet and cache it, so the
        whole escalation runs in one conversation on one sheet. ``doc`` is a
        blank control surface and is ignored."""
        del doc
        if self._ctx is not None:
            return self._ctx
        if self._sheet_ids:
            spreadsheet_id = self._sheet_ids[self._sheet_cursor % len(self._sheet_ids)]
            self._owns_sheet = False
        elif self._sheet_id:
            spreadsheet_id = self._sheet_id
            self._owns_sheet = False
        elif self._browser_mint:
            if self._browser_sheet_id is None:
                self._browser_sheet_id = await self._browser_create_sheet()
            spreadsheet_id = self._browser_sheet_id
            self._owns_sheet = False
        else:
            spreadsheet_id = await self._create_sheet()
            self._owns_sheet = True
            self._created_sheets.append(spreadsheet_id)
        self._ctx = NativeContext(
            context_id=spreadsheet_id, rendered={"spreadsheet_id": spreadsheet_id}
        )
        return self._ctx

    async def invoke(self, context: NativeContext, user_prompt: str) -> Response:
        """Send one turn to the live side panel. The FIRST invoke resets to a
        fresh conversation; later invokes continue it on the same warm page."""
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()

        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        spreadsheet_id = str(rendered.get("spreadsheet_id", ""))
        if not spreadsheet_id:
            raise RuntimeError("render_document did not record a spreadsheet_id on the context")

        reset = self._turn == 0
        trace_id = f"{self._trace_prefix}-c{self._conversation}-t{self._turn + 1}"
        invoke_fn = self._invoke_fn or self._default_invoke_fn()
        response: Response = await invoke_fn(
            spreadsheet_id=spreadsheet_id,
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
        if not self._owns_sheet:
            return
        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        spreadsheet_id = rendered.get("spreadsheet_id")
        if not spreadsheet_id:
            return
        await self._delete_sheet(str(spreadsheet_id))
        if str(spreadsheet_id) in self._created_sheets:
            self._created_sheets.remove(str(spreadsheet_id))
        self._owns_sheet = False
        self._ctx = None

    def reset(self) -> None:
        """Fresh conversation + fresh scratch Sheet on the next invoke,
        keeping the warm Chrome session. The turn counter goes back to 0 (next
        invoke passes ``reset_conversation=True``)."""
        self._turn = 0
        self._conversation += 1
        if self._sheet_ids:
            self._sheet_cursor += 1
            self._ctx = None
            self._owns_sheet = False
        elif self._sheet_id is None and not self._browser_mint:
            self._ctx = None
            self._owns_sheet = False

    async def close(self) -> None:
        """Run-level teardown: delete every scratch Sheet we minted, and close
        the Chrome session we created. Idempotent; leaves caller-supplied
        sheets / sessions alone."""
        for spreadsheet_id in self._created_sheets:
            try:
                await self._delete_sheet(spreadsheet_id)
            except Exception:
                pass
        self._created_sheets = []
        self._owns_sheet = False
        self._ctx = None
        if self._session is not None and self._owns_session:
            try:
                await self._session.close()
            finally:
                self._session = None

    def bind_carrier(self, **bindings: Any) -> None:
        sheet_id = bindings.get("sheet_id")
        if not isinstance(sheet_id, str) or not sheet_id:
            raise ValueError("Sheets carrier binding requires sheet_id")
        self._sheet_id = sheet_id
        self._sheet_ids = None
        self._ctx = None
        self._owns_sheet = False
        self.reset()

    async def cleanup_bound_carrier(self, **bindings: Any) -> bool:
        sheet_id = bindings.get("sheet_id")
        if not isinstance(sheet_id, str) or not sheet_id or self._sheet_deleter is None:
            return False
        result = self._sheet_deleter(sheet_id)
        if inspect.isawaitable(result):
            await result
        return True

    def capabilities(self) -> DocumentCapabilities:
        return DocumentCapabilities(multi_turn=True)

    def supported_techniques(self) -> set[str]:
        return {"*"}

    # ----- default (real) collaborators — overridden in tests --------

    def _default_invoke_fn(self) -> Any:
        from advtest_ext.adapters.sheets.ui_mode import ui_mode_invoke

        return ui_mode_invoke

    async def _browser_create_sheet(self) -> str:
        """Mint a blank host Sheet through the logged-in BROWSER — no API, no
        OAuth. Navigate to the Sheets ``create`` endpoint (redirects to a real
        ``/spreadsheets/d/<id>/edit``) and capture the id from the URL."""
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()
        await page.goto("https://docs.google.com/spreadsheets/create")
        for _ in range(40):  # ~12s
            m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", getattr(page, "url", "") or "")
            if m:
                return m.group(1)
            await asyncio.sleep(0.3)
        raise RuntimeError(
            "browser-mint: no /spreadsheets/d/<id> URL after navigating to "
            "/spreadsheets/create — is the Playwright profile logged into Google?"
        )

    async def _create_sheet(self) -> str:
        if self._sheet_factory is not None:
            return str(self._sheet_factory())
        from advtest_ext.adapters._auth import SHEETS_SCOPES, get_service

        sheets = get_service("sheets", "v4", SHEETS_SCOPES)
        created = (
            sheets.spreadsheets()
            .create(body={"properties": {"title": f"Scratch {uuid.uuid4().hex[:6]}"}})
            .execute()
        )
        return str(created["spreadsheetId"])

    async def _delete_sheet(self, spreadsheet_id: str) -> None:
        if self._sheet_deleter is not None:
            self._sheet_deleter(spreadsheet_id)
            return
        from advtest_ext.adapters._auth import SHEETS_SCOPES, get_service

        drive = get_service("drive", "v3", SHEETS_SCOPES)
        drive.files().delete(fileId=spreadsheet_id).execute()


__all__ = ["GeminiSheetsMultiTurnTarget"]
