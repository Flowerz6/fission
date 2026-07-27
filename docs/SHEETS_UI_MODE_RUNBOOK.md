# Sheets ui_mode runbook — run Bikash's guide live against Gemini

The Sheets multi-turn adapter (`google_sheets_multiturn`) is built and unit-
tested, but its **side-panel selectors are seeded from Docs and need one
live validation pass** before a real run — the Gemini side panel is the same
component across Workspace apps, so most carry over, but the toolbar entry
button is the likely divergence. This runbook takes you from a fresh checkout
to a live run of `guides/sheets_skill_leak.json` (source: human:bikash) whose
traces, review queue, and calibrated ASR ± CI show up in the dashboard.

> ⚠️ This drives a **real headed browser + real Gemini** against a Google
> account. Only use test tenants / accounts you own or are authorized to test.

---

## 1. One-time setup

```bash
poetry install --extras all
poetry run playwright install chromium

cp .env.example .env         # then fill in:
#   ANTHROPIC_API_KEY   — the CLAUDE_ATTACKER role (the red-team LLM)
#   GEMINI_API_KEY      — not needed for ui_mode (the side panel IS the model)
#   ADVTEST_PLAYWRIGHT_PROFILE_DIR — a persistent Chrome profile dir

# Log the profile into Google ONCE (interactive — only you can do this):
poetry run python scripts/setup_playwright_profile.py
# sign into the test account, open a Sheet, confirm the Gemini side panel opens.
```

`configs/dev.yaml` already defines the `CLAUDE_ATTACKER` role. The attacker
LLM needs `ANTHROPIC_API_KEY`.

---

## 2. Validate the Sheets selectors (~15 min, one time)

The selectors live in `advtest_ext/adapters/sheets/ui_mode/selectors.py`,
seeded from Docs. Confirm them against live Sheets and heal any drift into the
`google_sheets` overlay (no code edit needed):

```bash
# Manual heal against a Sheet you have open (the profile must be logged in):
poetry run python scripts/heal_selectors.py \
    --manual --url "https://docs.google.com/spreadsheets/d/<your-sheet-id>/edit" --apply
export ADVTEST_SELECTOR_OVERLAY=$PWD/out/selector_overlay.json
```

What to check by DevTools-inspecting the open Gemini panel on a Sheet:

| Selector key | Most likely status | If it drifts |
|---|---|---|
| `side_panel_button` | **verify first** — the toolbar "Ask Gemini" entry may differ on Sheets | broaden to `button[aria-label*="Gemini" i]` |
| `side_panel_container` | usually identical (`[role="complementary"][aria-label="Gemini"]`) | check the panel's accessible name |
| `side_panel_input` / `side_panel_send` / `side_panel_response` / `side_panel_loading_indicator` / `side_panel_new_conversation` / `side_panel_close` | shared `appsElementsSidekick…` panel classes — usually carry over | re-inspect and heal |

Heals are stored under the **`google_sheets`** overlay key, separate from the
Docs overlay, so a Docs heal and a Sheets heal never collide.

**Multiple-choice / suggestion options.** Sheets Gemini sometimes replies with
a clickable multiple-choice *questionnaire* (`appsElementsSidekickQuestionnaireRoot`
-> a `QuestionList` of options) or an inline *suggestion* list
(`appsElementsSidekickInlineSuggestionListSuggestionListContainer`) instead of a
plain text bubble. Confirmed live (DevTools, 2026-07) that these are
**non-blocking** — you can ignore the options and just type the next turn, and
Gemini replies. So the invoker only needs to *capture* the option labels (via
`_SUGGESTION_CHIP_SELECTOR` in `invoker.py`, pinned to those two container
classes) and fold them into the reply text, so the attacker LLM sees the offered
options and its next turn can respond by typing. No auto-click is needed. The
question *prompt* itself lives in the normal `AgentMessageBubbleContent`, so a
questionnaire reply is captured as `<question text>\n\n[Gemini asked you to
choose: ...]` — not an empty read.

**Model thoughts (reasoning capture).** Gemini shows a collapsible "Model
thoughts" disclosure with its chain-of-thought. The invoker captures it (via
`_read_model_thoughts` / `_MODEL_THOUGHTS_*` selectors in `invoker.py`) into
`Response.raw["model_thoughts"]` and hands it to the **attacker** (the
tap_crescendo transcript labels it "Target's internal reasoning") so the
attacker can adapt to WHY Gemini is refusing. It is deliberately kept OUT of
`Response.text`: the judge scores `.text`, and reasoning that merely *names* a
forbidden skill while deciding to refuse would otherwise trip the deterministic
leak regex into a false positive. These are **best-guess selectors to validate
live** — DevTools-inspect the "Model thoughts" dropdown and confirm the toggle
control and the reasoning-body classes (and whether the body text is present
when collapsed or only after expanding).

---

## 3. Run Bikash's guide live

`google_sheets` guides now default to the `google_sheets_multiturn` target, so:

```bash
poetry run python scripts/run_guide.py \
    --guide sheets_skill_leak \
    --browser-mint \
    --review-sample-budget 12 \
    --output-dir ./out
```

- `--browser-mint` mints one blank host Sheet through the logged-in browser
  (no Sheets API / OAuth) and reuses it across tree nodes.
- `--review-sample-budget 12` enqueues traces for expert review (feeds the
  gold set → PPI). DB persistence is on by default (`ADVTEST_DB_URL` or
  `sqlite:///./advtest.db`).
- The run maps Bikash's guide → `tap_crescendo`: the **root node replays his
  exact exemplar turns**, then the tree branches into **on-goal variants**
  (the branch strategist is now seeded from his strategy, not the default
  mail steering — see `strategy_brief`). His `success.signals` (the
  `get_skills`/`execute_plan` regex, the "Usage of unsecure function" token)
  are the deterministic L0 tells that confirm a leak.

Watch the headed Chrome: the side panel should open, replay his QA-assessment
opener, and escalate. If a selector misses you'll get a
`SidePanelUnavailable` drift error + a screenshot under `out/failures/` — heal
that selector (step 2) and re-run.

---

## 4. See the numbers

```bash
poetry run uvicorn "advtest_ext.dashboard:create_app" --factory --port 8000
# open http://localhost:8000
```

- **Runs** / **run detail** — the attack-tree explorer shows the root replay +
  the branched variants; nodes that leaked go green.
- **Review** (`/hitl`) — label the queued traces (~30s each) → gold labels.
- **Overview** / run hero — once you've labelled the uniform slice, the
  calibrated **ASR ± CI** appears (PPI).

---

## Notes / limitations

- The Sheets invoker is a focused port of the Docs one; it drops the Docs
  canvas body-reading and the "Resolve Gemini suggestions" edit gate (a
  skill-leak reads the panel reply, not a sheet body). If a future Sheets
  guide needs to read sheet cell content, that's an additive extension.
- This adapter is intentionally isolated from the Docs adapter (Option 1). If
  both prove stable live, collapsing them onto a shared side-panel base is a
  clean follow-up.
