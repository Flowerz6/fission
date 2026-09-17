"""A multi-turn, live-conversation ``DocumentTarget`` for the Gemini
Slides side panel.

Every other :class:`DocumentTarget` in the tree is single-turn:
``SlidesTarget`` and the stub both declare ``multi_turn=False`` and
treat each ``invoke`` as an isolated render→ask→teardown. That's the
right shape for *direct* IPI (open a poisoned deck, ask one question).

The inbox-grounding finding (Google Red Team report) is different:
the exploit *accumulates* across a conversation — turn 1 grounds and
seeds the persona, turn 2 confirms it, the last turn cashes it in.
Reproducing that needs a target that keeps ONE warm Gemini side-panel
conversation open across many invocations.

The key realisation that makes this composable with the existing
``CrescendoAttack`` *unchanged*: for a LIVE side panel the conversation
memory lives server-side, in the warm page's session — not in
``conversation_history``. So this target only has to:

  * open the side panel ONCE (the first ``invoke`` resets to a fresh
    conversation; every later ``invoke`` continues it), and
  * keep reusing the same Playwright page.

``CrescendoAttack`` calls ``render_document`` then ``invoke`` every
turn. We make ``render_document`` idempotent (one scratch deck for the
whole run) and let ``invoke`` decide reset-vs-continue from a turn
counter. Crescendo's attacker LLM thus drives a real multi-turn
conversation with no changes to the attack, the orchestrator, or the
``AttackAttempt`` shape.

The blank scratch deck is only a host surface for the panel — nothing
adversarial goes in it. The injection arrives out-of-band (in the deck
whose speaker notes carry the payload, or the inbox the side panel
grounds on).
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

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.document import AbstractDocument


class GeminiSlidesMultiTurnTarget(DocumentTarget):
    """Live multi-turn Gemini Slides side-panel target.

    All external collaborators are injectable so the flow is unit-
    testable without Playwright, Chrome, or the Workspace APIs:

    * ``session`` — a ``UiModeSession``-shaped object (``ensure_page``,
      ``close``). Default: lazily constructed on the resolved profile.
    * ``invoke_fn`` — the side-panel driver. Default:
      ``slides.ui_mode.ui_mode_invoke``. Signature must accept
      ``deck_id, user_prompt, trace_id, output_dir, external_page,
      reset_conversation`` and return a :class:`Response`.
    * ``deck_factory`` — ``() -> deck_id`` for the scratch deck.
      Default: create a blank deck via the Slides API.
    * ``deck_deleter`` — ``(deck_id) -> None``. Default: Drive delete.

    Pass ``deck_id`` to reuse an existing blank deck (skips the Slides
    API entirely, and that deck is never deleted on teardown).

    Pass ``deck_ids`` (a list) to ground each tree NODE on a different
    EXISTING deck — the target rotates to the next id on every ``reset()``
    (so node n0 lands on deck_ids[0], n1 on deck_ids[1], wrapping around).
    Use this to run the payload directly in pre-existing Drive decks: the
    panel opens ON the deck, so its content (body + speaker notes, and any
    planted payload) is the grounding source. Caller-supplied decks are
    never deleted.
    """

    def __init__(
        self,
        *,
        output_dir: Path | str,
        profile_dir: Path | None = None,
        deck_id: str | None = None,
        deck_ids: list[str] | None = None,
        browser_mint: bool = False,
        trace_prefix: str | None = None,
        session: Any = None,
        invoke_fn: Any = None,
        deck_factory: Any = None,
        deck_deleter: Any = None,
    ) -> None:
        super().__init__()
        self._output_dir = Path(output_dir)
        self._profile_dir = profile_dir
        self._deck_id = deck_id
        # Rotating pool of existing decks (one per node). Takes precedence
        # over a single ``deck_id`` when non-empty.
        self._deck_ids = [str(d) for d in deck_ids] if deck_ids else None
        self._deck_cursor = 0
        # Browser-mint: open ONE blank host deck through the logged-in
        # browser (no Slides API / OAuth, no caller-supplied id) and reuse
        # it across nodes. Used for retrieval runs where the deck is just a
        # host surface.
        self._browser_mint = browser_mint
        self._browser_deck_id: str | None = None
        self._trace_prefix = trace_prefix or f"crescendo-{uuid.uuid4().hex[:6]}"
        self._session = session
        # We only close a session we created ourselves; an injected one
        # belongs to the caller.
        self._owns_session = session is None
        self._invoke_fn = invoke_fn
        self._deck_factory = deck_factory
        self._deck_deleter = deck_deleter
        self._ctx: NativeContext | None = None
        self._owns_deck = False
        self._turn = 0
        # Monotonic conversation id keeps screenshots and intercepted traffic
        # unique when reset() starts another tree node.
        self._conversation = 1
        # Every scratch deck we mint across all nodes, deleted at close().
        self._created_decks: list[str] = []

    @property
    def context(self) -> NativeContext | None:
        """The cached scratch-deck context (``None`` before the first
        ``render_document``). Lets a driver call ``teardown`` without
        threading the context back out of the attack loop."""
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
        """Idempotent: create (or adopt) ONE scratch deck and cache it.

        Crescendo calls this every turn; we hand back the same context
        so the whole escalation runs in one conversation on one deck.
        ``doc`` is ignored — it's a blank control surface; the payload
        rides in the deck the side panel grounds on (its speaker notes)
        or the inbox.
        """
        del doc
        if self._ctx is not None:
            return self._ctx
        if self._deck_ids:
            # Rotate to the current node's existing deck (never deleted).
            deck_id = self._deck_ids[self._deck_cursor % len(self._deck_ids)]
            self._owns_deck = False
        elif self._deck_id:
            deck_id = self._deck_id
            self._owns_deck = False
        elif self._browser_mint:
            # Mint one blank host via the browser, then reuse it (no API).
            if self._browser_deck_id is None:
                self._browser_deck_id = await self._browser_create_deck()
            deck_id = self._browser_deck_id
            self._owns_deck = False  # leave the single blank host in Drive
        else:
            deck_id = await self._create_deck()
            self._owns_deck = True
            self._created_decks.append(deck_id)
        self._ctx = NativeContext(context_id=deck_id, rendered={"deck_id": deck_id})
        return self._ctx

    async def invoke(self, context: NativeContext, user_prompt: str) -> Response:
        """Send one turn to the live side panel.

        The FIRST invoke resets to a fresh conversation; later invokes
        continue it on the same warm page, so the persona accumulates.
        """
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()

        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        deck_id = str(rendered.get("deck_id", ""))
        if not deck_id:
            raise RuntimeError("render_document did not record a deck_id on the context")

        reset = self._turn == 0
        trace_id = f"{self._trace_prefix}-c{self._conversation}-t{self._turn + 1}"
        invoke_fn = self._invoke_fn or self._default_invoke_fn()
        response: Response = await invoke_fn(
            deck_id=deck_id,
            user_prompt=user_prompt,
            trace_id=trace_id,
            output_dir=self._output_dir,
            external_page=page,
            reset_conversation=reset,
        )
        self._turn += 1
        # Stamp turn provenance for downstream scoring / the dashboard.
        try:
            response.raw["turn"] = self._turn
            response.raw["is_multi_turn"] = True
        except Exception:
            pass
        return response

    async def teardown(self, context: NativeContext) -> None:
        """Delete the scratch deck, but only if WE created it. A
        caller-supplied ``deck_id`` is left untouched."""
        if not self._owns_deck:
            return
        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        deck_id = rendered.get("deck_id")
        if not deck_id:
            return
        await self._delete_deck(str(deck_id))
        if str(deck_id) in self._created_decks:
            self._created_decks.remove(str(deck_id))
        self._owns_deck = False
        self._ctx = None

    def reset(self) -> None:
        """Start a FRESH conversation AND a fresh scratch deck on the next
        invoke, keeping the warm Chrome session.

        The orchestrator builds one target per run, so a multi-conversation
        attack (TapChainAttack / TapCrescendoAttack) calls ``reset()``
        between nodes. The turn counter goes back to 0 (next ``invoke``
        passes ``reset_conversation=True`` → the side panel clears), AND the
        cached deck context is dropped so the next ``render_document`` mints
        a brand-new blank deck. That isolation matters: otherwise the
        previous node's deck edits — and any unresolved "Resolve Gemini
        suggestions" lock — carry over and contaminate or block the next
        node. A caller-supplied ``deck_id`` can't be re-minted, so it's
        reused as-is (carryover caveat — prefer auto-created decks for
        multi-node runs)."""
        self._turn = 0
        self._conversation += 1
        if self._deck_ids:
            # Advance to the next existing deck for the next node.
            self._deck_cursor += 1
            self._ctx = None
            self._owns_deck = False
        elif self._deck_id is None and not self._browser_mint:
            self._ctx = None
            self._owns_deck = False
        # Single pinned deck_id OR a browser-minted host: keep the cached
        # ctx (same host across nodes); the conversation still resets because
        # _turn is back to 0, so the next invoke passes reset_conversation.

    async def close(self) -> None:
        """Run-level teardown: delete EVERY scratch deck we minted across all
        nodes, and close the Chrome session we created. The orchestrator
        never calls ``teardown`` (it discards NativeContexts), so deck
        cleanup lives here. Idempotent; leaves caller-supplied decks /
        sessions alone."""
        for deck_id in self._created_decks:
            try:
                await self._delete_deck(deck_id)
            except Exception:
                pass
        self._created_decks = []
        self._owns_deck = False
        self._ctx = None
        if self._session is not None and self._owns_session:
            try:
                await self._session.close()
            finally:
                self._session = None

    def bind_carrier(self, **bindings: Any) -> None:
        deck_id = bindings.get("deck_id")
        if not isinstance(deck_id, str) or not deck_id:
            raise ValueError("Slides carrier binding requires deck_id")
        self._deck_id = deck_id
        self._deck_ids = None
        self._ctx = None
        self._owns_deck = False
        self.reset()

    async def cleanup_bound_carrier(self, **bindings: Any) -> bool:
        deck_id = bindings.get("deck_id")
        if not isinstance(deck_id, str) or not deck_id or self._deck_deleter is None:
            return False
        result = self._deck_deleter(deck_id)
        if inspect.isawaitable(result):
            await result
        return True

    def capabilities(self) -> DocumentCapabilities:
        # The whole point of this target: it CAN hold a conversation.
        return DocumentCapabilities(multi_turn=True)

    def supported_techniques(self) -> set[str]:
        # The scratch deck carries no injection (the payload is in the
        # deck's speaker notes or the inbox), so deck-hiding techniques
        # don't apply here. ``{"*"}`` keeps it from being filtered out by
        # capability-gating for attacks that, like Crescendo, inject
        # nothing into the deck.
        return {"*"}

    # ------------------------------------------------------------------
    # Default (real) collaborators — overridden in tests via __init__
    # ------------------------------------------------------------------

    def _default_invoke_fn(self) -> Any:
        from advtest_ext.adapters.slides.ui_mode import ui_mode_invoke

        return ui_mode_invoke

    async def _browser_create_deck(self) -> str:
        """Mint a blank host deck through the logged-in BROWSER — no Slides
        API, no OAuth, no caller-supplied id. Navigates to the Slides
        ``create`` endpoint, which redirects to a real
        ``/presentation/d/<id>/edit``, and captures that id from the URL.
        Reused across nodes as the host surface; left in Drive on teardown
        (a single blank untitled deck)."""
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()
        await page.goto("https://docs.google.com/presentation/create")
        # The create endpoint redirects to the new deck; poll the URL for
        # its id.
        for _ in range(40):  # ~12s
            m = re.search(r"/presentation/d/([A-Za-z0-9_-]{20,})", getattr(page, "url", "") or "")
            if m:
                return m.group(1)
            await asyncio.sleep(0.3)
        raise RuntimeError(
            "browser-mint: no /presentation/d/<id> URL after navigating to "
            "/presentation/create — is the Playwright profile logged into Google?"
        )

    async def _create_deck(self) -> str:
        if self._deck_factory is not None:
            return str(self._deck_factory())
        from advtest_ext.adapters._auth import get_service
        from advtest_ext.adapters.slides.target import SLIDES_SCOPES

        slides = get_service("slides", "v1", SLIDES_SCOPES)
        created = (
            slides.presentations()
            .create(body={"title": f"Scratch {uuid.uuid4().hex[:6]}"})
            .execute()
        )
        return str(created["presentationId"])

    async def _delete_deck(self, deck_id: str) -> None:
        if self._deck_deleter is not None:
            self._deck_deleter(deck_id)
            return
        from advtest_ext.adapters._auth import get_service
        from advtest_ext.adapters.slides.target import SLIDES_SCOPES

        drive = get_service("drive", "v3", SLIDES_SCOPES)
        drive.files().delete(fileId=deck_id).execute()


__all__ = ["GeminiSlidesMultiTurnTarget"]
