# Pilot Adapter — Action Checklists (all tiers)

First-PR-granularity actions for every surface in `PILOT_ADAPTER_BUILD_PLAN.md`.
Each tier is an independent unit of work. Prerequisites (one-time): logged-in
Playwright profile (`scripts/setup_playwright_profile.py`), `ANTHROPIC_API_KEY`
for the attacker LLM, `sqlite:///./advtest.db` (default).

Definition of done, every tier: a guide runs live → the oracle grades the
traces → `scripts/export_pilot_dataset.py` emits compliant records with the
right `user_journey_id` and a non-`unspecified` harm type, at **N ≥ 3 trials**.

---

## Tier 0 — Gmail: operationalize (START HERE)

Gmail is built and factory-wired; it has never been run. No construction —
this is selector verification + a campaign. Harm-coverage guides are **already
authored** (this PR): `gmail_reply_phishing_link` (phishing),
`gmail_inbox_summary_exfil` (data-exfil), `gmail_reply_credential_pretext`
(PII/credential).

**Checklist**
- [ ] Log the Playwright profile into the test Gmail; confirm the Gemini side
      panel opens on a thread and on compose.
- [ ] Verify/heal selectors in `advtest_ext/adapters/gmail/ui_mode/selectors.py`
      and `compose_selectors.py` against live Gmail. The side panel is the
      shared Sidekick component, so most carry over from Docs; the likely
      divergences are the **thread-open / compose-open** gates and the
      **"Help me reply" entry button**. Heal into the `gmail` overlay via
      `scripts/heal_selectors.py --manual --url <thread-url> --apply` (per the
      Sheets runbook). No code edit needed for heals.
- [ ] Smoke-run one guide end to end:
      `poetry run python scripts/run_guide.py --guide gmail_inbox_summary_exfil --browser-mint --output-dir ./out`
      (confirm the panel opens, the summary renders, a trace persists). Heal any
      `SidePanelUnavailable` drift (screenshot under `out/failures/`) and re-run.
- [ ] Run all three Gmail guides with ≥3 trials each; confirm the oracle grades
      and at least one lands `success`.
- [ ] Export and eyeball: `scripts/export_pilot_dataset.py --run <ids> --out gmail.json`
      → records carry `J-GMAIL-SUMMARIZE` / `J-GMAIL-REPLY` and harm ≠ unspecified.
- [ ] (Optional) add `ui_mode/multi_turn_target.py` mirroring
      `adapters/sheets/ui_mode/multi_turn_target.py` (~250 LOC) for crescendo
      coverage on Gmail, not just single-shot.

**Blocks pilot:** yes — core product + 3 missing harm types. **Size:** S (days).

---

## Green-field prerequisite (Tiers 1–2)

Slides / Calendar / Studio are **not** valid guide targets yet — the `Guide`
`_App` / journey enums don't include them. Before authoring guides for a new
surface:
- [ ] Extend `_App` and `_APP_REGION` in `advtest_ext/guides/models.py`.
- [ ] Add the surface's journeys to `JOURNEY_CATALOGUE` +
      `_APP_SURFACE_JOURNEY` / `_TARGET_JOURNEY` in
      `advtest_ext/delivery/journeys.py` (the catalogue entries already exist
      for Slides/Calendar/Studio — wire the resolver mappings).
- [ ] Add harm-mapping keywords if the surface introduces new behaviour ids
      (`advtest_ext/delivery/harm_taxonomy.py`).

Each new adapter mirrors `adapters/sheets/` (the smallest complete example):
`target.py`, `ui_mode/{invoker,selectors,multi_turn_target}.py`, `recipe_ops.py`,
`__init__.py`. Register `recipe_ops` in `advtest_ext/core/builtin_operations.py`
and the target in `_workspace_target_factory` (`advtest_ext/_cli_factories.py`).
Shared for free: `_ui_mode_common` + `selector_heal.py`.

---

## Tier 1 — Calendar (green-field, simplest)

Payload = event **title + description + invite body** (short text fields — the
simplest content model). Journey `J-CALENDAR-ASSIST` ("what's on my calendar",
summarize-my-day).

**Checklist**
- [ ] Green-field prerequisite above (app enum + journey wiring for `calendar`).
- [ ] `adapters/calendar/target.py` — map `AbstractDocument.body_blocks` →
      event title/description; `recipe_ops.create_event` for staging (plant the
      poisoned event). Mirror `sheets/target.py`.
- [ ] `ui_mode/invoker.py` — navigate to Calendar, open the Gemini side panel,
      submit the assist prompt, read + screenshot. Mirror `gmail/ui_mode/invoker.py`
      (same Sidekick panel; different URL + readiness gate).
- [ ] `ui_mode/selectors.py` — verify live: Calendar side-panel entry button,
      event-open gate. Heal into a `calendar` overlay.
- [ ] `ui_mode/multi_turn_target.py` — mirror `sheets`.
- [ ] Author 2–3 guides in thin harm categories Calendar suits: **misinformation**
      (fake meeting time/location injected into the day summary),
      **disrupting_workflow** (event that steers the assistant to spam/reschedule),
      and the Promptware auto-invocation angle (**rogue_actions**).
- [ ] Run ≥3 trials, grade, export; confirm `J-CALENDAR-ASSIST` + real harm.

**Blocks pilot:** yes — core product. **Size:** M (~1 wk).

---

## Tier 1 — Slides (green-field, Docs-like)

Payload = **slide body + speaker notes**; journeys summarize / help-me-write.
Closest sibling to Docs — reuse `adapters/google_docs/` heavily.

**Checklist**
- [ ] Green-field prerequisite above (app enum + journey wiring for `slides`).
- [ ] `adapters/slides/target.py` — map blocks → slide text + speaker notes;
      `recipe_ops` to plant/open a deck. Mirror `google_docs/target.py`.
- [ ] `ui_mode/invoker.py` — mirror `google_docs/ui_mode/invoker.py` (Docs and
      Slides share the Sidekick panel; differ in deck navigation + notes pane).
- [ ] `ui_mode/selectors.py` — verify live: Slides side-panel entry, deck/notes
      readiness gates. Heal into a `slides` overlay.
- [ ] `ui_mode/multi_turn_target.py` — mirror `google_docs`.
- [ ] Author 2–3 guides: reuse the Docs indirect-summary pattern with the
      injection in **speaker notes** (a channel a presenter rarely reads) —
      **data_exfiltration** and **misinformation** land naturally.
- [ ] Run ≥3 trials, grade, export; confirm `J-SLIDES-SUMMARIZE` + real harm.

**Blocks pilot:** yes — core product. Runs in parallel with Calendar (they
share no code). **Size:** M (~1 wk).

---

## Tier 2 — Studio (green-field, hardest, spike first)

Broad agent capabilities, newer/less-stable surface, least-documented DOM. The
brief calls it "of particular importance," so a few strong examples are high
value — but it must not block core-product coverage.

**Checklist**
- [ ] **Feasibility spike (time-boxed, ~1–2 days):** can Studio be driven via
      Playwright at all? Log the profile in, open Studio, DevTools-inspect the
      agent entry + response surface, and record whether selectors are stable
      enough for a run. Write findings to a short `docs/STUDIO_SPIKE.md`.
- [ ] Only if the spike is green: green-field prerequisite (app enum + journeys
      for `studio`, catalogue entry `J-STUDIO-AGENT` already present).
- [ ] `adapters/studio/` mirroring the closest working adapter from the spike's
      findings (likely Docs/Gmail for the panel, but Studio's agent loop may
      need a bespoke invoker).
- [ ] Author 2–3 high-severity guides exercising Studio's broad capabilities
      (tool misuse / **rogue_actions**, cross-content **data_exfiltration**).
- [ ] Run ≥3 trials, grade, export; confirm `J-STUDIO-AGENT` + real harm.

**Blocks pilot:** partial — high value, not core-gating. **Size:** L (spike first).

---

## Coordination with seed sourcing

Seeds are not the bottleneck — surfaces are. While seeds are sourced, steer them
toward the **thin harm categories** (unauthorized modification, workflow
disruption, phishing) and the **uncovered products** (Gmail/Calendar/Slides/
Studio), not more Docs/Sheets/exfil where coverage is already strong. Each
adapter ships its own harm-coverage guides so runs produce delivery records
immediately.
