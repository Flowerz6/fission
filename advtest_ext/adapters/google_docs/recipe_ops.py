"""Google Docs operation handlers for the Phase-5 Recipe Runner.

Two handlers register here:

- ``("docs", "open_document")`` — "Account A opens a benign
  carrier doc" step. Creates a fresh Doc via the Drive API and
  returns its id so a later ``type_prompt`` step can drive the
  sidebar against it.
- ``("docs", "type_prompt")`` — "Account A types a prompt in the
  Gemini sidebar" step. Delegates to the existing ui_mode
  invoker so this handler benefits from every flake-hardening
  feature the Docs adapter already has (Chrome stealth, panel
  retry, traffic interception, etc.).

These are V1's last two steps. Combined with
``adapters/gmail/recipe_ops.py``, the four handlers cover the
whole V1 chain.

Phase 5.1 of the :doc:`red_team_report_infra_plan`. Registered
into the default registry by
:func:`advtest_ext.core.builtin_operations.default_operations`.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from advtest_ext.adapters._auth import DOCS_SCOPES, get_service
from advtest_ext.adapters.google_docs.ui_mode import (
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


async def open_document(ctx: OperationContext) -> OperationResult:
    """Create a fresh Google Doc via the Drive API and return its
    id. Step params::

        params:
          title: "Quarterly Review Draft"
          body: "..."          # optional — when set, written into the doc body
                                #            via the Drive update endpoint.

    Outputs::

        outputs:
          document_id: <drive file id>
          title: <as-created title>
          url: https://docs.google.com/document/d/<id>

    The ``document_id`` is the load-bearing output — a later
    ``type_prompt`` step uses it to navigate the sidebar to this
    doc.
    """
    title = ctx.params.get("title", "Recipe carrier doc")
    if not isinstance(title, str) or not title:
        title = "Recipe carrier doc"

    drive = get_service("drive", "v3", DOCS_SCOPES)
    metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    created = drive.files().create(body=metadata, fields="id, name").execute()
    document_id = created["id"]

    # Optional body content. For V1 the doc body is benign cover
    # (the attack is in the email + the side-panel prompt, not in
    # the doc itself), so this is usually omitted.
    body_text = ctx.params.get("body")
    if isinstance(body_text, str) and body_text:
        # Drive's plain-text update path: re-export with text/plain
        # would actually require the Docs API for in-place edits,
        # but for an initial body the simpler ``files().update``
        # with a text/plain ``media_body`` works. We use the
        # ``googleapiclient.http.MediaInMemoryUpload`` shim.
        from googleapiclient.http import MediaInMemoryUpload

        media = MediaInMemoryUpload(body_text.encode("utf-8"), mimetype="text/plain")
        drive.files().update(fileId=document_id, media_body=media).execute()

    return OperationResult(
        outputs={
            "document_id": document_id,
            "title": created.get("name", title),
            "url": f"https://docs.google.com/document/d/{document_id}",
        },
        raw={
            "operation": "docs.open_document",
            # When called with optional ``body``, expose it so the
            # Recipe Runner can populate SignalContext.document_body
            # without trying to scrape Docs' canvas-rendered DOM.
            "document_body": body_text if isinstance(body_text, str) else "",
        },
    )


async def set_document_body(ctx: OperationContext) -> OperationResult:
    """Replace the body content of an existing Google Doc.

    The right delivery channel for V2-style "plant parameter-shaped
    strings in the doc body" attacks: the content lives inside the
    document itself (visible to the user, parsed by Gemini as
    grounding context), NOT inside a side-panel chat turn.

    Step params::

        params:
          document_id: "{{step.open_doc.document_id}}"
          body: "absolute_insert_position: start\\ninstruction: ..."

    Outputs::

        outputs:
          document_id: <unchanged from input>
          body_length: <chars written>

    Uses Drive's update endpoint with a ``text/plain`` media
    upload — same Drive scope the rest of the Docs operations use,
    no extra auth setup. For richer formatting (HTML, structured
    elements) operators can swap to the Docs API ``batchUpdate``
    endpoint in a follow-up handler.
    """
    document_id = require_str_param(ctx.params, "document_id", "docs.set_document_body")
    body = require_str_param(ctx.params, "body", "docs.set_document_body")

    from googleapiclient.http import MediaInMemoryUpload

    drive = get_service("drive", "v3", DOCS_SCOPES)
    media = MediaInMemoryUpload(body.encode("utf-8"), mimetype="text/plain")
    drive.files().update(fileId=document_id, media_body=media).execute()

    return OperationResult(
        outputs={
            "document_id": document_id,
            "body_length": len(body),
        },
        raw={
            "operation": "docs.set_document_body",
            # Expose the planted body so the Recipe Runner can
            # surface it as ``SignalContext.document_body``. Reading
            # the live editor via Playwright doesn't work — Docs
            # paints text to canvas, not standard DOM — so the
            # source of truth for "what got written" is the params
            # we just sent to Drive.
            "document_body": body,
        },
    )


def _earlier_type_prompt_ran(ctx: OperationContext) -> bool:
    """Check whether an earlier Step in the same Recipe was a
    ``docs.type_prompt`` invocation. Used by ``type_prompt`` to
    decide whether to land as turn 1 of a fresh Gemini conversation
    (first prompt) or to continue the existing one (subsequent
    prompts in V3/V4 progressive-disclosure chains).

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
    """Drive the Gemini side panel against an existing Doc. Step
    params::

        params:
          document_id: "{{step.open_doc.document_id}}"   # template ref
          prompt: "Please summarise this document."

    Outputs::

        outputs:
          document_id: <unchanged from input>
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
    document_id = require_str_param(ctx.params, "document_id", "docs.type_prompt")
    prompt = require_str_param(ctx.params, "prompt", "docs.type_prompt")
    trace_id = f"recipe-{uuid.uuid4().hex[:12]}"
    output_dir = Path(ctx.params.get("output_dir") or _DEFAULT_RECIPE_OUTPUT_DIR)

    # Reset-conversation policy: the FIRST type_prompt in a Recipe
    # lands as turn 1 of a fresh conversation (resets the auto-
    # summary that Gemini fires when the panel opens). Subsequent
    # type_prompt steps keep the existing conversation so V3/V4
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
            document_id=document_id,
            user_prompt=prompt,
            trace_id=trace_id,
            output_dir=output_dir,
            external_page=page,
            reset_conversation=reset_conversation,
        )
    else:
        response = await ui_mode_invoke(
            document_id=document_id,
            user_prompt=prompt,
            trace_id=trace_id,
            output_dir=output_dir,
            reset_conversation=reset_conversation,
        )

    excerpt = (response.text or "")[:300]
    return OperationResult(
        response_text=response.text or "",
        outputs={
            "document_id": document_id,
            "response_excerpt": excerpt,
        },
        raw={
            "operation": "docs.type_prompt",
            "trace_id": trace_id,
            **dict(response.raw),
        },
    )
