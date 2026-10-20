# Mutation-axis taxonomy (revised)

Status: draft spec (2026-09-27). Owner: search/attacks.
Related: `docs/SEARCH_TREE_DESIGN.md`, `advtest_ext/archive/signature.py`,
`advtest_ext/attacks/tap_search.py`, `advtest_ext/guides/models.py`.

## Why change the axes

The tree fans out one branch per **mutation category**; those categories are the
guide's operator `dimension`s, which map to the signature cell key. Today that set
is `framing · turn_order · persona · carrier_placement · escalation_speed`
(+ `wording`, + `objective_class`, + `cross_app_hops`). The first `sheets_skill_leak`
run exposed three problems with it:

1. **Redundancy** — `turn_order` and `escalation_speed` are two facets of one thing
   (the crescendo trajectory). They were never varied independently in a way the
   cell key could tell apart.
2. **A mislabel that corrupted cell keys** — `probe_target` (which internal module
   to name) was filed under `framing`. Combined with the attacker self-reporting a
   dimension that didn't match the scheduled operator, this produced garbage cell
   keys like `persona:escalation_speed:one-concession-per-turn`
   (`MutationCandidate.cell = "{dimension}:{operator}:{feature}"`).
3. **The two highest-leverage axes in the literature are missing** —
   *persuasion principle* and *format/channel mimicry*. The run itself showed both
   mattering (the attack rides commitment/consistency; Gemini emitted a mimicked
   `### Tool Call Request` block), yet neither is a first-class axis.

## What the field varies (grounding)

- **Rainbow Teaming** (the MAP-Elites design our archive copies) varies
  **Risk Category × Attack Style** — "style" (role-play, technical terms,
  misspellings) is a first-class descriptor, separate from persona.
  <https://arxiv.org/html/2402.16822v3>
- **PAP — "How Johnny Can Persuade LLMs"** (Zeng et al., ACL 2024): a taxonomy of
  **40 persuasion techniques in 13 families** (authority, commitment/consistency,
  reciprocity, social proof, scarcity, expert endorsement, foot-in-the-door,
  emotional appeal…). 92%+ ASR from paraphrasing along a persuasion principle.
  Our `sheets_skill_leak` seed is literally commitment/consistency.
  <https://arxiv.org/abs/2401.06373> · <https://github.com/CHATS-lab/persuasive_jailbreaker>
- **Crescendo / ActorAttack** (multi-turn): the trajectory decomposes into
  order × speed × **backtracking** × **topic transition** × **intent decomposition**.
  <https://www.usenix.org/conference/usenixsecurity25/presentation/russinovich> ·
  <https://arxiv.org/pdf/2504.04717>
- **IPI taxonomies / "Anatomy of a Prompt Injection"** (carrier attacks): delivery
  vector, **injection technique (delimiter/format spoofing, encoding)**, position,
  conditional trigger, objective.
  <https://arxiv.org/pdf/2608.07808> · <https://arxiv.org/pdf/2604.27202> ·
  <https://genai.owasp.org/llmrisk/llm01-prompt-injection/>

## The revised axis set

Two families share one cell key; an axis a given attack doesn't use sits at
`BASELINE` (the signature already treats that as informative thin coverage, not
missing data). Mutable axes go from 5 → 6.

**Cell-key axes** (the MAP-Elites coordinate):

| # | axis | replaces / status | values (examples) |
|---|------|-------------------|-------------------|
| — | `objective_class` | keep | the end-effect class |
| 1 | `persuasion_principle` | **NEW** (PAP) | commitment-consistency · authority · reciprocity · social-proof · scarcity · expert-endorsement |
| 2 | `pretext_scenario` | reframes `framing` / `proof_pressure` | qa-evaluation · internal-audit · debugging · incident-response · capability-research · roleplay |
| 3 | `persona_role` | keep (`persona`) | who the human claims to be × who the model is cast as |
| 4 | `escalation_trajectory` | **merges** `turn_order` + `escalation_speed` | order × speed × backtracking × topic-transition × decomposition-granularity |
| 5 | `format_mimicry` | **NEW** | plain-prose · fake-system-tag · tool-call-syntax · json-schema-block · error-trace-mimicry |
| 6 | `carrier_placement` | keep (carrier mode) | post-signature · footer · hidden-comment · … |
| — | `cross_app_hops_bucket` | keep | 0 · 1 · 2+ |

**Not cell-key axes** (facets, kept out of the coordinate so they don't explode
the archive and don't corrupt keys):

- `target_selection` — *which* internal object/module the probe names
  (`api_safety` real vs `cell_encryption` fake). This is a payload/tactical choice,
  not a diversity coordinate. It was the mislabeled `probe_target`.
- `obfuscation_encoding` — TIP / base64 / unicode / language. Carrier-mode dressing;
  fold into the payload operator, or treat as a within-cell variant like `wording`.
- `wording` — the paraphrase axis; identifies the individual within a cell, never
  the cell (unchanged).

### Family usage
- **Social / conversation** (e.g. `sheets_skill_leak`): axes 1–5 active,
  `carrier_placement = BASELINE`.
- **Carrier / planted-doc** (e.g. `gmail_ai_overview`): axes 1,2,3,5,6 active,
  `escalation_trajectory` often `BASELINE` (single-shot), plus the cover/payload
  split from `SEARCH_TREE_DESIGN.md`.

## Concrete operator set — `sheets_skill_leak`

Replaces the current 6 operators:

| operator | dimension | cell values (seed) |
|----------|-----------|--------------------|
| `persuasion_principle` | `persuasion_principle` | commitment-consistency · authority-inversion · expert-endorsement · reciprocity · foot-in-the-door · social-proof |
| `pretext_scenario` | `pretext_scenario` | qa-evaluation · internal-audit · debugging-session · incident-response · capability-research |
| `persona_role` | `persona_role` | deputy agent · spawned execution node · internal QA subagent · delegated evaluator · internal auditor |
| `escalation_trajectory` | `escalation_trajectory` | collaborator→deputy→spawned:one-per-turn · deputy→spawned:compressed · consistency-first:slow-burn · with-backtracking · topic-transition-first |
| `format_mimicry` | `format_mimicry` | plain-prose · tool-call-syntax · fake-system-tag · json-schema-block · error-trace-mimicry |
| `target_selection` | `wording` (facet, not a cell axis) | api_safety · technical_skills/cell_encryption (fake) · persona_skills · sheets_executor internals |
| `surface_wording` | `wording` | terse-operator-voice · verbose-polite · technical-jargon · casual-collaborative |

Invariants unchanged (QA frame, gradual escalation, real-then-fake probe,
disclosure = success).

## Code-extension scope (`signature.py` + friends)

This is a real change to the cell key. Touch points, in dependency order:

1. **`advtest_ext/guides/models.py`** — extend the `_MutationDimension` Literal to
   the new names; keep the old names as accepted aliases for one release so the
   existing corpus still validates.
2. **`advtest_ext/archive/signature.py`** — `KEY_MUTATION_AXES`, the `Signature`
   fields, `cell_key()`, `axis_values()`. Add `persuasion_principle`,
   `pretext_scenario` (renamed from `framing`), `escalation_trajectory` (merged),
   `format_mimicry`; drop `turn_order`/`escalation_speed` as standalone.
3. **`advtest_ext/attacks/tap_search.py`** — `MutationDimension` enum,
   `_DIMENSION_TERMS`, `infer_dimension`. This is also where the
   `dimension≠scheduled-operator` mismatch must **reject-and-not-record** (today it
   flagged the mismatch but still archived a corrupted cell — fix alongside).
4. **`advtest_ext/mutate/instantiate.py`** (`SEMANTIC_AXES`) and
   **`mutate/engine.py`** (`PRIORITY_PAIR = escalation_speed × turn_order` becomes a
   within-`escalation_trajectory` concern).
5. **Guides** — relabel each guide's operator `dimension`s (script + review). Gold
   guides re-checked.
6. **Migration / back-compat** — an alias map `{framing→pretext_scenario,
   turn_order→escalation_trajectory, escalation_speed→escalation_trajectory}` so
   existing traces and archives resolve; the merged axis means two old axes collapse
   to one (archive cells de-duplicate, acceptable).

**Risk:** the cell key changes shape → old archives/checkpoints are not directly
comparable; treat this as a new archive generation, not an in-place migration.

## Open decisions
1. **6 mutable axes vs holding at 5** — is `format_mimicry` worth the extra archive
   dimension now, or fold it into `pretext_scenario` initially? (Recommend: keep it
   separate — the run shows it's independently varied.)
2. **`persuasion_principle` value set** — the full PAP 13-family list vs the 6-value
   subset above. (Recommend: start with the 6 that fit disclosure/IPI, grow via the
   cell generator.)
3. **Back-compat window** — how long to keep old dimension names as aliases before
   removing.
