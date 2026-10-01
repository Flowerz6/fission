# TAP Attack-Success Upgrades

Changes to `TapCrescendoAttack` (`advtest_ext/attacks/tap_crescendo.py`) and its
search policy (`advtest_ext/attacks/tap_search.py`) that sharpen the search
gradient and stop it wasting budget. One defect fix plus six improvements, with
their operator surface. Everything that could change a default run is opt-in or
inert until fitted; corrections are on by default.

## Defect fixed: verifier quorum was half-wired

The verifier-quorum feature voted across the full checker set only in candidate
acceptance (`_verify_candidate`). The two verifier calls that actually steer the
live search still used a single checker:

- `_check_on_mechanism` — the branch-drift pruner, and
- `_reflect_node` — the per-node reflection.

Both now iterate `self._checkers` and require the quorum. Consequence of the old
code: a run configured with plural `checkers` but no singular `checker` silently
disabled on-mechanism pruning and reflection entirely. Regression test:
`tests/test_tap_upgrades.py::test_on_mechanism_pruning_works_with_only_plural_checkers`.

## 1. Primary-judge calibration

The per-turn judge score is the whole search gradient — ranking, pruning, and the
TextGrad trigger all key off it — but it was uncalibrated. `JudgeCalibrator`
(`tap_search.py`) learns a monotonic isotonic map (Pool-Adjacent-Violators) from
a raw judge score to the empirical success rate observed at that score band, and
`_send_and_score` applies it before the score becomes the gradient. Identity
until fitted, so an unconfigured run is unchanged. The raw pre-calibration score
is persisted per turn (`extra_metadata.tap_crescendo.judge_score_raw`).

## 2. Judge-error carry-forward

A judge exception used to score the turn a hard `0.0`, indistinguishable from a
real failure — it tripped stall/stuck detection and pruned live branches. The
turn now carries the previous turn's score, is flagged
(`extra_metadata.tap_crescendo.judge_error`), and is excluded from stall
accounting. Deterministic ground-truth stop signals still apply.

## 3. Graded on-mechanism drift

`_check_on_mechanism` returns the fraction of checkers voting OFF, not a binary
verdict. A turn counts toward the drift streak only when that fraction meets
`onmech_drift_threshold` (default 0.5), so one dissenting checker cannot prune a
live branch. `None` (no parseable verdict) is still never a prune.

## 4. Cross-sibling refusal memory

Framings that hit a refusal are recorded (`_record_refused_framing`) and injected
into the strategist prompt as an explicit avoid-list, so sibling branches stop
re-proposing a framing that already hit the same wall. Surfaced in the search
report as `refused_framings`.

## 5. Best-prefix rewind (opt-in)

`rewind_max_attempts` (default 0 = disabled). When a generated node stalls,
instead of abandoning it the node resets the target, replays its highest-scoring
prefix (`_replay_prefix`), and escalates again from the peak. Replayed turns are
tagged `extra_metadata.tap_crescendo.replayed`.

## 6. Seed refinement and drift-checking

Adaptive-seed nodes now open a TextGrad refine budget when a turn under-performs
its reference, and are subject to on-mechanism drift pruning. Strict
byte-for-byte replay is never rewritten or pruned (`_refine_applies` /
`_onmech_applies`).

## Operator surface

### `advtest calibrate` — fit the calibrators from the gold set

The two calibrators learn from expert ground truth
(`GoldLabel.expert_verdict`), never the oracle's own verdict — calibrating the
oracle against itself is circular. `advtest_ext/core/search_calibration.py` turns
gold rows into fit inputs; the CLI (`advtest_ext/_cli_calibrate.py`) reads them
and writes the calibration files where the next run loads them.

```
advtest calibrate --run <run-id>            # writes into that run's output_dir
advtest calibrate --all \
    --judge-out out/judge_calibration.json \
    --partial-out out/partial_signal_calibration.json
advtest calibrate --run <run-id> --dry-run  # report only, write nothing
```

`--slice` (default `all`) selects the gold-label sampling slice; `--min-labels`
(default 10) refuses to fit on too little data.

### Dashboard: Search-coverage panel

The run page (`/runs/{id}`) renders a **Search coverage** panel from the run's
`tap_search_report.json`: MAP-Elites cell coverage, verifier quorum, judge
calibration status, rewind budget, planner/executor/verifier models, refused
framings, causal controls, and the cells never explored. Hidden entirely when a
run produced no report (non-TAP or older runs), so it never fabricates coverage.
Loaded by `_load_search_report` into `_build_run_context`; template
`runs/_search_report.html`.

## Configuration reference

New `TapCrescendoAttack` constructor params (all with safe defaults):

| Param | Default | Effect |
|---|---|---|
| `judge_calibration_path` | `None` | Load/apply the judge calibration curve. |
| `onmech_drift_threshold` | `0.5` | OFF-vote fraction that counts toward drift. |
| `rewind_max_attempts` | `0` | Best-prefix rewind budget (0 disables). |
| `checkers` | `[]` | Extra family-disjoint verifiers for the quorum. |
| `verifier_quorum` | all checkers | Votes required to prune / accept / reflect. |

Guide runs wire the calibration/report paths automatically in
`advtest_ext/guides/run.py`; the CLI exposes them via `_cli_factories.py`.

## Remaining validation

Best-prefix rewind (§5) is tested against fakes, including a direct check that
`_replay_prefix` resets the target and re-sends the best prefix verbatim in order.
It reuses only target methods every run already exercises (`reset`,
`render_document`, `invoke`), so it needs no new target capability. A single live
smoke run against a real UI-mode Gemini session is still the recommended final
check before enabling it in production, since side-panel replay timing has not
been exercised against a live target here.

## Checkpoint identity and measurement

A TAP checkpoint can be reused only when `attack_options.tap_crescendo.checkpoint_identity`
contains nonempty immutable fingerprints for `guide`, `models`, `evaluator`,
`config`, and `environment`, and all five match the saved checkpoint. A display
name is insufficient. Missing, legacy, or changed identity causes a fresh run.
The adaptive-turn SEM in the search report describes variation over turns in
one evolving conversation; it is not a confidence interval for repeatability.
Measure repeatability through independent validation episodes and export those
with the pilot validation workflow in `docs/PILOT_DELIVERY_FORMAT.md`.

Archive reports now expose two separate measures. `search_coverage` is the
fraction of declared mutation cells containing any elite. `search_success_coverage`
is the fraction containing a node that cleared the configured search success
gate. Neither is a count of independently reproduced pilot findings; use the
validation episode export for that count.
