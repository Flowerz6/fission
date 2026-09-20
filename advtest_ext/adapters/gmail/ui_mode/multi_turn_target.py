"""A multi-turn, live-conversation ``DocumentTarget`` for the Gemini
Gmail side panel — the Gmail analogue of
:class:`~advtest_ext.adapters.google_docs.ui_mode.multi_turn_target.GeminiDocsMultiTurnTarget`.

The single-turn :class:`~advtest_ext.adapters.gmail.target.GmailTarget` is a
provider-coupled ``DocumentTarget`` (it holds a ``gemini_provider`` and defaults
to ``api_mode``). That's the wrong shape for a UI-mode tap_crescendo run, which
needs a target that holds ONE warm side-panel conversation across turns and
needs no Gemini provider at all — the side panel IS the model, exactly like the
Docs multiturn target.

This target grounds the side panel on the victim's inbox (or one planted
thread). The bait email(s) are delivered out-of-band by the setup steps
(``gmail.send_email`` as the attacker account); ``thread_id`` is bound from that
staging via ``bind_setup_target_options``. When no specific thread is bound the
panel opens the inbox, so it grounds across recent mail — the shape retrieval /
inbox-summary exfil guides need.
"""

from __future__ import annotations

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

# thread_id values that mean "open the inbox, not a specific thread". The gmail
# ui_mode invoker treats an empty thread_id as the inbox URL.
_INBOX_ALIASES = frozenset({"", "inbox", "none"})


def _normalise_thread(thread_id: str | None) -> str:
    """Map inbox aliases to the empty string the invoker uses for the inbox."""
    if thread_id is None:
        return ""
    return "" if thread_id.strip().lower() in _INBOX_ALIASES else thread_id


class GeminiGmailMultiTurnTarget(DocumentTarget):
    """Live multi-turn Gemini Gmail side-panel target (provider-free).

    All external collaborators are injectable so the flow is unit-testable
    without Playwright / Chrome:

    * ``session`` — a ``UiModeSession``-shaped object (``ensure_page``,
      ``close``). Default: lazily constructed on the resolved profile.
    * ``invoke_fn`` — the side-panel driver. Default:
      ``gmail.ui_mode.ui_mode_invoke``. Signature must accept
      ``thread_id, user_prompt, trace_id, output_dir, external_page,
      continue_conversation`` and return a :class:`Response`.

    ``thread_id`` selects what the panel grounds on: a specific planted thread,
    or an inbox alias (``""`` / ``"inbox"``) to ground on recent mail. It is
    bound from staging (``bind_setup_target_options`` maps the planted
    ``thread_id`` into target options) or passed explicitly.
    """

    def __init__(
        self,
        *,
        output_dir: Path | str,
        profile_dir: Path | None = None,
        thread_id: str | None = None,
        trace_prefix: str | None = None,
        session: Any = None,
        invoke_fn: Any = None,
    ) -> None:
        super().__init__()
        self._output_dir = Path(output_dir)
        self._profile_dir = profile_dir
        self._thread_id = _normalise_thread(thread_id)
        self._trace_prefix = trace_prefix or f"gmail-crescendo-{uuid.uuid4().hex[:6]}"
        self._session = session
        self._owns_session = session is None
        self._invoke_fn = invoke_fn
        self._ctx: NativeContext | None = None
        self._turn = 0
        self._conversation = 1

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
        """Idempotent: the grounding surface is the inbox/thread, not a minted
        doc, so cache one context for the whole conversation. ``doc`` is ignored
        — the payload rides in the inbox the side panel grounds on."""
        del doc
        if self._ctx is None:
            self._ctx = NativeContext(
                context_id=self._thread_id or "inbox",
                rendered={"thread_id": self._thread_id},
            )
        return self._ctx

    async def invoke(self, context: NativeContext, user_prompt: str) -> Response:
        """Send one turn to the live Gmail side panel. The FIRST invoke starts a
        fresh conversation; later invokes continue it on the same warm page."""
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()

        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        thread_id = _normalise_thread(str(rendered.get("thread_id", "")))

        trace_id = f"{self._trace_prefix}-c{self._conversation}-t{self._turn + 1}"
        invoke_fn = self._invoke_fn or self._default_invoke_fn()
        response: Response = await invoke_fn(
            thread_id=thread_id,
            user_prompt=user_prompt,
            trace_id=trace_id,
            output_dir=self._output_dir,
            external_page=page,
            continue_conversation=self._turn > 0,
        )
        self._turn += 1
        try:
            response.raw["turn"] = self._turn
            response.raw["is_multi_turn"] = True
        except Exception:
            pass
        return response

    async def teardown(self, context: NativeContext) -> None:
        # Nothing to delete — the target mints no artifact (the inbox/thread is
        # planted and torn down by the setup/staging layer).
        del context

    def reset(self) -> None:
        """Start a FRESH conversation on the next invoke, keeping the warm
        Chrome session. TapCrescendo calls this between tree nodes."""
        self._turn = 0
        self._conversation += 1

    async def close(self) -> None:
        if self._session is not None and self._owns_session:
            try:
                await self._session.close()
            finally:
                self._session = None
        self._ctx = None

    def bind_carrier(self, **bindings: Any) -> None:
        thread_id = bindings.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("Gmail carrier binding requires thread_id")
        self._thread_id = _normalise_thread(thread_id)
        self._ctx = None
        self.reset()

    def capabilities(self) -> DocumentCapabilities:
        return DocumentCapabilities(multi_turn=True)

    def supported_techniques(self) -> set[str]:
        # The injection rides in the inbox, not a doc-hiding technique, so keep
        # this from being filtered out by capability-gating (mirrors Docs).
        return {"*"}

    # ------------------------------------------------------------------
    # Default (real) collaborator — overridden in tests via __init__
    # ------------------------------------------------------------------

    def _default_invoke_fn(self) -> Any:
        from advtest_ext.adapters.gmail.ui_mode import ui_mode_invoke

        return ui_mode_invoke


__all__ = ["GeminiGmailMultiTurnTarget"]
