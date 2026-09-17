"""Google Slides adapter — :class:`SlidesTarget` is a
``DocumentTarget`` that materialises an :class:`AbstractDocument`
into a real Google Slides deck, asks Gemini-for-Workspace (via the
``Provider`` abstraction) to summarise it or help write it, and
tears the deck down afterwards.

Slides is Docs' closest sibling: the two share the Gemini Sidekick
side panel, so this adapter mirrors ``google_docs``. The one
Slides-specific twist is the carrier — the injection rides in slide
body text and, especially, **speaker notes** (a channel a presenter
rarely reads). See the Docs ``TECHNIQUE_COVERAGE.md`` for the
hiding-technique support matrix this adapter inherits.
"""

from advtest_ext.adapters.slides.target import SlidesTarget

__all__ = ["SlidesTarget"]
