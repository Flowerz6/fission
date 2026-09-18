"""``Surface`` — a UI region of one Workspace app, scoped to one
account, optionally to one document.

A Surface is the address the Replayer / Recipe Runner uses to
identify "where does this Step happen?". Without it, multi-
account / multi-app Recipes (V1 in the Google Red Team Report:
"B emails A, then A opens Docs") have no way to declare which
account drives which step in which product.

The shape lifts a few axes out of free-form strings into
discriminated unions so a config typo (``app: doks``) fails at
load time, not at run time when the orchestrator tries to dispatch.

Phase 1 of the
:doc:`red_team_report_infra_plan`. Used by Phase 5's Recipe
Runner; declared early so other components can reference it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Workspace apps that have a Gemini surface today. Extend as new
# apps gain Gemini integration. The strings match the
# Fission-design-doc taxonomy so future cross-doc references stay
# self-consistent.
App = Literal[
    "docs",
    "gmail",
    "sheets",
    "calendar",
    "drive",
    "slides",
    "gemini_app",  # standalone Gemini at gemini.google.com
    "outlook",  # outlook.com web UI — used by the Outlook send_email
    # handler for V1/V4 attacker_sender steps (no OAuth)
]


# Within one app, where in its UI the Step happens. Different
# regions have different driving semantics — a Docs ``sidebar``
# means the Gemini side panel; ``main`` means the document body
# itself; ``compose`` (Gmail) means the compose-window helper.
Region = Literal[
    "sidebar",  # Gemini side panel
    "main",  # primary content area (Doc body, email reading pane)
    "compose",  # compose window / draft surface (Gmail)
    "thread_list",  # inbox / event list view
    "chat",  # standalone Gemini chat surface
]


# Role labels for the multi-account pool (Phase 4). Recipes
# reference accounts by role; the run scheduler binds each role
# to a concrete pool account at run time.
AccountRole = Literal[
    "recipient",  # the victim — Gemini operates on their behalf
    "attacker_sender",  # an account the attacker controls; plants content into the recipient's surfaces
    "outlook_sender",  # attacker account on Outlook.com, driven purely through the web UI (no OAuth)
    "bystander",  # third-party account referenced by the Recipe but neither attacker nor victim
]


class Surface(BaseModel):
    """A UI region of one Workspace app, scoped to one account.

    Equality is field-wise. Surfaces with the same
    ``(app, region, account_role)`` triple are the same surface
    regardless of the document_id (which often becomes known only
    after a setup Step runs).

    The ``document_id`` field is intentionally a free-form string,
    not an enum or URL — different apps name their objects
    differently (Docs / Drive: file id, Gmail: thread id,
    Calendar: event id). Validation lives in the adapter that
    knows what to do with the id, not here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    app: App
    region: Region
    account_role: AccountRole
    document_id: str | None = Field(
        default=None,
        description=(
            "App-specific object id this surface is scoped to. "
            "None for surfaces that aren't object-scoped (a "
            "thread_list view, a fresh compose, etc.)."
        ),
    )

    def __str__(self) -> str:
        base = f"{self.app}/{self.region}@{self.account_role}"
        return f"{base}#{self.document_id}" if self.document_id else base
