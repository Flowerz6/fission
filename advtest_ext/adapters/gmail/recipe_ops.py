"""Gmail operation handlers for the Phase-5 Recipe Runner.

Two handlers register here:

- ``("gmail", "send_email")`` — Account B-style "plant a crafted
  email into Account A's inbox" step. Returns the resulting
  ``thread_id`` so a later step can reply to it.
- ``("gmail", "reply")`` — "Account A acknowledges the planted
  email" step. Reads the thread to get the prior message's
  reply-to address, posts a benign reply on the same thread.

These are the V1 chain's first two steps. They use the same
Gmail API path the existing :class:`GmailTarget` uses (so the
OAuth token cache requirements are unchanged) but bypass the
hidden-technique machinery — Recipe steps shape email content
explicitly via params, no auto-applied carrier-doc transforms.

Phase 5.1 of the :doc:`red_team_report_infra_plan`. Registered
into the default registry by
:func:`advtest_ext.core.builtin_operations.default_operations`.
"""

from __future__ import annotations

import base64
import email.mime.text
from typing import Any

from advtest_ext.adapters._auth import GMAIL_SCOPES, get_service
from advtest_ext.core.recipe_operations import (
    OperationContext,
    OperationResult,
    require_str_param,
)


def _resolve_me(gmail: Any) -> str:
    """Resolve the special ``me`` recipient alias to the
    authenticated user's actual email address. The Gmail send API
    accepts ``userId=me`` but the MIME ``To:`` header must be a
    real RFC822 address, so the substitution happens at send
    time, not in the operator's params."""
    profile = gmail.users().getProfile(userId="me").execute()
    addr = profile.get("emailAddress")
    if not addr:
        raise RuntimeError("getProfile returned no emailAddress; cannot send")
    return str(addr)


def _send_message(
    gmail: Any,
    *,
    to: str,
    subject: str,
    body_html: str,
    thread_id: str | None = None,
) -> dict[str, str]:
    """Build a MIME message, send via Gmail API, return the
    resulting ``{thread_id, message_id}`` dict. Shared between
    ``send_email`` and ``reply`` — they only differ in whether
    they pass an existing ``thread_id`` to attach the message to
    an in-flight thread."""
    msg = email.mime.text.MIMEText(body_html, "html")
    msg["Subject"] = subject
    msg["To"] = to
    msg["From"] = "me"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload: dict[str, str] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    sent = gmail.users().messages().send(userId="me", body=payload).execute()
    return {
        "thread_id": sent.get("threadId", ""),
        "message_id": sent.get("id", ""),
    }


# ----------------------------------------------------------------------
# Handlers — async signatures the Recipe Runner calls into.
# ----------------------------------------------------------------------


async def send_email(ctx: OperationContext) -> OperationResult:
    """Send a single email. Step params::

        params:
          to: "alice@example.com"     # or "me" for self-send
          subject: "..."
          body: "..."                   # html string

    Outputs::

        outputs:
          thread_id: <gmail thread id>
          message_id: <gmail message id>
          to: <resolved recipient address>
          subject: <as-sent subject>

    The ``thread_id`` is the load-bearing output — a later
    ``reply`` step references it via
    ``{{step.<this_step>.thread_id}}``.
    """
    subject = require_str_param(ctx.params, "subject", "gmail.send_email")
    body = require_str_param(ctx.params, "body", "gmail.send_email")
    to_raw = str(ctx.params.get("to", "me") or "me")

    gmail = get_service("gmail", "v1", GMAIL_SCOPES)
    to = _resolve_me(gmail) if to_raw == "me" else to_raw
    sent = _send_message(gmail, to=to, subject=subject, body_html=body)

    return OperationResult(
        outputs={
            "thread_id": sent["thread_id"],
            "message_id": sent["message_id"],
            "to": to,
            "subject": subject,
        },
        raw={"operation": "gmail.send_email"},
    )


async def reply(ctx: OperationContext) -> OperationResult:
    """Reply on an existing thread. Step params::

        params:
          thread_id: "{{step.plant_email.thread_id}}"   # template ref
          body: "Got it, thanks."
          subject: "Re: ..."           # optional; defaults to "Re: <original>"

    Outputs::

        outputs:
          thread_id: <unchanged from input>
          message_id: <new message in the thread>
    """
    thread_id = require_str_param(ctx.params, "thread_id", "gmail.reply")
    body = require_str_param(ctx.params, "body", "gmail.reply")
    subject_override = ctx.params.get("subject")

    gmail = get_service("gmail", "v1", GMAIL_SCOPES)
    # Look up the thread's first message to recover the original
    # subject + a recipient address for the reply.
    thread = (
        gmail.users()
        .threads()
        .get(
            userId="me",
            id=thread_id,
            format="metadata",
            metadataHeaders=["Subject", "From"],
        )
        .execute()
    )
    messages = thread.get("messages", [])
    if not messages:
        raise RuntimeError(f"thread {thread_id!r} has no messages to reply to")
    headers = {
        h["name"].lower(): h["value"] for h in messages[0].get("payload", {}).get("headers", [])
    }
    original_subject = headers.get("subject", "(no subject)")
    original_from = headers.get("from") or _resolve_me(gmail)
    subject = (
        str(subject_override)
        if isinstance(subject_override, str) and subject_override
        else (
            original_subject
            if original_subject.lower().startswith("re:")
            else f"Re: {original_subject}"
        )
    )

    sent = _send_message(
        gmail,
        to=original_from,
        subject=subject,
        body_html=body,
        thread_id=thread_id,
    )

    return OperationResult(
        outputs={
            "thread_id": sent["thread_id"],
            "message_id": sent["message_id"],
            "subject": subject,
        },
        raw={"operation": "gmail.reply"},
    )
