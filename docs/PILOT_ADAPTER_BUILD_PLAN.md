# Pilot Adapter Build Plan

The pilot needs the core Workspace products **covered with diversity**. We only
run **Docs + Sheets** today. This plan covers the surface gap — the true
critical path, since a seed for a product we can't drive is undeliverable.

Seed-finding is owned separately; this plan is everything *downstream* that
turns "we can run product X" into reality. Nothing here waits on seeds — build
these in parallel with seed sourcing.

## Current state (verified)

| Product | Adapter | Factory-wired | Run? | Verdict |
|---|---|---|---|---|
| Docs | full | yes | yes | ✅ live |
| Sheets | full (+ multi-turn) | yes | yes | ✅ live |
| **Gmail** | **full** (`GmailTarget` api+ui, invoker, compose, `recipe_ops`) | **yes** | **no** | **operationalize** |
| Outlook | invoker + selectors + recipe_ops | partial | no | cross-app helper, not a core product |
| **Slides** | **full** (`SlidesTarget` api+ui, invoker, `recipe_ops`, multi-turn) | **yes** | **no** | **heal selectors + run** (built, selectors seeded from Docs) |
| **Calendar** | **full** (`CalendarTarget` api+ui, invoker, `recipe_ops`, multi-turn) | **yes** | **no** | **heal selectors + run** (built, selectors seeded from Sheets/Docs) |
| Studio | none | no | no | green-field (brief: "particular importance") |

> **Update:** Slides and Calendar adapters are now built and fully wired
> (enum, journey resolver, operation registry, target factory), with offline
> tests for the wiring and pure surface. Their UI-mode selectors are seeded
> from Docs/Sheets and **must be healed against the live surface** before a real
> run (per the runbook) — that live validation plus authoring 2–3 harm-coverage
> guides each is the remaining work to move them from "built" to "delivering
> records."

Every adapter follows one pattern, so green-field work is mostly *mirroring*:
- `target.py` — maps `AbstractDocument` → the product's content (the payload).
- `ui_mode/invoker.py` — Playwright: open the product, trigger the Gemini
  side panel, submit the prompt, read + screenshot the response.
- `ui_mode/selectors.py` — the product's DOM selectors (the fragile part).
- `ui_mode/multi_turn_target.py` — the crescendo/conversation variant.
- `recipe_ops.py` — staging operations (plant email / create event / open deck).
- Shared for free: `_ui_mode_common` (session pool, interception, response
  assembly) and **`selector_heal.py`** (self-healing selectors — cuts DOM churn
  maintenance).

## Prerequisites (all tiers)

- A live Workspace tenancy with **Gemini enabled for each product**.
- A Playwright profile (`scripts/setup_playwright_profile.py`).
- The surface bring-up runbook pattern (`docs/SHEETS_UI_MODE_RUNBOOK.md`) — the
  established recipe for pointing selectors at a new live surface.

## Sequenced plan

### Tier 0 — Gmail: operationalize (days) · START HERE · highest ROI
Gmail is built and factory-wired; it has simply never been included in a
campaign. Work:
1. Verify/heal selectors against live Gmail (summarize side panel + compose).
2. Smoke-run one guide end-to-end; confirm the oracle grades and the exporter
   emits a clean record.
3. (Optional) add `ui_mode/multi_turn_target.py` mirroring Sheets (~250 LOC) if
   we want crescendo coverage on Gmail, not just single-shot summarize/reply.
4. Author/point 2–3 guides at Gmail in the **thin harm categories** it suits
   best: **phishing** (link in drafted reply), **data exfiltration** (inbox
   summary → attacker link), **PII/credential** (pretext reply). Email is the
   richest harm surface — this one adapter closes several harm gaps at once.

**Why first:** near-zero construction, closes a core product *and* ~3 missing
harm types. Do not build anything new until Gmail is running.

### Tier 1 — Calendar: green-field, simplest surface (~1 wk)
New `adapters/calendar/` mirroring the pattern. Payload = event **title +
description + invite body** (short text fields — the simplest content model).
Trigger journey = `J-CALENDAR-ASSIST` ("what's on my calendar", summarize-my-day).
`recipe_ops`: `create_event` for staging. Unlocks context-poisoning, misinfo,
and the Promptware "automatic agent/app invocation" angle the brief cares about.

### Tier 1 — Slides: green-field, Docs-like (~1 wk, parallel with Calendar)
New `adapters/slides/`. Payload = **slide body + speaker notes**; triggers =
summarize / help-me-write. Closest sibling to Docs — reuse `google_docs`
heavily. Lower harm-novelty than Calendar but needed for core-product coverage.

### Tier 2 — Studio: green-field, hardest, highest brief-importance (spike first)
Broad agent capabilities, newer and less-stable surface, least-documented DOM.
**Time-box a feasibility spike** (can we drive it via Playwright at all, and how
stable are its selectors?) before committing to a full adapter. The brief calls
Studio "of particular importance" precisely because of its broad capabilities,
so a few strong Studio examples are worth more than many on a covered surface —
but don't let it block the core-product coverage that Tiers 0–1 deliver.

## Rough sizing & ownership split

| Item | Size | Blocks pilot? |
|---|---|---|
| Gmail operationalize | S (days) | yes — core product + 3 harm types |
| Calendar adapter | M (~1 wk) | yes — core product |
| Slides adapter | M (~1 wk) | yes — core product |
| Studio spike → adapter | L (spike first) | partial — high-value, not core-gating |

Suggested split while seeds are being sourced: one person operationalizes Gmail
now, then Calendar and Slides proceed in parallel (they share almost no code
with each other), Studio spiked once a core-product adapter is fully proven end
to end. Each adapter ships its harm-coverage guides so runs produce delivery
records immediately rather than in a later pass.

## Definition of done per adapter
A guide runs end-to-end against the live product, the oracle grades the traces,
and `scripts/export_pilot_dataset.py` emits compliant records with the correct
`user_journey_id` and a non-`unspecified` harm type — with N ≥ 3 trials.
