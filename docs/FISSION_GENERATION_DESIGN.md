# Fission — Generation Design (how variants are produced)

> **For the CC session.** How fission's variant generator should mutate seeds along axes, grounded in the quality-diversity (QD) red-teaming literature. Companion to `FISSION_SEEDER_TO_GUIDE_SPEC.md` (serialization) and `FISSION_CAPTURE_SPEC.md` (variant = full seed object, two-tier axes). Refactors `advtest_ext/mutate/` + `advtest_ext/strategy/` into a standalone Venice-driven generator (`FISSION_V2_PLAN.md §5–6`, task 6).
>
> **Phase 1 vs Layer 0.5 is load-bearing in this doc.** Phase 1 = generation + **human** judge (no automated loop, no live runs). Layer 0.5 = swap/augment the human judge with an automated scorer. Build Phase 1 only; the design keeps the seam clean.

## 1. The model: MAP-Elites with a human judge

The validated approach to "generate variants that find *new* exploits" is **quality-diversity (QD) search over an archive** (Rainbow Teaming, Samvelyan et al. 2024, Meta; Ferret, Pala et al. 2024/EMNLP 2025; Ruby Teaming; QDRT 2026). The skeleton is always three parts:

1. **Archive** — a grid whose dimensions are the diversity **axes**; each cell holds the best attack found for that axis-combination.
2. **Directed mutator** — an LLM that takes a parent attack + a **target cell** and edits the parent to land in that cell.
3. **Judge + preference** — decides whether a candidate beats the cell's current occupant.

Why this and not "ask an LLM for N variants": a plain attacker LLM **mode-collapses** onto one working style; the archive forces coverage and *illuminates the vulnerability space* instead of collapsing to a single mode (Quality-Diversity Evolution, ICLR 2026 workshop). And diversity here means **behaviour-space coverage, not text difference** (QDRT 2026) — which is why fission's structured axes are the right substrate, not n-gram/embedding distance.

**Fission Phase 1 = this exact skeleton with the human in the judge seat:**

| QD component | Fission Phase 1 |
|---|---|
| Archive grid | the axis grid: `harm × surface × technique/pretext × carrier_type` (content + structural axes, `CAPTURE_SPEC §4.5`) |
| Cell occupant | the best confirmed variant for that cell (a full seed object) |
| Parent | a confirmed variant/seed in a filled neighbour cell |
| Directed mutator | **Venice**, prompted with parent carrier + target cell's axis values |
| Judge / fitness | the **human**: reproduces the variant, records verdict + why (`CAPTURE_SPEC §4.6`) |
| Archive update | a confirmed (`success`) variant becomes/replaces the cell's occupant |

This is *higher* signal than the literature's automated judges (Llama-Guard / reward model / LLM-as-judge) — fission uses a real human. Nothing about the method is deferred; only the automated judge is.

## 2. Directed mutation — the actual prompt mechanic

The core operation (Rainbow/Ferret/Ruby all share it): **sample a parent + a prescribed target cell, tell the mutator exactly which axis-values to hit.** Not "make a variant" — "take this working carrier and move it to *this* cell."

**Venice generation call, per candidate:**
- **Inputs:** parent carrier (verbatim), parent's axis-cell, **target axis-cell** (the cell being filled), the seed's `invariants` (what must not break — from `why_it_works` / success signal), and the operator `allowed`/`forbidden` for each varied axis (`SEEDER_TO_GUIDE §4a`).
- **Instruction:** edit the parent carrier so it now exhibits the target cell's axis values, holding the invariants fixed. One axis changed per step where possible (compose multi-axis moves as a sequence, per Rainbow's K-mutations / Ruby's risk-then-style).
- **Output:** a full seed-shaped variant (carrier + metadata), which renders through the writeup (`FISSION_WRITEUP_SPEC`).

**Three refinements from the literature (all Phase-1 compatible — they're prompt/selection structure, not automation):**

1. **One axis per step, composed.** Mutate toward one target axis at a time and chain, rather than changing everything at once (Rainbow mutates once per feature; Ruby does risk-then-style sequentially). Cleaner, higher hit-rate.
2. **Multiple candidates per cell.** Generate N candidates for a target cell (Ferret generates multiple mutations per iteration). In Phase 1 the human picks/confirms among them; an optional cheap pre-rank (length/heuristic, NOT an automated success judge) can order them for the human — keep it advisory.
3. **Memory-augmented mutation.** Feed the mutator the history at that cell: past candidates + **why they failed** (Ruby augments the mutation with past candidate–critique pairs). Fission already captures `why_tags` + `what_changed_and_why` — pass the failed attempts + their critiques into the next generation prompt so Venice doesn't repeat them. This is prompt context, not a feedback loop.

**Carrier is the genome.** Mutate the concrete carrier text (the thing GPTFuzzer/Rainbow/AgentVigil all edit), conditioned on the abstract fields — do **not** regenerate a fresh carrier from `objective` alone. Confirm `build_prompt`/the operator path actually feeds the carrier to Venice (open gap flagged in `SEEDER_TO_GUIDE` deviation #4); if it doesn't, that's the first fix.

## 3. Cell targeting — where to spend generation (and human time)

Human reproduction is the scarce resource, so generation must aim at **uncovered or weak cells**, not re-fill covered ones.

- **Non-uniform targeting:** sample target cells with probability weighted toward empty / low-fitness cells (Ferret samples low-fitness feature descriptors with higher probability). Empty cell = untested exploit combination = highest value.
- **Coverage is the metric**, not count. Report archive fill-rate over the axis grid; empty cells are the worklist that tells seeders what to test next.
- **Per-model archives = the drift matrix.** Keep an archive per `target_model`; different models fall to different cells (QD-Evolution finds distinct per-model vulnerability profiles). This is `CAPTURE_SPEC`'s vulnerability/drift matrix, now with a generation-time purpose.

## 4. Phase 1 scope (build this) vs Layer 0.5 (do NOT build yet)

**Phase 1 — build:**
- Directed Venice mutation toward a named target cell, carrier-as-genome, invariants held.
- One-axis-per-step composition; N-candidates-per-cell; memory-augmented prompts from captured critiques.
- Archive data structure over the axis grid; non-uniform cell targeting biased to empty/weak cells; coverage/fill-rate reporting.
- Human judge fills the archive (reproduce → verdict → occupant).
- Fixtures only until the redaction gate (`FISSION_V2_PLAN §6`); no live target runs.

**Layer 0.5 — do NOT build yet (needs the automated loop fission deliberately shelved):**
- Automated judge / reward-model scoring to fill cells without a human.
- Automated adaptive seed selection (MCTS/bandit) closing the generate→score→regenerate loop.
- Auto-feeding verdicts back into generation.

The seam: in Phase 1 the "judge" and "cell targeting" are a human + a simple empty-cell worklist. Layer 0.5 swaps in an automated judge and an MCTS policy **over the same archive** — so capturing variants in archive-cell shape now (every variant tagged with its cell + verdict) is what makes Layer 0.5 a swap, not a rebuild.

## 4b. Generation path of record (reconciliation)

> Added 2026-11-16, after the §2 diagnostic and the Phase-1 build. Fission now has two code paths
> that read a saved seed; they are **complementary, not competing**.

**The directed generator (`advtest_ext/generate/`) is the Phase-1 variant generator of record.** It
is the carrier-as-genome QD mutator this document specifies: parent carrier in → directed one-axis
edit toward a named archive cell, invariants held → a full seed-shaped variant out. All variant
*generation* goes through it.

**`seed_to_guide` → `generate_cells` is secondary — a diversity-palette feeder, not a variant
generator.** `generate_cells` grows a guide's `mutation_operators[].cell_values`: short concrete
values on one axis (e.g. new pretext themes). The §2 diagnostic confirmed it never sends the carrier
to the model and never emits a carrier. Its output is the **menu of target axis-values** the directed
generator aims a cell at — the QD archive's feature descriptors. So the guide path supplies *where to
aim*; the directed generator does *the carrier edit*. Keeping it is deliberate: QD needs a supply of
concrete per-axis values to target, which is exactly what it produces.

### How a saved seed flows into the directed generator — the full seed object, directly.

A saved seed enters the directed generator as a **`Variant` built from the full seed object**
(`CAPTURE_SPEC §4.1`), **not** re-derived from `guides/<id>.yaml`. The guide is a lossy projection
for the archive cell — it does not store `carrier_type` and it flattens the technique set — whereas
the full seed object carries every cell axis explicitly, so seeding from it keeps the cell exact:

| `Variant` field | Source on the saved seed (`CAPTURE_SPEC §4.1`) | On the guide? |
|---|---|---|
| `carrier` (the genome) | carrier artifact `body` (verbatim) | yes (`carrier_payload`) |
| `cell.harm` | `harm` (`PILOT_HARM_TYPES`) | yes (`harm_types[0]`) |
| `cell.surface` | `surface` | yes (via `target.app`/`surface`) |
| `cell.technique` | primary content `technique` (a content `_MutationDimension`) | partial (`threat_class`) |
| `cell.carrier_type` | carrier artifact `type` (doc/email/sheet/…) | **no — dropped** |
| `canary` | `canary` | yes |
| `invariants` | the seed's invariants (as `seed_to_guide` emits them) | yes |

`guides/<id>.yaml` stays the on-disk record for the corpus, `generate_cells`, and the shelved
`run_guide` path; it is **not** the directed generator's input, because of the two lossy columns
above. The one remaining wiring step is a thin `variant_from_seed(seed) -> Variant` adapter that
performs this mapping; it makes **no model call** (fixtures only until the redaction gate,
`V2_PLAN §6`).

### Why the two paths don't diverge
Both read the *same* saved seed. The guide path produces axis-value diversity (the palette); the
directed generator consumes the seed's carrier as genome and the palette as target descriptors.
Neither regenerates a carrier from `objective` alone — the carrier is edited only by the directed
mutator (§2). The Layer-0.5 seam is unchanged: an automated judge/MCTS later swaps in over the *same*
archive the directed generator already fills.


## 5. References (hand to CC with the doc)
- Rainbow Teaming — Samvelyan et al. 2024 (arXiv:2402.16822) — QD/MAP-Elites, directed mutation, feature descriptors.
- Ferret — Pala et al. 2024 (arXiv:2408.10701) — multiple mutations + scoring, non-uniform cell sampling.
- Ruby Teaming — (arXiv:2406.11654) — memory-augmented mutation, critique agent.
- QDRT — 2026 (arXiv:2506.07121) — behaviour-space diversity vs text-similarity.
- Quality-Diversity Evolution — ICLR 2026 workshop (arXiv:2606.00801) — semantic genome, per-model vulnerability profiles, mode-collapse argument.
- IPI-specific (same skeleton on injection): AgentVigil (arXiv:2505.05849), EVA (arXiv:2505.14289).
