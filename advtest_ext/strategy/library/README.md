# Strategy library

The long-term memory of the PIMiner-style learning loop
(`advtest_ext/strategy/`). One Markdown file per **attack mechanism**,
named `<dataset>_attack_strategy_<short_name>_<target_model>.md`.

- The **router** reads each file's scope summary (everything above
  `## In-context examples`) to pick the top-K per sample.
- The **attacker** is shown the full file for whichever strategies the
  router picks.
- The **digester** (post-run job) edits these files: it appends a verbatim
  in-context example per confirmed hit, widens/narrows the scope and
  failure sections with cited evidence, or creates a new file for a novel
  mechanism. Examples are only ever appended, never replaced.

`_TEMPLATE.md` is the canonical structure (skipped by the discovery glob
and preserved on reset). Keep its H2 headings verbatim — the router and
digester parse them by exact match.

These files are the *learned strategy* layer. They are distinct from
`guides/*.yaml`, which are the *executable recipes*. A strategy file
should cite the guide(s) and run(s) it was distilled from.

## Attribution

The format and the router/attacker/digester prompts are adapted from
**PIMiner** (github.com/Wang-Yanting/PIMiner, MIT License) — "Agent
Against Agent: An Agentic System for Automatic Prompt Injection Red
Teaming", Wang, Yin, Geng & Jia, arXiv 2608.05108.
