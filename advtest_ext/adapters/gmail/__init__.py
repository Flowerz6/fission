"""Gmail adapter — :class:`GmailTarget` is a ``DocumentTarget`` that
materialises an :class:`AbstractDocument` as a Gmail message in the
authenticated user's own inbox, asks Gemini-for-Workspace (via the
``Provider`` abstraction) to summarise the thread, and tears the
thread down afterwards.

See ``TECHNIQUE_COVERAGE.md`` next to this file for the per-technique
support matrix; Gmail is mostly an HTML-body surface, so techniques
that require Workspace-document semantics (suggested edits, page
headers/footers) don't translate.
"""

from advtest_ext.adapters.gmail.target import GmailTarget

__all__ = ["GmailTarget"]
