"""The strategy library: discovery + section parsing.

Strategy files are Markdown, one per attack mechanism, named
``<dataset>_attack_strategy_<short_name>_<target>.md`` (PIMiner's
convention). The discovery glob skips ``_TEMPLATE.md`` (the structural
reference the digester writes against) and any ``_template_cold_start``
snapshot.

The router is shown each strategy's **summary** (intro + the two scope
sections + the "why distinct" + the template "moves" — everything down to,
but not including, ``## In-context examples``) to keep its context small,
exactly as the paper's router does (it reports a 43-61% input cut with no
ASR loss). The attacker is shown the **full** file for whichever
strategies the router picks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_LIBRARY_DIR = Path(__file__).parent / "library"
_STRATEGY_GLOB = "*_attack_strategy_*.md"
_EXAMPLES_HEADING = "## In-context examples"


@dataclass
class Strategy:
    id: str
    """Filename stem — the id the router emits and the attacker is keyed on."""
    path: Path
    text: str
    """Full file contents (shown to the attacker)."""
    summary: str
    """Intro + scope + template, down to ``## In-context examples`` (shown
    to the router)."""


def _summarize(text: str) -> str:
    """Everything above the in-context examples — the scope/regime the
    router reasons from, without the (large) example bodies."""
    idx = text.find(_EXAMPLES_HEADING)
    return (text[:idx] if idx != -1 else text).strip()


def discover(library_dir: Path | str | None = None) -> list[Strategy]:
    """Load every real strategy file under ``library_dir`` (default: the
    packaged ``library/``). Sorted by id for stable routing prompts."""
    root = Path(library_dir) if library_dir is not None else _LIBRARY_DIR
    out: list[Strategy] = []
    for path in sorted(root.glob(_STRATEGY_GLOB)):
        if path.name.startswith("_"):  # _TEMPLATE / _template_cold_start
            continue
        text = path.read_text(encoding="utf-8")
        out.append(Strategy(id=path.stem, path=path, text=text, summary=_summarize(text)))
    return out


def cold_start_doc(library_dir: Path | str | None = None) -> str:
    """The ``_TEMPLATE.md`` structural reference — handed to the attacker
    as the single candidate when a sample routes to cold-start (attack
    from scratch, mirroring the template's section structure)."""
    root = Path(library_dir) if library_dir is not None else _LIBRARY_DIR
    template = root / "_TEMPLATE.md"
    if not template.exists():
        raise FileNotFoundError(f"cold-start template not found at {template}")
    return template.read_text(encoding="utf-8")
