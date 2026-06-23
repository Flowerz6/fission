"""Google Docs adapter — :class:`GoogleDocsTarget` is a
``DocumentTarget`` that materialises an :class:`AbstractDocument`
into a real Google Doc, asks Gemini-for-Workspace (via the
``Provider`` abstraction) to summarise it, and tears the doc down
afterwards.

See ``TECHNIQUE_COVERAGE.md`` next to this file for the
hiding-technique support matrix; not every technique in the
catalogue maps onto a public Docs API call (suggested edits and
text-box-offscreen are notable absences).
"""

from advtest_ext.adapters.google_docs.target import GoogleDocsTarget

__all__ = ["GoogleDocsTarget"]
