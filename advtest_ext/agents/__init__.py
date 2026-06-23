"""Claude-driven agents — the three roles from the
:doc:`platform-plan` (Fission spec):

- :class:`Extractor` — turns a normalised Seed into a draft
  Recipe (Phase 6).
- ``Replayer`` (future — Phase deferred) — drives Playwright
  as Claude tools instead of via hand-written selectors.
- ``Explorer`` (future — Phase deferred) — generates Recipe
  variants by mutation strategies (PAIR, TAP, persona_swap,
  surface_swap, etc.).

Each agent is one Python class wrapping a Provider call.
Operators pass the Provider in; tests inject stub Providers.
"""

from advtest_ext.agents.extractor import (
    Extractor,
    ExtractorError,
)

__all__ = ["Extractor", "ExtractorError"]
