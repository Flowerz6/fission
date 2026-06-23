"""advtest_ext — document-centric extensions to PyRIT.

This package layers on top of `pyrit` (untouched upstream) to support
indirect prompt injection (IPI) testing against productivity AI surfaces
(Google Docs / Gmail / Sheets Gemini).

Threat model and citations: see ../FOUNDATIONS.md and ../references.bib.

Each submodule declares a ``__citations__: list[str]`` of bib keys from
``references.bib``. The reporting layer (Prompt 4) walks subpackages and
unions these lists to build the references section of the PDF report,
so this list is a machine-readable contract — keep entries as exact bib
keys (no aliases, no free-text).
"""

__version__ = "0.0.1"

# Foundational reference for the project as a whole.
__citations__: list[str] = [
    "greshake2023ipi",  # origin of the IPI threat class
    "owasp_llm_top10_2025",  # industry framing (LLM01:2025)
    "pyrit_microsoft",  # the framework we fork
]
