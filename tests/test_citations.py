"""Citation hygiene: every ``__citations__`` key declared by an
``advtest_ext.*`` submodule must exist as a real bibtex entry in
``references.bib`` at the repo root.

This is the contract the Prompt-4 PDF report builder relies on. If
this test fails, either add the missing entry to references.bib or
remove the bogus key from the submodule.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import advtest_ext

REPO_ROOT = Path(__file__).resolve().parents[1]
BIB_PATH = REPO_ROOT / "references.bib"

# @entry_type{key, ...   ->   captures the key.
_BIBKEY_RE = re.compile(r"^@\w+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)


def _load_bib_keys() -> set[str]:
    text = BIB_PATH.read_text()
    return set(_BIBKEY_RE.findall(text))


def _walk_citations() -> dict[str, list[str]]:
    """Return {module_name: __citations__} for every submodule that
    declares one (including the top-level package itself)."""
    found: dict[str, list[str]] = {}
    if hasattr(advtest_ext, "__citations__"):
        found[advtest_ext.__name__] = list(advtest_ext.__citations__)

    for info in pkgutil.walk_packages(advtest_ext.__path__, prefix="advtest_ext."):
        mod = importlib.import_module(info.name)
        if hasattr(mod, "__citations__"):
            found[info.name] = list(mod.__citations__)
    return found


def test_every_citation_key_resolves_to_a_bib_entry() -> None:
    bib_keys = _load_bib_keys()
    assert bib_keys, "references.bib parsed empty — regex broken or file missing"

    citations = _walk_citations()
    missing: dict[str, list[str]] = {}
    for module_name, keys in citations.items():
        unknown = [k for k in keys if k not in bib_keys]
        if unknown:
            missing[module_name] = unknown

    assert not missing, "Citation keys not found in references.bib:\n" + "\n".join(
        f"  {m}: {ks}" for m, ks in missing.items()
    )


def test_at_least_one_module_declares_citations() -> None:
    citations = _walk_citations()
    nonempty = {m: ks for m, ks in citations.items() if ks}
    assert nonempty, "no submodule declares __citations__ — drift from FOUNDATIONS.md"


def test_citations_are_unique_within_each_module() -> None:
    for module_name, keys in _walk_citations().items():
        assert len(keys) == len(set(keys)), f"duplicate keys in {module_name}: {keys}"
