"""Seed the strategy library from fission's executable guides.

Each :class:`~advtest_ext.guides.models.Guide` is distilled into a
PIMiner-format strategy file so the router has a real library to route
over and the digester has prior strategies to widen. The guide's own
``carrier_payload`` (the planted poison) seeds one in-context example; the
file cites the guide it came from. Guides remain the executable recipes —
this is a one-way projection into the learned-strategy layer.
"""

from __future__ import annotations

from pathlib import Path

from advtest_ext.guides.models import Guide, display_title
from advtest_ext.strategy.markdown import example_block, strategy_file

# The live Workspace surfaces are all Gemini; the strategy-file naming
# convention bakes the target into the filename.
_SEED_TARGET = "gemini"


def strategy_id_for_guide(guide: Guide) -> str:
    """`<app>_attack_strategy_<guide_id>_gemini` — matches the discovery
    glob `*_attack_strategy_*` and the digester's dataset-prefix grep."""
    return f"{guide.target.app}_attack_strategy_{guide.id}_{_SEED_TARGET}"


def _scope_target(guide: Guide) -> list[str]:
    conf = (
        f"**Confirmed effective**: `{_SEED_TARGET}` "
        f"({guide.target.app} / {guide.target.surface}) — seeded from guide "
        f"`{guide.id}` (maturity={guide.maturity})."
    )
    use_when = guide.focus or (guide.objective or "").strip()
    return [conf, f"**Use this strategy when**: {use_when}"]


def _scope_task(guide: Guide) -> list[str]:
    out = [
        f"**Suite/product coverage demonstrated**: `{guide.target.app}` "
        f"(surface: {guide.target.surface}).",
    ]
    if guide.threat_class:
        out.append(f"**Injection-goal forms documented**: {guide.threat_class}.")
    if guide.tags:
        out.append(f"**Tags**: {', '.join(guide.tags)}.")
    if guide.entity:
        out.append(f"**Entity / persona**: {guide.entity}.")
    return out


def _seed_example(guide: Guide) -> str:
    trigger = (
        guide.exemplar_turns[0]
        if guide.exemplar_turns
        else (guide.seed_prompts[0] if guide.seed_prompts else "(user triggers the guide's journey)")
    )
    payload = guide.carrier_payload or "(payload lives in the guide's setup steps; not inlined)"
    why = (guide.strategy or guide.objective or "").strip() or "(see guide strategy)"
    return example_block(
        1,
        product=guide.target.app,
        user_task_id=guide.id,
        injection_task_id=(guide.threat_class or "ipi").split(",")[0].strip(),
        target=f"{_SEED_TARGET}, seeded",
        user_task=trigger,
        injection_goal=guide.objective or "",
        placeholder_context=payload,
        winning_injection=payload,
        resulting_action="(seeded from a confirmed guide — no single captured "
        "trajectory inlined; see the guide's success signals / replay bundles)",
        why=why,
    )


def render_seed_strategy(guide: Guide) -> str:
    """Project one guide into a complete strategy-file body."""
    intro = (guide.objective or "").strip()
    if guide.strategy:
        intro = (intro + "\n\n" + guide.strategy.strip()).strip()
    intro += (
        f"\n\n*Seeded from guide `{guide.id}` "
        f"(maturity={guide.maturity}); refine with run evidence via the digester.*"
    )
    return strategy_file(
        title=display_title(guide),
        intro=intro,
        scope_target=_scope_target(guide),
        scope_task=_scope_task(guide),
        distinct="Seeded from a guide; sibling distinctions to be refined by the "
        "digester as runs accumulate.",
        moves=list(guide.key_moves),
        examples=[_seed_example(guide)],
        fingerprint_rows=[
            (
                "1",
                (guide.key_moves[0][:40] if guide.key_moves else "seed"),
                _SEED_TARGET,
                f"{guide.target.app}/{guide.target.surface}",
                "seeded from guide",
            )
        ],
        failures=list(guide.refusal_conditions),
        notes=list(guide.invariants) + list(guide.mutation_surface),
    )


def seed_library(
    guides: list[Guide],
    library_dir: str | Path,
    *,
    overwrite: bool = False,
    only_gold: bool = False,
) -> list[Path]:
    """Write a strategy file per guide into ``library_dir``. Returns the
    paths written (skipped files are omitted)."""
    root = Path(library_dir)
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for guide in guides:
        if only_gold and guide.maturity != "gold":
            continue
        path = root / f"{strategy_id_for_guide(guide)}.md"
        if path.exists() and not overwrite:
            continue
        path.write_text(render_seed_strategy(guide), encoding="utf-8")
        written.append(path)
    return written
