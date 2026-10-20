"""A multi-turn, live-conversation ``DocumentTarget`` for the Gemini
Docs side panel.

Every other :class:`DocumentTarget` in the tree is single-turn:
``GoogleDocsTarget`` and the stub both declare ``multi_turn=False`` and
treat each ``invoke`` as an isolated render→ask→teardown. That's the
right shape for *direct* IPI (open a poisoned doc, ask one question).

The V1 inbox-grounding finding (Google Red Team report) is different:
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
turn. We make ``render_document`` idempotent (one scratch doc for the
whole run) and let ``invoke`` decide reset-vs-continue from a turn
counter. Crescendo's attacker LLM thus drives a real multi-turn
conversation with no changes to the attack, the orchestrator, or the
``AttackAttempt`` shape.

The blank scratch doc is only a host surface for the panel — nothing
adversarial goes in it. The injection arrives out-of-band, in the
inbox the side panel grounds on (see ``scripts/quillfeather_send.py``).
"""

from __future__ import annotations

import asyncio
import inspect
import os
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


class GeminiDocsMultiTurnTarget(DocumentTarget):
    """Live multi-turn Gemini Docs side-panel target.

    All external collaborators are injectable so the flow is unit-
    testable without Playwright, Chrome, or the Workspace APIs:

    * ``session`` — a ``UiModeSession``-shaped object (``ensure_page``,
      ``close``). Default: lazily constructed on the resolved profile.
    * ``invoke_fn`` — the side-panel driver. Default:
      ``google_docs.ui_mode.ui_mode_invoke``. Signature must accept
      ``document_id, user_prompt, trace_id, output_dir, external_page,
      reset_conversation`` and return a :class:`Response`.
    * ``doc_factory`` — ``() -> document_id`` for the scratch doc.
      Default: create a blank Doc via the Docs API.
    * ``doc_deleter`` — ``(document_id) -> None``. Default: Drive delete.

    Pass ``doc_id`` to reuse an existing blank doc (skips the Docs API
    entirely, and that doc is never deleted on teardown).

    Pass ``doc_ids`` (a list) to ground each tree NODE on a different
    EXISTING doc — the target rotates to the next id on every ``reset()``
    (so node n0 lands on doc_ids[0], n1 on doc_ids[1], wrapping around).
    Use this to run the payload directly in pre-existing Drive docs: the
    panel opens ON the doc, so its content (and any planted payload) is the
    grounding source. Caller-supplied docs are never deleted.
    """

    def __init__(
        self,
        *,
        output_dir: Path | str,
        profile_dir: Path | None = None,
        doc_id: str | None = None,
        doc_ids: list[str] | None = None,
        browser_mint: bool = False,
        trace_prefix: str | None = None,
        session: Any = None,
        invoke_fn: Any = None,
        doc_factory: Any = None,
        doc_deleter: Any = None,
        accept_suggestions: bool = False,
        confirm_actions: bool = False,
    ) -> None:
        super().__init__()
        self._output_dir = Path(output_dir)
        self._profile_dir = profile_dir
        self._doc_id = doc_id
        # Rotating pool of existing docs (one per node). Takes precedence
        # over a single ``doc_id`` when non-empty.
        self._doc_ids = [str(d) for d in doc_ids] if doc_ids else None
        self._doc_cursor = 0
        # Browser-mint: open ONE blank host doc through the logged-in browser
        # (no Docs API / OAuth, no caller-supplied id) and reuse it across
        # nodes. Used for retrieval runs where the doc is just a host surface.
        self._browser_mint = browser_mint
        self._browser_doc_id: str | None = None
        self._trace_prefix = trace_prefix or f"crescendo-{uuid.uuid4().hex[:6]}"
        self._session = session
        # We only close a session we created ourselves; an injected one
        # belongs to the caller.
        self._owns_session = session is None
        self._invoke_fn = invoke_fn
        self._doc_factory = doc_factory
        self._doc_deleter = doc_deleter
        # When True, after each turn play the human-in-the-loop clicks a
        # write-back exfil needs: accept Gemini's suggested edit (commit the
        # proposed text into the doc body) and/or clear an action-permission
        # gate. Off by default so read-only/side-panel guides are unaffected.
        self._accept_suggestions = accept_suggestions
        self._confirm_actions = confirm_actions
        self._ctx: NativeContext | None = None
        self._owns_doc = False
        self._turn = 0
        # Monotonic conversation id keeps screenshots and intercepted traffic
        # unique when reset() starts another tree node.
        self._conversation = 1
        # Every scratch doc we mint across all nodes, deleted at close().
        self._created_docs: list[str] = []

    @property
    def context(self) -> NativeContext | None:
        """The cached scratch-doc context (``None`` before the first
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
        """Idempotent: create (or adopt) ONE scratch doc and cache it.

        Crescendo calls this every turn; we hand back the same context
        so the whole escalation runs in one conversation on one doc.
        ``doc`` is ignored — it's a blank control surface; the payload
        rides in the inbox the side panel grounds on.
        """
        del doc
        if self._ctx is not None:
            return self._ctx
        if self._doc_ids:
            # Rotate to the current node's existing doc (never deleted).
            document_id = self._doc_ids[self._doc_cursor % len(self._doc_ids)]
            self._owns_doc = False
        elif self._doc_id:
            document_id = self._doc_id
            self._owns_doc = False
        elif self._browser_mint:
            # Mint one blank host via the browser, then reuse it (no API).
            if self._browser_doc_id is None:
                self._browser_doc_id = await self._browser_create_doc()
            document_id = self._browser_doc_id
            self._owns_doc = False  # leave the single blank host in Drive
        else:
            document_id = await self._create_doc()
            self._owns_doc = True
            self._created_docs.append(document_id)
        self._ctx = NativeContext(context_id=document_id, rendered={"document_id": document_id})
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
        document_id = str(rendered.get("document_id", ""))
        if not document_id:
            raise RuntimeError("render_document did not record a document_id on the context")

        reset = self._turn == 0
        trace_id = f"{self._trace_prefix}-c{self._conversation}-t{self._turn + 1}"
        invoke_fn = self._invoke_fn or self._default_invoke_fn()
        invoke_kwargs: dict[str, Any] = {
            "document_id": document_id,
            "user_prompt": user_prompt,
            "trace_id": trace_id,
            "output_dir": self._output_dir,
            "external_page": page,
            "reset_conversation": reset,
        }
        # Only forward the write-back flags when opted in, so injected test
        # doubles with a fixed signature keep working unchanged.
        if self._accept_suggestions:
            invoke_kwargs["accept_suggestions"] = True
        if self._confirm_actions:
            invoke_kwargs["confirm_actions"] = True
        response: Response = await invoke_fn(**invoke_kwargs)
        self._turn += 1
        # Stamp turn provenance for downstream scoring / the dashboard.
        try:
            response.raw["turn"] = self._turn
            response.raw["is_multi_turn"] = True
        except Exception:
            pass
        return response

    async def teardown(self, context: NativeContext) -> None:
        """Delete the scratch doc, but only if WE created it. A
        caller-supplied ``doc_id`` is left untouched."""
        if not self._owns_doc:
            return
        rendered = context.rendered if isinstance(context.rendered, dict) else {}
        document_id = rendered.get("document_id")
        if not document_id:
            return
        await self._delete_doc(str(document_id))
        if str(document_id) in self._created_docs:
            self._created_docs.remove(str(document_id))
        self._owns_doc = False
        self._ctx = None

    def reset(self) -> None:
        """Start a FRESH conversation AND a fresh scratch doc on the next
        invoke, keeping the warm Chrome session.

        The orchestrator builds one target per run, so a multi-conversation
        attack (TapChainAttack / TapCrescendoAttack) calls ``reset()``
        between nodes. The turn counter goes back to 0 (next ``invoke``
        passes ``reset_conversation=True`` → the side panel clears), AND the
        cached doc context is dropped so the next ``render_document`` mints
        a brand-new blank doc. That isolation matters: otherwise the
        previous node's doc edits — and any unresolved "Resolve Gemini
        suggestions" lock — carry over and contaminate or block the next
        node. A caller-supplied ``doc_id`` can't be re-minted, so it's
        reused as-is (carryover caveat — prefer auto-created docs for
        multi-node runs)."""
        self._turn = 0
        self._conversation += 1
        if self._doc_ids:
            # Advance to the next existing doc for the next node.
            self._doc_cursor += 1
            self._ctx = None
            self._owns_doc = False
        elif self._doc_id is None and not self._browser_mint:
            self._ctx = None
            self._owns_doc = False
        # Single pinned doc_id OR a browser-minted host: keep the cached ctx
        # (same host across nodes); the conversation still resets because
        # _turn is back to 0, so the next invoke passes reset_conversation.

    async def close(self) -> None:
        """Run-level teardown: delete EVERY scratch doc we minted across all
        nodes, and close the Chrome session we created. The orchestrator
        never calls ``teardown`` (it discards NativeContexts), so doc
        cleanup lives here. Idempotent; leaves caller-supplied docs /
        sessions alone."""
        for document_id in self._created_docs:
            try:
                await self._delete_doc(document_id)
            except Exception:
                pass
        self._created_docs = []
        self._owns_doc = False
        self._ctx = None
        if self._session is not None and self._owns_session:
            try:
                await self._session.close()
            finally:
                self._session = None

    def bind_carrier(self, **bindings: Any) -> None:
        doc_id = bindings.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError("Docs carrier binding requires doc_id")
        self._doc_id = doc_id
        self._doc_ids = None
        self._ctx = None
        self._owns_doc = False
        self.reset()

    async def cleanup_bound_carrier(self, **bindings: Any) -> bool:
        doc_id = bindings.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id or self._doc_deleter is None:
            return False
        result = self._doc_deleter(doc_id)
        if inspect.isawaitable(result):
            await result
        return True

    def capabilities(self) -> DocumentCapabilities:
        # The whole point of this target: it CAN hold a conversation.
        return DocumentCapabilities(multi_turn=True)

    def supported_techniques(self) -> set[str]:
        # The scratch doc carries no injection (the payload is in the
        # inbox), so doc-hiding techniques don't apply here. ``{"*"}``
        # keeps it from being filtered out by capability-gating for
        # attacks that, like Crescendo, inject nothing into the doc.
        return {"*"}

    # ------------------------------------------------------------------
    # Default (real) collaborators — overridden in tests via __init__
    # ------------------------------------------------------------------

    def _default_invoke_fn(self) -> Any:
        from advtest_ext.adapters.google_docs.ui_mode import ui_mode_invoke

        return ui_mode_invoke

    async def _browser_create_doc(self) -> str:
        """Mint a blank host doc through the logged-in BROWSER — no Docs API,
        no OAuth, no caller-supplied id. Navigates to the Docs ``create``
        endpoint, which redirects to a real ``/document/d/<id>/edit``, and
        captures that id from the URL. Reused across nodes as the host
        surface; left in Drive on teardown (a single blank untitled doc)."""
        if self._session is None:
            await self.setup()
        assert self._session is not None
        page = await self._session.ensure_page()
        # Account-scoped create URL. A bare /document/create returns Google's
        # "400. That's an error" page when the Chrome profile has MORE THAN ONE
        # Google account signed in — the account-less URL is ambiguous, so Google
        # rejects it. Scoping to /u/<n>/ picks a specific signed-in account and
        # also works for single-account profiles. Override the index via
        # ADVTEST_GOOGLE_ACCOUNT_INDEX when the victim isn't the first account.
        idx = (os.environ.get("ADVTEST_GOOGLE_ACCOUNT_INDEX", "0") or "0").strip()
        if not idx.isdigit():
            idx = "0"
        await page.goto(f"https://docs.google.com/document/u/{idx}/create")
        # The create endpoint redirects to the new doc; poll the URL for its id.
        for _ in range(40):  # ~12s
            m = re.search(r"/document/d/([A-Za-z0-9_-]{20,})", getattr(page, "url", "") or "")
            if m:
                return m.group(1)
            await asyncio.sleep(0.3)
        raise RuntimeError(
            "browser-mint: no /document/d/<id> URL after navigating to "
            f"/document/u/{idx}/create. Either the Playwright profile is not "
            "logged into Google, or the victim is a different account index — "
            "set ADVTEST_GOOGLE_ACCOUNT_INDEX to the right /u/<n> (0, 1, 2, …)."
        )

    async def _create_doc(self) -> str:
        if self._doc_factory is not None:
            return str(self._doc_factory())
        from advtest_ext.adapters._auth import DOCS_SCOPES, get_service

        docs = get_service("docs", "v1", DOCS_SCOPES)
        created = (
            docs.documents().create(body={"title": f"Scratch {uuid.uuid4().hex[:6]}"}).execute()
        )
        return str(created["documentId"])

    async def _delete_doc(self, document_id: str) -> None:
        if self._doc_deleter is not None:
            self._doc_deleter(document_id)
            return
        from advtest_ext.adapters._auth import DOCS_SCOPES, get_service

        drive = get_service("drive", "v3", DOCS_SCOPES)
        drive.files().delete(fileId=document_id).execute()


__all__ = ["GeminiDocsMultiTurnTarget"]
