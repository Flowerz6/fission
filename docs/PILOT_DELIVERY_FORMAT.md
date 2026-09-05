# Pilot Delivery Format

How Fission folds persisted runs into the Google Workspace IPI pilot's
per-attack delivery dataset. Code: `advtest_ext/delivery/`; CLI:
`scripts/export_pilot_dataset.py`.

## What it produces

A single JSON document (`PilotDataset`) with two attack sets — `successful`
and `plausible_unsuccessful` — plus the shared `user_journeys` catalogue and
the `harm_taxonomy` legend. Every record maps 1:1 onto the brief's "Data
Format" fields:

| Brief field | Record field |
|---|---|
| 1. poisoned payload (full text + structure) | `poisoned_payload` (`full_text`, `structured[]`, `payload_kind`) |
| 2. verbatim attack string(s) | `attack_string` |
| 3. harm type (from the taxonomy) | `harm_type` (+ `harm_subtype`, `harm_rationale`) |
| 4. custom tagging metadata | `metadata` (strategy, hiding technique, tone, obfuscation, exfil method, OWASP/ATLAS/Promptware tags, judge decomposition) |
| 5. product + user journey | `user_journey_id` → `user_journeys` catalogue |
| 6. user-specified prompt | `user_supplied_prompt` |
| 7. attack success rate (N, M over 3+ trials) | `success_rate` (`trials_n`, `successes_m`, `asr`, `verdict_breakdown`) |
| 8. attack lineage id | `lineage_id` |

## The unit of delivery: an attack group

Traces are grouped by `(behavior_id, attack_name, technique_id)` — the same key
the run summary aggregates ASR over. One group → one `PilotAttack`:

- **N / M** come straight from the group's verdicts (`error` traces are
  excluded from N, matching the orchestrator's ASR denominator).
- **Lineage** is the `behavior_id` — the seed idea. A cluster of near-identical
  trials therefore shares one lineage, so diversity scoring isn't penalised for
  repetition (the brief's stated intent for field 8).
- The record is built from a **representative trace**: the highest-scoring
  success if any exists, else the highest-scoring trace overall.

## Successful vs. plausible-unsuccessful

- **successful** — group has ≥1 `success` verdict.
- **plausible_unsuccessful** — no success, but genuine engagement: a `partial`
  verdict, or a best judge score ≥ the plausibility floor (default `0.15`).
- **dropped** — pure hard-refusal groups (no engagement). `--include-hard-refusals`
  keeps them.

## Two payload shapes

- **`injected_content`** — `payload_text` carries the injection (poisoned Doc /
  email / event). `attack_string` is the recorded planted instruction
  (`extra_metadata.hidden_injection`) when present, else the carrier text.
- **`multi_turn_conversation`** — `payload_text` is empty and the attack *is* a
  conversation. Per-turn trace rows carry no trial linkage, so the canonical
  conversation is taken from the **guide's `exemplar_turns`**; `attack_string`
  is the joined attacker turns and `structured[]` lists them.

## Harm taxonomy

The brief's eight harm types, plus two higher-harm additions our corpus
exercises heavily (`system_prompt_skill_disclosure`, `internal_reasoning_leak`).
Classification is a priority-ordered keyword match over the attack identity and
guide metadata; an unmatched group is `unspecified` with a rationale (never a
confident wrong label — check the CLI's `unspecified` count and tag those by
hand). Each record also carries external-standard tags — OWASP `LLM01:2025`,
MITRE ATLAS `AML.T0051.001`, and the Promptware Workspace 5-class — which the
brief rewards as complexity/diversity metadata.

## Severity

The more severe of the harm-type default and any promoted-Finding severity, on
a `low < medium < high < critical` scale. A default-`med` finding never drags a
`critical` harm type down; an explicitly-`high` finding still wins. This keeps
the delivered distribution tilted to the severe end truthfully (a pilot scoring
axis).

## Running it

```bash
# every run in the DB
poetry run python scripts/export_pilot_dataset.py --all-runs --out pilot_dataset.json

# specific runs
poetry run python scripts/export_pilot_dataset.py \
    --run run-a95d60639e65 --run run-c4c6066f7b27 --out pilot_dataset.json
```

The CLI prints the counts by harm type, severity, and journey over the
successful set, and flags how many records are `unspecified` and need manual
harm tagging before delivery.

## Known limitations (call these out at delivery)

- **ASR trials.** N reflects however many times each attack was actually
  replayed in the runs you export. The brief wants N ≥ 3 per attack; if a run
  played each attack once, re-run before delivering so N is meaningful.
- **Multi-turn attack string** is the guide's scripted conversation, not a
  turn-linked reconstruction from the trace rows (they carry no trial id). It is
  the canonical attack that was replayed, but note it as scripted-source.
- **`unspecified` harm** records need a human tag; they are counted and warned
  about, never silently shipped as a real category.
