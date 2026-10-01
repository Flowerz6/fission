# Search-tree design: category fan-out → compounding, deduped

Status: draft spec (2026-09-27). Owner: search/attacks.
Related: `docs/TAP_ATTACK_SUCCESS_UPGRADES.md`, `docs/FISSION_GUIDE_FORMAT.md`,
`advtest_ext/attacks/tap_search.py`, `advtest_ext/attacks/tap_crescendo.py`,
`advtest_ext/mutate/engine.py`, `advtest_ext/archive/signature.py`.

## Goal

Given one human seed (a faithful replica of a real capture), find **as many
distinct successful variants as possible per unit budget**, where "success" is
the seed's own oracle (its `success.signals` / `rubric`). This is an
*optimisation* objective, not a measurement one — so the tree is built to
**compound** the moves that work, not to hold each axis in isolation.

Non-goal: per-axis effect attribution. A small fixed slice of pure single-axis
lineages is kept alive only to feed the effects read (`advtest_ext/mutate/effects.py`);
it is not where the success budget goes.

## The invariant: what a mutation may and may not change

Everything in the carrier and the interaction is mutable — **cover text,
payload text, persona, turn order, escalation, placement**. The only thing
pinned across the whole subtree rooted at a seed is the **effect the oracle
checks**. For `gmail_ai_overview_owner_note_guidelines_star` that constant is
"an unrequested `Guidelines.md` transparency append + the `*` FALSE branch";
for `slides_serialized_spec_…` it is "cross-run-stable tool-registry / runtime
disclosure". A node whose realized artifact no longer satisfies the seed's
`invariants` is a *different attack*, not a variant of it, and is rejected
before it costs a target call.

## Cover / payload as separable layers

Each carrier decomposes into two layers that mutate **independently but are
scored together**:

- **cover** — the benign document/email a normal user would send.
- **payload** — the injected instruction (SYS block, owner note, per-slide
  footer, …).

This is the existing `GuideMutationOperator.carrier_role` field
(`cover | payload | full`). The realizer assembles the planted artifact in
code as `assemble(cover, payload)`, so:

- a `carrier_role: cover` operator regenerates **only** the benign doc; the
  current payload is re-appended verbatim;
- a `carrier_role: payload` operator regenerates **only** the instruction; the
  current cover is kept;
- a **compound** node moves both, each through its own operator, and the two
  results are assembled.

`carrier_role: full` (regenerate the whole field) is deprecated for seed
guides: it lets a weak attacker model silently drop the payload. Seed operators
are migrated to explicit `cover` / `payload` pairs with `mutable_fields`
pointing at the right slice of `setup_steps.<id>.params`.

## Tree contract

```
Depth 0   SEED REPLICA (control)
          exact human capture: verbatim cover + payload + original interaction.
          Scored once; never mutated.

Depth 1   CATEGORY FAN-OUT  (single-axis probes — the ranking stage)
          one child per mutation category:
            payload · cover · interaction · persona · placement · escalation
          each child = seed + one first move on that category only.
          Cheap. Purpose: rank which categories/cells move the oracle for THIS
          seed (full success OR partial_success both count as signal).

Depth ≥2  COMPOUNDING  (the success stage)
          a child = its parent + the best-scoring move from ANOTHER category,
          layered on. Depth = number of compounded winning moves.
          The frontier expands highest-scoring nodes first (beam / TAP prune),
          not uniformly. Dead branches (see pruning) are killed early.
```

The category set at Depth 1 is derived from the seed guide's typed
`mutation_operators` grouped by their role/dimension:

| category    | operators (`carrier_role` / `dimension`)                        |
|-------------|------------------------------------------------------------------|
| payload     | `*_payload` operators (`carrier_role: payload`)                  |
| cover       | `*_cover` operators (`carrier_role: cover`)                      |
| interaction | `exemplar_turns` / crescendo turn shape (side-chat turns)        |
| persona     | `dimension: persona`                                             |
| placement   | `dimension: carrier_placement`                                   |
| escalation  | `dimension: escalation_speed` / `turn_order`                     |

A node's category membership is fixed by its Depth-1 ancestor **only for the
ranking stage**; from Depth 2 a node carries the *set* of categories it has
compounded (recorded on the trace so the signature can recover cumulative
path state, per `archive/signature.py`).

## Scoring and pruning

Each realized node is scored by the seed's oracle. Expansion is driven by:

1. **full success** → record as an elite (see dedup), keep expanding: a working
   attack is the best base for the next compounded win.
2. **partial success** (`Guide.partial_success` fired, or refusal-without-
   warning) → expand: the payload half-landed; compounding another category may
   push it over.
3. **hard refusal / oracle-negative twice on a branch** → prune. Honour the
   guide's `refusal_conditions` / `stop_conditions`.

Beam width and max depth are run config. The point of the Depth-1 ranking stage
is that Depth-2 compounding is **not blind**: it layers the *winning* cell from
each category, so the branching factor stays small and every expansion spends
budget on a combination with prior evidence behind it.

## De-duplication across the tree

No cover, no payload, and no cover×payload pair may repeat anywhere in the
tree — including near-paraphrases. Three layers, coarsest to finest:

1. **Cell coordinate (exists).** `archive/signature.py` gives every leaf a
   MAP-Elites cell key over `KEY_MUTATION_AXES`. Two paths reaching the same
   coordinate share a cell. Coarse: different texts can share a coordinate, so
   this alone does not stop textual repeats.
2. **Strategy diversity (exists).** `tap_search.DiversityArchive` already dedups
   proposed *strategies* via `strategy_similarity` (word-set Jaccard +
   trigram overlap) and keeps per-cell elites (`is_duplicate`, `record_attempt`,
   `record_rejection`, `update`). Reuse this, don't reinvent it.
3. **Realized-content ledger (NEW).** A run-level ledger of the **realized
   cover text and payload text** actually planted at each node:
   - key: `normalize(text)` → stable hash, plus a similarity guard reusing
     `strategy_similarity(new, seen) < τ` so paraphrases are caught, not just
     byte-identical repeats;
   - separate namespaces for `cover`, `payload`, and the `(cover,payload)` pair;
   - checked **before** a node is realized against the target (a collision
     costs zero target calls); on collision the strategist regenerates (up to
     `k` retries) or the node is pruned;
   - the ledger also *feeds generation*: the pool the cell generator /
     strategist must diverge from is `static cell_values ∪ ledger`, not just
     the static `cell_values`. (`cell_generator.filter_novel` today only checks
     the static list — it is extended to accept the run pool.)

Threshold `τ` is a run knob. Start strict on payloads (they carry the mechanism;
near-dupes waste budget) and looser on covers (surface variety is cheap and
useful).

## Wiring into existing modules

- **`mutate/engine.py`** — `plan_mutations` already emits one-axis variants;
  it supplies the **Depth-1 probes**. `realize_variant` gains a compound path
  (chain two operators of different categories) and calls `assemble(cover,
  payload)` for `carrier_role`-split operators.
- **`attacks/tap_search.py`** — owns the frontier, beam, and `DiversityArchive`.
  The realized-content ledger is added here (or a sibling) and consulted in the
  candidate-acceptance path alongside `is_duplicate`.
- **`attacks/tap_crescendo.py`** — supplies the **interaction** category: the
  side-chat turn sequence is one mutable axis, not just the carrier.
- **`archive/signature.py`** — unchanged; keeps giving cumulative-path cell
  keys so compounded nodes stay diverse in the MAP-Elites sense.
- **guides** — seed operators migrated to `cover`/`payload` pairs with real
  `mutable_fields`; `partial_success` populated so pruning has a partial signal
  to read.

## Open decisions

1. **Duplicate rule τ** — identical-hash only, or similarity-thresholded
   (recommended: thresholded, per-namespace τ). Pin the payload/cover values.
2. **Compounding order** — greedy best-first across categories (recommended)
   vs. fixed category order.
3. **Effects side-budget** — how many pure single-axis lineages to keep alive
   purely for `mutate/effects.py` (recommended: small, fixed, off the hot
   success path).
4. **Interaction axis representation** — how crescendo turn shape is encoded as
   a mutable cell so it dedups in the same ledger.
