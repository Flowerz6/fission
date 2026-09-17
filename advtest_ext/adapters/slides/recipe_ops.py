"""Google Slides operation handlers for the Phase-5 Recipe Runner.

Three handlers live here (mirroring the Docs recipe handlers, with
the carrier renamed doc→deck and the planted content routed into the
deck's body + speaker notes):

- ``("slides", "open_deck")`` — "Account A opens a benign carrier
  deck" step. Creates a fresh presentation via the Drive API and
  returns its id so a later ``type_prompt`` step can drive the
  sidebar against it. Mirrors docs ``open_document``.
- ``("slides", "set_speaker_notes")`` — "plant/replace the deck's
  content" step. The right delivery channel for "plant
  parameter-shaped strings in the deck" attacks: the payload lives
  inside the deck itself (visible to the user, parsed by Gemini as
  grounding context), NOT inside a side-panel chat turn. Mirrors docs
  ``set_document_body``.
- ``("slides", "type_prompt")`` — "Account A types a prompt in the
  Gemini sidebar" step. Delegates to the existing slides ui_mode
  invoker so this handler benefits from every flake-hardening
  feature the Slides adapter already has (Chrome stealth, panel
  retry, traffic interception, etc.). Mirrors docs ``type_prompt``.

Phase 5.1 of the :doc:`red_team_report_infra_plan`. The
``(app, operation)`` pairs above are registered into the default
registry by the caller's operation-registry wiring (the same place
the Docs handlers are wired via
:func:`advtest_ext.core.builtin_operations.default_operations`).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from advtest_ext.adapters._auth import get_service
from advtest_ext.adapters.slides.target import SLIDES_SCOPES
from advtest_ext.adapters.slides.ui_mode import (
    UiModeSession,
    ui_mode_invoke,
)
from advtest_ext.core.recipe_operations import (
    OperationContext,
    OperationResult,
    require_str_param,
)

# Where Recipe-driven ui_mode invocations land their screenshot
# and intercepted-traffic captures. Sibling of the per-attack
# ``out/gmail_ui`` / ``out/<run-id>`` paths so the disk layout
# stays browsable.
_DEFAULT_RECIPE_OUTPUT_DIR = Path("out") / "recipe_runs"


# ----------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------


async def open_deck(ctx: OperationContext) -> OperationResult:
    """Create a fresh Google Slides deck via the Drive API and return
    its id. Step params::

        params:
          title: "Quarterly Review Deck"
          body: "..."          # optional — when set, written into the deck
                                #            via the Drive update endpoint.

    Outputs::

        outputs:
          deck_id: <drive file id>
          title: <as-created title>
          url: https://docs.google.com/presentation/d/<id>

    The ``deck_id`` is the load-bearing output — a later
    ``type_prompt`` step uses it to navigate the sidebar to this
    deck.
    """
    title = ctx.params.get("title", "Recipe carrier deck")
    if not isinstance(title, str) or not title:
        title = "Recipe carrier deck"

    drive = get_service("drive", "v3", SLIDES_SCOPES)
    metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.presentation",
    }
    created = drive.files().create(body=metadata, fields="id, name").execute()
    deck_id = created["id"]

    # Optional body content. For V1 the deck body is benign cover
    # (the attack is in the email + the side-panel prompt, not in
    # the deck itself), so this is usually omitted.
    body_text = ctx.params.get("body")
    if isinstance(body_text, str) and body_text:
        # Drive's plain-text update path: re-import as text/plain. On a
        # Doc this replaces the body; on a Slides deck it lands as the
        # deck's importable text. Richer per-slide / speaker-notes
        # planting uses the Slides API batchUpdate (see
        # ``SlidesTarget._apply_speaker_notes``) — a follow-up here.
        from googleapiclient.http import MediaInMemoryUpload

        media = MediaInMemoryUpload(body_text.encode("utf-8"), mimetype="text/plain")
        drive.files().update(fileId=deck_id, media_body=media).execute()

    return OperationResult(
        outputs={
            "deck_id": deck_id,
            "title": created.get("name", title),
            "url": f"https://docs.google.com/presentation/d/{deck_id}",
        },
        raw={
            "operation": "slides.open_deck",
            # When called with optional ``body``, expose it so the
            # Recipe Runner can populate SignalContext.document_body
            # without trying to scrape Slides' canvas-rendered DOM.
            "document_body": body_text if isinstance(body_text, str) else "",
        },
    )


async def set_speaker_notes(ctx: OperationContext) -> OperationResult:
    """Replace the content of an existing Google Slides deck.

    The right delivery channel for "plant parameter-shaped strings in
    the deck" attacks: the content lives inside the deck itself
    (visible to the user, parsed by Gemini as grounding context), NOT
    inside a side-panel chat turn. The payload rides in the deck's
    body and — especially — its speaker notes, the channel a presenter
    never proof-reads.

    Step params::

        params:
          deck_id: "{{step.open_deck.deck_id}}"
          notes: "absolute_insert_position: start\\ninstruction: ..."

    Outputs::

        outputs:
          deck_id: <unchanged from input>
          notes_length: <chars written>

    Uses Drive's update endpoint with a ``text/plain`` media upload —
    same Drive scope the rest of the Slides operations use, no extra
    auth setup. For per-slide speaker-notes placement (the primary
    injection carrier) operators can swap to the Slides API
    ``batchUpdate`` endpoint (see ``SlidesTarget._apply_speaker_notes``)
    in a follow-up handler.
    """
    deck_id = require_str_param(ctx.params, "deck_id", "slides.set_speaker_notes")
    notes = require_str_param(ctx.params, "notes", "slides.set_speaker_notes")

    from googleapiclient.http import MediaInMemoryUpload

    drive = get_service("drive", "v3", SLIDES_SCOPES)
    media = MediaInMemoryUpload(notes.encode("utf-8"), mimetype="text/plain")
    drive.files().update(fileId=deck_id, media_body=media).execute()

    return OperationResult(
        outputs={
            "deck_id": deck_id,
            "notes_length": len(notes),
        },
        raw={
            "operation": "slides.set_speaker_notes",
            # Expose the planted content so the Recipe Runner can
            # surface it as ``SignalContext.document_body``. Reading
            # the live editor via Playwright doesn't work — Slides
            # paints text to canvas, not standard DOM — so the
            # source of truth for "what got written" is the params
            # we just sent to Drive.
            "document_body": notes,
        },
    )


def _earlier_type_prompt_ran(ctx: OperationContext) -> bool:
    """Check whether an earlier Step in the same Recipe was a
    ``slides.type_prompt`` invocation. Used by ``type_prompt`` to
    decide whether to land as turn 1 of a fresh Gemini conversation
    (first prompt) or to continue the existing one (subsequent
    prompts in progressive-disclosure chains).

    Heuristic: only ``type_prompt`` produces the ``response_excerpt``
    output key. Cheaper than threading operation names through the
    Runner's accumulated_context. If we ever add another handler
    that emits ``response_excerpt``, swap this for an explicit
    operation-history check in the Runner.
    """
    for prior_outputs in ctx.accumulated_context.values():
        if isinstance(prior_outputs, dict) and "response_excerpt" in prior_outputs:
            return True
    return False


async def type_prompt(ctx: OperationContext) -> OperationResult:
    """Drive the Gemini side panel against an existing deck. Step
    params::

        params:
          deck_id: "{{step.open_deck.deck_id}}"   # template ref
          prompt: "Please summarise this presentation."

    Outputs::

        outputs:
          deck_id: <unchanged from input>
          response_excerpt: <first 300 chars of Gemini's reply>

    The full reply lands in ``OperationResult.response_text`` so
    the Recipe Runner can use it as the final-step response when
    evaluating Recipe-level signals.

    Uses the per-account Chrome session from the OperationContext
    when one is available (multi-account Recipes). Falls back to
    a freshly-launched Chrome on the legacy single-profile path
    when no session is provided — same back-compat shape as
    ``advtest run`` for ui_mode targets.
    """
    deck_id = require_str_param(ctx.params, "deck_id", "slides.type_prompt")
    prompt = require_str_param(ctx.params, "prompt", "slides.type_prompt")
    trace_id = f"recipe-{uuid.uuid4().hex[:12]}"
    output_dir = Path(ctx.params.get("output_dir") or _DEFAULT_RECIPE_OUTPUT_DIR)

    # Reset-conversation policy: the FIRST type_prompt in a Recipe
    # lands as turn 1 of a fresh conversation (resets the auto-
    # summary that Gemini fires when the panel opens). Subsequent
    # type_prompt steps keep the existing conversation so
    # progressive-disclosure chains can accumulate context across
    # turns. Operators can override per-step via the
    # ``reset_conversation`` param.
    explicit = ctx.params.get("reset_conversation")
    if isinstance(explicit, bool):
        reset_conversation = explicit
    else:
        reset_conversation = not _earlier_type_prompt_ran(ctx)

    # Multi-account: ctx.session is the per-account UiModeSession
    # from the SessionPool. Drive the panel against THAT account's
    # browser. Single-account legacy: ctx.session is None;
    # ui_mode_invoke launches a fresh Chrome against the default
    # profile dir, same path advtest run uses.
    if ctx.session is not None and isinstance(ctx.session, UiModeSession):
        page = await ctx.session.ensure_page()
        response = await ui_mode_invoke(
            deck_id=deck_id,
            user_prompt=prompt,
            trace_id=trace_id,
            output_dir=output_dir,
            external_page=page,
            reset_conversation=reset_conversation,
        )
    else:
        response = await ui_mode_invoke(
            deck_id=deck_id,
            user_prompt=prompt,
            trace_id=trace_id,
            output_dir=output_dir,
            reset_conversation=reset_conversation,
        )

    excerpt = (response.text or "")[:300]
    return OperationResult(
        response_text=response.text or "",
        outputs={
            "deck_id": deck_id,
            "response_excerpt": excerpt,
        },
        raw={
            "operation": "slides.type_prompt",
            "trace_id": trace_id,
            **dict(response.raw),
        },
    )
