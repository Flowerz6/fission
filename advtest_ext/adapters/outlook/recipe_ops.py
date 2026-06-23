"""Outlook operation handlers for the Phase-5 Recipe Runner.

One handler registers here:

- ``("outlook", "send_email")`` — Account B-style "plant a crafted
  email into Account A's inbox" step, delivered via the outlook.com
  web UI (Playwright-driven Compose flow). Faithful to the report's
  threat model: the attacker is anyone with email access, no
  Workspace / Gmail OAuth required.

Why this exists alongside ``gmail.send_email``: the Gmail handler
uses the Gmail API under OAuth, and the advtest OAuth client is
unverified — Google blocks non-allowlist accounts. The Outlook UI
path side-steps that gate entirely. Both handlers stay registered;
operators choose by operation name in the Recipe.

Returns the same ``thread_id`` / ``message_id`` output shape as
``gmail.send_email`` so a Recipe migrating from one to the other
needs only the operation rename — template refs
(``{{step.<id>.thread_id}}``) work unchanged, though Outlook
returns empty strings for both (see
:class:`OutlookSendResult` for why).
"""

from __future__ import annotations

from advtest_ext.adapters.outlook.ui_mode import (
    OutlookUiSession,
    outlook_ui_send_email,
)
from advtest_ext.core.recipe_operations import (
    OperationContext,
    OperationResult,
    require_str_param,
)


async def send_email(ctx: OperationContext) -> OperationResult:
    """Send a single email via outlook.com web UI. Step params::

        params:
          to: "recipient@example.com"
          subject: "..."
          body: "..."

    Outputs::

        outputs:
          to: <as-typed recipient address>
          subject: <as-typed subject>
          thread_id: ""    # empty on UI path — see OutlookSendResult
          message_id: ""   # empty on UI path

    Uses the per-account ``OutlookUiSession`` from
    ``ctx.session`` when one is available (the multi-account
    SessionPool path). Falls back to a freshly-launched Chrome on
    the legacy single-profile path when no session is provided —
    same back-compat shape the docs ``type_prompt`` handler uses.
    """
    to = require_str_param(ctx.params, "to", "outlook.send_email")
    subject = require_str_param(ctx.params, "subject", "outlook.send_email")
    body = require_str_param(ctx.params, "body", "outlook.send_email")

    if ctx.session is not None and isinstance(ctx.session, OutlookUiSession):
        page = await ctx.session.ensure_page()
        result = await outlook_ui_send_email(
            to=to,
            subject=subject,
            body=body,
            external_page=page,
        )
    else:
        result = await outlook_ui_send_email(
            to=to,
            subject=subject,
            body=body,
        )

    return OperationResult(
        outputs={
            "to": result.to,
            "subject": result.subject,
            "thread_id": result.thread_id,
            "message_id": result.message_id,
        },
        raw={"operation": "outlook.send_email"},
    )


__all__ = ["send_email"]
