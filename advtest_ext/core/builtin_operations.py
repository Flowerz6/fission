"""Factory for the default Recipe-Runner operation registry.

Bundles every handler shipped by the per-product adapter
``recipe_ops.py`` modules into one
:class:`~advtest_ext.core.recipe_operations.OperationRegistry`
instance that ``advtest run-recipe`` uses by default. Operators
who want a custom registry (additional ops, mocks for testing)
build their own by combining this factory's output with their
own registrations.

Shipped today (Phase 5.1) — enough to execute the V1 chain from
the Google Red Team Report end-to-end:

- ``("gmail", "send_email")``  → plant inbound email
- ``("gmail", "reply")``       → ack on the planted thread
- ``("docs",  "open_document")`` → create the benign carrier doc
- ``("docs",  "type_prompt")`` → drive Gemini side panel

Adding a new operation = write a handler, import it here, append
to the registration list. Adding a new app = create
``adapters/<app>/recipe_ops.py`` and import its handlers
similarly.
"""

from __future__ import annotations

from advtest_ext.adapters.calendar import recipe_ops as calendar_ops
from advtest_ext.adapters.gmail import recipe_ops as gmail_ops
from advtest_ext.adapters.google_docs import recipe_ops as docs_ops
from advtest_ext.adapters.outlook import recipe_ops as outlook_ops
from advtest_ext.adapters.sheets import recipe_ops as sheets_ops
from advtest_ext.adapters.slides import recipe_ops as slides_ops
from advtest_ext.adapters.studio import recipe_ops as studio_ops
from advtest_ext.core.recipe_operations import OperationRegistry


def default_operations() -> OperationRegistry:
    """Construct a fresh :class:`OperationRegistry` populated with
    every built-in handler.

    Returns a new registry per call so tests + parallel runs
    don't accidentally share state. The handler functions
    themselves are stateless — registering the same handler
    callable into two independent registries is fine.
    """
    registry = OperationRegistry()

    # Gmail-side operations.
    registry.register("gmail", "send_email")(gmail_ops.send_email)
    registry.register("gmail", "reply")(gmail_ops.reply)

    # Docs-side operations.
    registry.register("docs", "open_document")(docs_ops.open_document)
    registry.register("docs", "set_document_body")(docs_ops.set_document_body)
    registry.register("docs", "type_prompt")(docs_ops.type_prompt)

    # Slides-side operations (deck body + speaker notes are the payload).
    registry.register("slides", "open_deck")(slides_ops.open_deck)
    registry.register("slides", "set_speaker_notes")(slides_ops.set_speaker_notes)
    registry.register("slides", "type_prompt")(slides_ops.type_prompt)

    # Workspace Studio — the agentic app-runner surface. ``create_app`` points
    # an app at its (planted) data source; ``run_agent`` runs it / sends the
    # trigger. See adapters/studio/recipe_ops.py.
    registry.register("studio", "create_app")(studio_ops.create_app)
    registry.register("studio", "run_agent")(studio_ops.run_agent)

    # Calendar-side operations (event title + description are the payload).
    registry.register("calendar", "create_event")(calendar_ops.create_event)
    registry.register("calendar", "type_prompt")(calendar_ops.type_prompt)

    # Sheets: mint + seed a host spreadsheet so the conversation aligns with
    # real on-grid content (no blank-sheet incoherence).
    registry.register("sheets", "create_and_seed")(sheets_ops.create_and_seed)

    # Outlook-side operations — UI-mode email send, no OAuth gate.
    # Registered alongside (not instead of) gmail.send_email; a
    # Recipe picks by operation name.
    registry.register("outlook", "send_email")(outlook_ops.send_email)

    return registry


__all__ = ["default_operations"]
