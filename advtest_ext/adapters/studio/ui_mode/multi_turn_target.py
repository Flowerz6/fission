"""A multi-turn, live-conversation ``DocumentTarget`` for a Workspace Studio
app.

Workspace Studio is agentic: a built app is pointed at a DATA SOURCE (typically
a shared Sheet) and, when run, ingests it and can act across tools. The indirect
injection rides in that data source (planted out-of-band via
``sheets.create_and_seed``), not in the user's turn — the benign trigger is just
"run the app". This target keeps ONE warm Studio app open across invocations so
a crescendo / tap_crescendo attack can escalate the run instructions over turns,
exactly like the Slides side-panel target keeps one warm panel.

Mirror of :class:`~advtest_ext.adapters.slides.ui_mode.multi_turn_target.
GeminiSlidesMultiTurnTarget` with deck→app: the host is a Studio *app*
(``app_id``) rather than a deck, and the driver is the Studio invoker rather
than the side-panel one. The app is only the host surface; nothing adversarial
goes in it — the payload lives in the app's data source.

STUDIO ADAPTER NOTE: there is no public Studio API this code can create an app
with, so the default ``_create_app`` raises with guidance — supply an existing
``app_id`` (or a full app URL as ``app_id``), pass ``browser_mint=True`` to mint
one through the logged-in browser, or inject an ``app_factory`` in tests. The
Studio selectors and app-URL shape are SEEDED and need live validation.
"""

from __future__ import annotations

import inspect
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from advtest_ext.document.target import (
    DocumentCapabilities,
    DocumentTarget,
    NativeContext,
    Response,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.document import AbstractDocument


class GeminiStudioMultiTurnTarget(DocumentTarget):
    """Live multi-turn Workspace Studio app target.

    All external collaborators are injectable so the flow is unit-testable
    without Playwright, Chrome, or any Workspace API:

    * ``session`` — a ``UiModeSession``-shaped object (``ensure_page`` /
      ``close``). Default: lazily built on the resolved profile.
    * ``invoke_fn`` — the Studio driver. Default:
      ``studio.ui_mode.ui_mode_invoke``. Must accept ``app_id, user_prompt,
      trace_id, output_dir, external_page, reset_conversation`` and return a
      :class:`Response`.
    * ``app_factory`` — ``() -> app_id`` for a scratch app.
    * ``app_deleter`` — ``(app_id) -> None`` for teardown.

    Pass ``app_id`` to reuse an existing app (never deleted). Pass ``app_ids``
    to ground each tree NODE on a different existing app (rotated on ``reset``).
    """

    def __init__(
        self,
        *,
        output_dir: Path | str,
        profile_dir: Path | None = None,
        app_id: str | None = None,
        app_ids: list[str] | None = None,
        browser_mint: bool = False,
        trace_prefix: str | None = None,
        session: Any = None,
        invoke_fn: Any = None,
        app_factory: Any = None,
        app_deleter: Any = None,
    ) -> None:
        super().__init__()
        self._output_dir = Path(output_dir)
        self._profile_dir = profile_dir
        self._app_id = app_id
        self._app_ids = [str(a) for a in app_ids] if app_ids else None
        self._app_cursor = 0
        self._browser_mint = browser_mint
        self._browser_app_id: str | None = None
        self._trace_prefix = trace_prefix or f"studio-{uuid.uuid4().hex[:6]}"
        self._session = session
        self._owns_session = session is None
        self._invoke_fn = invoke_fn
        self._app_factory = app_factory
        self._app_deleter = app_deleter
        self._ctx: NativeContext | None = None
        self._owns_app = False
        self._turn = 0
        self._conversation = 1
        self._created_apps: list[str] = []

    @property
    def context(self) -> NativeContext | None:
        return self._ctx

    # ------------------------------------------------------------------
    # DocumentTarget surface
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        if self._session is None:
            from advtest_ext.adapters._ui_mode_common import UiModeSession
            from advtest_ext.adapters._ui_mode_common.config import resolve_profile_dir

            self._session = UiModeSession(profile_dir=self._profile_dir or resolve_profile_dir())

    async def render_document(self, doc: AbstractDocument) -> NativeContext:
        """Idempotent: adopt or mint ONE Studio app and cache it. ``doc`` is a
        blank control surface — the payload rides in the app's data source."""
        del doc
        if self._ctx is not None:
            return self._ctx
        if self._app_ids:
            app_id = self._app_ids[self._app_cursor % len(self._app_ids)]
            self._owns_app = False
        elif self._app_id:
            app_id = self._app_id
            self._owns_app = False
        elif self._browser_mint:
            if self._browser_app_id is None:
                self._browser_app_id = await self._browser_create_app()
            app_id = self._browser_app_id
            self._owns_app = False
        else:
            app_id = await self._create_app()
            self._owns_app = True
            self._created_apps.append(app_id)
        self._ctx = NativeContext(context_id=app_id, rendered={"app_id": app_id})
        return self._ctx

    async def invoke(self, context: NativeContext, user_prompt: str) -> Response:
        """Send one turn to the live Studio app. The FIRST invoke resets to a
        fresh run; later invokes continue it on the same warm page."""
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()

        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        app_id = str(rendered.get("app_id", ""))
        if not app_id:
            raise RuntimeError("render_document did not record an app_id on the context")

        reset = self._turn == 0
        trace_id = f"{self._trace_prefix}-c{self._conversation}-t{self._turn + 1}"
        invoke_fn = self._invoke_fn or self._default_invoke_fn()
        response: Response = await invoke_fn(
            app_id=app_id,
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
        if not self._owns_app:
            return
        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        app_id = rendered.get("app_id")
        if not app_id:
            return
        await self._delete_app(str(app_id))
        if str(app_id) in self._created_apps:
            self._created_apps.remove(str(app_id))
        self._owns_app = False
        self._ctx = None

    def reset(self) -> None:
        """Start a FRESH run AND a fresh scratch app on the next invoke, keeping
        the warm Chrome session (mirrors the Slides target's node isolation)."""
        self._turn = 0
        self._conversation += 1
        if self._app_ids:
            self._app_cursor += 1
            self._ctx = None
            self._owns_app = False
        elif self._app_id is None and not self._browser_mint:
            self._ctx = None
            self._owns_app = False

    async def close(self) -> None:
        for app_id in self._created_apps:
            try:
                await self._delete_app(app_id)
            except Exception:
                pass
        self._created_apps = []
        self._owns_app = False
        self._ctx = None
        if self._session is not None and self._owns_session:
            try:
                await self._session.close()
            finally:
                self._session = None

    def bind_carrier(self, **bindings: Any) -> None:
        app_id = bindings.get("app_id")
        if not isinstance(app_id, str) or not app_id:
            raise ValueError("Studio carrier binding requires app_id")
        self._app_id = app_id
        self._app_ids = None
        self._ctx = None
        self._owns_app = False
        self.reset()

    async def cleanup_bound_carrier(self, **bindings: Any) -> bool:
        app_id = bindings.get("app_id")
        if not isinstance(app_id, str) or not app_id or self._app_deleter is None:
            return False
        result = self._app_deleter(app_id)
        if inspect.isawaitable(result):
            await result
        return True

    def capabilities(self) -> DocumentCapabilities:
        return DocumentCapabilities(multi_turn=True)

    def supported_techniques(self) -> set[str]:
        # The host app carries no injection (the payload is in its data source),
        # so app-hiding techniques don't apply — ``{"*"}`` keeps it from being
        # capability-gated out for attacks (like Crescendo) that inject nothing
        # into the host.
        return {"*"}

    # ------------------------------------------------------------------
    # Default (real) collaborators — overridden in tests via __init__
    # ------------------------------------------------------------------

    def _default_invoke_fn(self) -> Any:
        from advtest_ext.adapters.studio.ui_mode import ui_mode_invoke

        return ui_mode_invoke

    async def _browser_create_app(self) -> str:
        """Mint a host app through the logged-in BROWSER. SEEDED — the Studio
        create URL is not confirmed; set ADVTEST_STUDIO_CREATE_URL to override.
        Returns the opened app's URL (used verbatim as the app_id)."""
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()
        create_url = os.environ.get(
            "ADVTEST_STUDIO_CREATE_URL", "https://workspace.google.com/studio/create"
        )
        await page.goto(create_url)
        # Without a confirmed id-in-URL shape, reuse the landed URL as the app
        # handle; the invoker treats an http(s) app_id as a full URL.
        return str(getattr(page, "url", "") or create_url)

    async def _create_app(self) -> str:
        if self._app_factory is not None:
            return str(self._app_factory())
        raise RuntimeError(
            "no Workspace Studio API to mint an app: pass app_id (or a full app "
            "URL as app_id), set browser_mint=True, or inject app_factory"
        )

    async def _delete_app(self, app_id: str) -> None:
        if self._app_deleter is not None:
            result = self._app_deleter(app_id)
            if inspect.isawaitable(result):
                await result
            return
        # No API to delete; a browser-minted / caller-supplied app is left as-is.
        return


__all__ = ["GeminiStudioMultiTurnTarget"]
