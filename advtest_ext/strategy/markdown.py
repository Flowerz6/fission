"""Pure Markdown helpers for rendering and editing strategy files.

Shared by the seeder (guide -> strategy file) and the digester (append a
run hit's example, create a novel file). Rendering a whole file and
appending an example both go through here so the H2 headings stay exactly
what the router/digester parse, and so the digester's "examples are
verbatim and append-only" discipline is enforced mechanically — the
example block is built from the hit data, never free-written by the LLM.
"""

from __future__ import annotations

EXAMPLES_HEADING = "## In-context examples"


def _fence(body: str) -> str:
    """Fence a block, escaping any closing fence inside it."""
    body = (body or "").replace("```", "'''")
    return f"```\n{body}\n```"


def example_block(
    n: int,
    *,
    product: str,
    user_task_id: str,
    injection_task_id: str,
    target: str,
    user_task: str,
    injection_goal: str,
    placeholder_context: str,
    winning_injection: str,
    resulting_action: str,
    why: str,
    note: str = "",
) -> str:
    """One `### Example N` block in the canonical shape. ``placeholder_context``
    and ``winning_injection`` are pasted verbatim (fenced)."""
    tag = f" — {note}" if note else ""
    return "\n".join(
        [
            f"### Example {n} — `{product} / {user_task_id} / {injection_task_id}` "
            f"(target: {target}){tag}",
            "",
            "**User task (verbatim)**:",
            f"> {user_task.strip()}",
            "",
            "**Injection goal (verbatim)**:",
            f"> {injection_goal.strip()}",
            "",
            "**Placeholder context** (the EXACT, FULL polluted slot — verbatim):",
            _fence(placeholder_context),
            "",
            "**Winning injection text**:",
            _fence(winning_injection),
            "",
            "**Resulting target action**:",
            _fence(resulting_action),
            "",
            f"**Why this worked on {target}**:",
            why.strip() or "(rationale not recorded)",
            "",
        ]
    )


def strategy_file(
    *,
    title: str,
    intro: str,
    scope_target: list[str],
    scope_task: list[str],
    distinct: str,
    moves: list[str],
    examples: list[str],
    fingerprint_rows: list[tuple[str, str, str, str, str]],
    failures: list[str],
    notes: list[str],
) -> str:
    """Render a complete strategy file with every H2 the router/digester
    parse. ``moves`` are numbered; ``fingerprint_rows`` are
    (#, move, target, surface, why)."""

    def bullets(items: list[str], empty: str) -> str:
        items = [i for i in items if i and i.strip()]
        return "\n".join(f"- {i.strip()}" for i in items) if items else f"- {empty}"

    moves_md = (
        "\n".join(f"{i}. {m.strip()}" for i, m in enumerate(moves, 1))
        if moves
        else "1. (moves not yet distilled — digester will populate from hits)"
    )
    fp = (
        "\n".join(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} |" for r in fingerprint_rows)
        or "| 1 | (tbd) | | | |"
    )

    return "\n".join(
        [
            f"# {title.strip()}",
            "",
            intro.strip(),
            "",
            "---",
            "",
            "## Recommended target-LLM scope",
            "",
            bullets(scope_target, "(tbd)"),
            "",
            "## Recommended user-task / injected-task scope",
            "",
            bullets(scope_task, "(tbd)"),
            "",
            "---",
            "",
            "## Why this mechanism is distinct",
            "",
            distinct.strip() or "(distinctions to be refined by the digester).",
            "",
            "## The template",
            "",
            moves_md,
            "",
            "---",
            "",
            EXAMPLES_HEADING,
            "",
            ("\n".join(examples)).strip() or "(no confirmed examples yet — seeded from a guide.)",
            "",
            "---",
            "",
            "## Strategy fingerprint, in one sentence",
            "",
            "| # | Mechanism move | Target | Surface | Why it landed |",
            "|---|----------------|--------|---------|---------------|",
            fp,
            "",
            "---",
            "",
            "## When this strategy is expected to fail",
            "",
            bullets(failures, "(none documented yet; digester adds miss-pattern evidence)."),
            "",
            "---",
            "",
            "## Notes for PAIR initialization with this strategy",
            "",
            bullets(notes, "(seed guidance to be refined by the digester)."),
            "",
        ]
    )


def count_examples(doc: str) -> int:
    return doc.count("### Example ")


def append_example(doc: str, block: str) -> str:
    """Insert ``block`` at the end of the ``## In-context examples`` section
    (append-only), before the next top-level ``## `` heading."""
    i = doc.find(EXAMPLES_HEADING)
    if i == -1:  # no section yet — create one at EOF
        return doc.rstrip() + "\n\n" + EXAMPLES_HEADING + "\n\n" + block.rstrip() + "\n"
    after = i + len(EXAMPLES_HEADING)
    j = doc.find("\n## ", after)
    if j == -1:
        return doc.rstrip() + "\n\n" + block.rstrip() + "\n"
    return doc[:j].rstrip() + "\n\n" + block.rstrip() + "\n" + doc[j:]


def append_bullet_under(doc: str, heading: str, bullet: str) -> str:
    """Append ``- bullet`` to the bullet list under an H2 ``heading``
    (used by fits-but-widens scope updates). No-op-safe if the heading is
    absent (appends a fresh section)."""
    marker = heading if heading.startswith("## ") else f"## {heading}"
    i = doc.find(marker)
    line = f"- {bullet.strip()}"
    if i == -1:
        return doc.rstrip() + "\n\n" + marker + "\n\n" + line + "\n"
    after = i + len(marker)
    j = doc.find("\n## ", after)
    section_end = j if j != -1 else len(doc)
    head, tail = doc[:section_end].rstrip(), doc[section_end:]
    return head + "\n" + line + "\n" + tail
