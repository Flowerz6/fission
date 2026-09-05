"""Google Calendar operation handlers for the Phase-5 Recipe Runner.

Two handlers register here:

- ``("calendar", "create_event")`` — "plant a crafted event into
  Account A's calendar" step. Creates an event via the Calendar API
  and returns its id so a later ``type_prompt`` step can drive the
  side panel against the day it lands on.
- ``("calendar", "type_prompt")`` — "Account A asks Gemini to
  summarise the day" step. Delegates to the existing ui_mode invoker
  so this handler benefits from every flake-hardening feature the
  Calendar adapter already has (Chrome stealth, panel retry, traffic
  interception, etc.).

Combined with the other adapters' recipe handlers these plug into
the same chain shape (plant a carrier, then drive the Gemini panel).

Phase 5.1 of the :doc:`red_team_report_infra_plan`. Registered
into the default registry by
:func:`advtest_ext.core.builtin_operations.default_operations`.
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import Path

from advtest_ext.adapters._auth import get_service
from advtest_ext.adapters._ui_mode_common import UiModeSession
from advtest_ext.adapters.calendar.target import CALENDAR_SCOPES
from advtest_ext.adapters.calendar.ui_mode import ui_mode_invoke
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

# The event lands on the signed-in user's default calendar — the
# surface the "summarise my day" journey reads.
_PRIMARY_CALENDAR_ID = "primary"


# ----------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------


async def create_event(ctx: OperationContext) -> OperationResult:
    """Plant a crafted Calendar event and return its id. Step params::

        params:
          title: "Team sync"          # event summary
          body: "..."                   # optional — the event description,
                                        #            where a poisoned payload rides
          date: "2026-09-02"           # optional — defaults to today (all-day)

    Outputs::

        outputs:
          event_id: <calendar event id>
          title: <as-created summary>
          url: https://calendar.google.com/calendar/u/0/r/day
          calendar_id: primary

    The ``event_id`` is the load-bearing output — a later
    ``type_prompt`` step references it via
    ``{{step.<this_step>.event_id}}``.
    """
    title = ctx.params.get("title", "Recipe carrier event")
    if not isinstance(title, str) or not title:
        title = "Recipe carrier event"

    body = ctx.params.get("body")
    description = body if isinstance(body, str) else ""

    date_raw = ctx.params.get("date")
    if isinstance(date_raw, str) and date_raw:
        start_date = datetime.date.fromisoformat(date_raw)
    else:
        start_date = datetime.date.today()
    end_date = start_date + datetime.timedelta(days=1)

    calendar = get_service("calendar", "v3", CALENDAR_SCOPES)
    created = (
        calendar.events()
        .insert(
            calendarId=_PRIMARY_CALENDAR_ID,
            body={
                "summary": title,
                "description": description,
                "start": {"date": start_date.isoformat()},
                "end": {"date": end_date.isoformat()},
            },
        )
        .execute()
    )
    event_id = created["id"]

    return OperationResult(
        outputs={
            "event_id": event_id,
            "title": created.get("summary", title),
            "url": "https://calendar.google.com/calendar/u/0/r/day",
            "calendar_id": _PRIMARY_CALENDAR_ID,
        },
        raw={
            "operation": "calendar.create_event",
            # When called with optional ``body``, expose it so the
            # Recipe Runner can populate SignalContext.document_body
            # without scraping the Calendar DOM.
            "document_body": description,
        },
    )


def _earlier_type_prompt_ran(ctx: OperationContext) -> bool:
    """Check whether an earlier Step in the same Recipe was a
    ``calendar.type_prompt`` invocation. Used by ``type_prompt`` to
    decide whether to land as turn 1 of a fresh Gemini conversation
    (first prompt) or to continue the existing one (subsequent
    prompts in progressive-disclosure chains).

    Heuristic: only ``type_prompt`` produces the ``response_excerpt``
    output key. Cheaper than threading operation names through the
    Runner's accumulated_context.
    """
    for prior_outputs in ctx.accumulated_context.values():
        if isinstance(prior_outputs, dict) and "response_excerpt" in prior_outputs:
            return True
    return False


async def type_prompt(ctx: OperationContext) -> OperationResult:
    """Drive the Gemini side panel against a planted event's day. Step
    params::

        params:
          event_id: "{{step.plant_event.event_id}}"   # template ref
          prompt: "What's on my calendar today?"

    Outputs::

        outputs:
          event_id: <unchanged from input>
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
    event_id = require_str_param(ctx.params, "event_id", "calendar.type_prompt")
    prompt = require_str_param(ctx.params, "prompt", "calendar.type_prompt")
    trace_id = f"recipe-{uuid.uuid4().hex[:12]}"
    output_dir = Path(ctx.params.get("output_dir") or _DEFAULT_RECIPE_OUTPUT_DIR)

    # Reset-conversation policy: the FIRST type_prompt in a Recipe
    # lands as turn 1 of a fresh conversation (resets the auto-
    # summary that Gemini fires when the panel opens). Subsequent
    # type_prompt steps keep the existing conversation so progressive-
    # disclosure chains can accumulate context across turns. Operators
    # can override per-step via the ``reset_conversation`` param.
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
            event_id=event_id,
            user_prompt=prompt,
            trace_id=trace_id,
            output_dir=output_dir,
            external_page=page,
            reset_conversation=reset_conversation,
        )
    else:
        response = await ui_mode_invoke(
            event_id=event_id,
            user_prompt=prompt,
            trace_id=trace_id,
            output_dir=output_dir,
            reset_conversation=reset_conversation,
        )

    excerpt = (response.text or "")[:300]
    return OperationResult(
        response_text=response.text or "",
        outputs={
            "event_id": event_id,
            "response_excerpt": excerpt,
        },
        raw={
            "operation": "calendar.type_prompt",
            "trace_id": trace_id,
            **dict(response.raw),
        },
    )
