"""Per-source-format loaders that normalise raw red-team artefacts
into :class:`advtest_ext.core.seed.Seed` JSON.

Every loader exposes a single ``load(path: Path) -> list[Seed]``
entry point. Returning a *list* lets multi-variant artefacts
(like the Google Red Team Report's V1/V2/V3/V4 docx) produce one
Seed per variant in a single ingest call.

Loaders are intentionally thin: they normalise structure, they
don't interpret. The Phase-6 :class:`Extractor` agent does the
interpretation pass — turning normalised Seeds into draft
Recipes.

Shipped today (Phase 6 v1):

- :mod:`docx_loader` — Word ``.docx`` red-team reports
  (the Google Red Team Report shape).

Future formats (chat-export JSON, annotated markdown, HAR)
land as sibling modules; the loader registry in
:mod:`advtest_ext.cli`'s ``ingest-seed`` subcommand picks the
right loader based on file extension.
"""

from advtest_ext.core.loaders.docx_loader import load as load_docx

__all__ = ["load_docx"]
