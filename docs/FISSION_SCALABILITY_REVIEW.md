# Fission — Production Scalability Review

> A code-grounded review of what must change before Fission can be deployed as a service,
> not run as one operator's local tool. Findings cite `file:line`. Priorities: **P0** blocks
> a real deploy, **P1** is needed before real load or customers, **P2** is maturity.
>
> Companion to [FISSION_ARCHITECTURE_AND_PRODUCTION.md](FISSION_ARCHITECTURE_AND_PRODUCTION.md)
> (the gap analysis) and [FISSION_DEPLOYMENT.md](FISSION_DEPLOYMENT.md) (the topology and
> staged rollout). This doc is the *evidence*: where in the code each blocker lives.

---

## 1. Verdict

Deployability splits cleanly into two lanes with very different readiness.

| Lane | State | Can it deploy? |
|---|---|---|
| **Dashboard / read + report service** | Containerized, health probes, optional auth, Postgres path works, CI/CD builds and pushes an image with a gated Cloud Run deploy | **Close.** A handful of P0 fixes (event-loop blocking, worker model) and it serves a team. |
| **Run engine (api_mode targets)** | Single-process asyncio, sync DB on the hot path, no retries, no run state machine, no queue | **Not yet.** Needs the P0 engine fixes; then it scales to modest parallel load on one box. |
| **Run engine (ui_mode / browser farm)** | Headed Chrome only, profiles on local disk, concurrency pinned to 1, no account rotation or flag detection | **No.** This is a rebuild, not a config change. It is ~80% of the deployment effort and is gated on the Google test-tenant ask. |

**One-line answer:** the dashboard can be deployed soon; the api_mode engine after the P0
engine fixes; the ui_mode browser farm is the real project and should be sequenced last,
behind the Google asks.

The single most pervasive defect, found independently in the orchestrator, storage, and
dashboard, is **synchronous SQLAlchemy calls made from inside `async def` on the event
loop**. It silently serializes the concurrency the code appears to offer. Fix this first;
it unblocks the most with the least.

---

## 2. P0 — blocks a real deploy

### P0-1. Sync DB writes on the asyncio event loop (three subsystems, one root cause)
The run loop is asyncio, but the entire storage layer is synchronous SQLAlchemy, called
directly from coroutines with no thread offload. A slow write (network Postgres, or an
SQLite lock wait) stalls the whole loop and every concurrent worker, so raising
`max_concurrency` buys little.
- **Orchestrator:** `_record_trace` (async, `core/orchestrator.py:555`) makes up to five
  separate blocking sessions per trace — `_db_persist_trace`, `_db_persist_signals`,
  `_db_update_verdict`, `_db_update_uncertainty`, `_maybe_promote_finding`
  (`orchestrator.py:718-882`). Each opens its own `session_scope` → commit → close
  (`storage/database.py:123-138`). A `self._db_lock` was created for batching
  (`orchestrator.py:384`) and is dead code.
- **Dashboard:** `async def` routes depend on a sync `Session` (`dashboard/app.py:734-745`)
  and call blocking queries in the coroutine (`_build_run_context`, `app.py:929-939`). The
  2-second HTMX pollers (`/_hero`, `/_findings`, `/_tree.json`) make it acute.
- **Fix:** batch the per-trace writes into one transaction; run DB work off-loop
  (`asyncio.to_thread` / `run_in_executor`) or move to the SQLAlchemy async engine +
  asyncpg. For the dashboard, making routes plain `def` lets FastAPI run them in its
  threadpool as a fast interim fix.

### P0-2. No retries, backoff, or circuit breakers on any network call
`tenacity` is a declared dependency (`pyproject.toml:18`) but **unused** everywhere. A
single transient 429 or 5xx from a provider or target permanently burns that cell as an
error and consumes a budget slot.
- Providers build clients with no retry/timeout config: `providers/anthropic.py:105`,
  `providers/gemini.py:57,83`. No handler for `429` / `RateLimitError` / `overloaded`.
- The engine calls `target.invoke` / `judge.score_response` exactly once
  (`orchestrator.py:185-191`).
- ui_mode retries are *blind*: capped at 3 with no backoff (`recipe_runner.py:81,294-343`),
  which makes Google's "unusual activity" trip worse, not better.
- **Fix:** wrap provider/target/judge calls in bounded exponential-backoff-with-jitter for
  transient classes only; add a per-target circuit breaker so a dead target fails fast
  instead of grinding the whole grid into error traces.

### P0-3. No run state machine; a crashed run is orphaned
The `Run` row has only `started_at` and a nullable `finished_at` (`storage/models.py:73-75`)
— no status enum, heartbeat, or lease. If the process dies mid-run, the row stays
`finished_at IS NULL` forever, indistinguishable from a live run. There is no resume: the
loop always rebuilds fresh attack iterators (`orchestrator.py:940-945`) and never consults
completed traces to skip done cells, though `insert_trace` is already idempotent on
`trace.id` (`repository.py:108-110`), which would support one.
- **Fix:** add `status` (running/completed/crashed) + a heartbeat/lease column; reconcile
  stale `running` rows on startup; add a resume mode that skips already-persisted cells.

### P0-4. Cost and query caps default to None; a runaway run is unbounded
All three `Budget` caps default to `None` (`core/models.py:312-314`), so out of the box
there is no ceiling. Worse, the `CostLedger` is fed almost only *attacker* usage
(`attacks/pair/generator.py:193`, `tap_crescendo.py:1778`); judge and target usage are
largely unrecorded, and pricing is placeholder (Gemini priced at `0.0`,
`providers/cost.py:28`; unknown models return `0.0`, `cost.py:43`). So `max_cost_usd`
under-counts the dominant judge cost even when set.
- **Fix:** ship non-null default caps; record judge and target usage in the ledger; fill
  real per-model prices before relying on the cap.

### P0-5 (ui_mode). Headed Chrome + local-disk profiles + concurrency-1
Any one of these prevents fleet deployment; together they mean one operator, one machine,
one Chrome at a time.
- Headed is hardcoded: `headless=False` at `_ui_mode_common/session.py:48` and in every
  invoker, no env override.
- Persistent profiles (carrying signed-in Google cookies) live under `~/.advtest/...`
  (`config.py:35`, `_cli_run_recipe.py:407-420`); a fresh container has no such dir, so
  Chrome comes up logged out.
- Concurrency is pinned to 1 for ui_mode by design because all runs share one persistent
  profile (`_cli_factories.py:582-584`); the session pool reuses sessions only *within* a
  run (`session_pool.py`), so every run cold-launches.
- **Fix:** parameterize `headless`; hydrate profile dirs from a secret/artifact store at
  task start; shard by profile (N profiles → N workers, one Chrome each) with a real
  cross-run warm-session pool. This is the browser-farm build, not a patch.

---

## 3. P1 — needed before real load or customers

### P1-1 (ui_mode). No account-flag, rate-limit, or challenge detection; no rotation
Nothing in the ui_mode/account path matches `unusual activity|challenge|captcha|rotate|
cooldown`. A flagged account just fails selector matching and surfaces as a generic
`SidePanelUnavailable` (`invoker.py:130`, `errors.py`). There is no spare-account rotation.
- **Fix:** add a challenge-detection selector set raising a distinct `AccountFlagged`;
  add account cooldown + rotation in the account/session pool.

### P1-2 (ui_mode). Failure signals collapse into one exception
Selector-drift, account-flag, generation-timeout, and a genuine model refusal all raise the
same `SidePanelUnavailable` (`invoker.py:130,197,253`), and the recipe runner retries all of
them identically (`recipe_runner.py:329-331`). You cannot tell harness failure from attack
success in the trace, which corrupts the metric.
- **Fix:** subclass the exception per failure class; emit a harness-health signal separate
  from the attack verdict.

### P1-3 (ui_mode). Selector fragility is structural, with no runtime drift gate
Selectors are hand-maintained strings ("re-validate before each demo run",
`gmail/ui_mode/selectors.py:1-17`), and Docs/Gmail/Sheets/Slides share the Sidekick
component, so one Workspace UI change breaks all products at once. A heal loop
(`selector_heal.py`) and overlay exist but are operator-run, not in the invoke path.
- **Fix:** run drift detection as a pre-flight health gate at run start; wire the heal loop
  + overlay into an automated pre-run step.

### P1-4. Storage is not multi-process safe, and Postgres has two sharp edges
- SQLite is the default (`core/models.py:442`) with no WAL mode and no `busy_timeout`
  pragma (`storage/database.py`), so concurrent workers plus the dashboard reader will hit
  `database is locked`.
- The engine sets no pool tuning and, critically, no `pool_pre_ping` (`database.py:110`);
  after a Postgres/pgbouncer idle timeout the first reuse raises.
- The additive-migration helper emits `DEFAULT 1`/`0` for booleans (`database.py:43-44`),
  which Postgres rejects on a BOOLEAN column, so any new boolean model field breaks
  self-heal on an existing Postgres DB.
- **Fix:** require Postgres for multi-process; set `pool_pre_ping`, `pool_recycle`, and a
  pool sized to worker concurrency; emit `TRUE`/`FALSE` dialect-aware; adopt Alembic (see
  P2-2).

### P1-5. In-memory trace list grows unbounded for the whole run
`self._traces` is appended every attempt and never trimmed (`orchestrator.py:373,561`),
copied again into `RunResult` (`:459`), and multi-phase runs accumulate every phase's traces
resident at once (`cli.py:229,247`). Each trace holds full raw request/response. Memory
scales with total attempts × response size.
- **Fix:** stream/flush traces (already persisted per-trace) and compute `SummaryStats`
  incrementally instead of retaining the full list.

### P1-6. No global outbound-LLM concurrency cap; per-call client construction
The only limiter is the per-pair semaphore (`orchestrator.py:919`); inside a pair the
ensemble fans out via `asyncio.gather` with no sub-limit (`judges/ensemble.py:86`). Raising
`max_concurrency` or adding judges fans out unbounded to the provider. Each provider call
also builds a fresh client and re-resolves the API key (`anthropic.py:105,82`;
`gemini.py:57`), defeating connection reuse.
- **Fix:** add a shared provider-level semaphore or token bucket; construct one client per
  provider instance and reuse it.

### P1-7. Judge fan-out is the dominant cost, unbudgeted and uncached
Worst case per trace is ~3 ensemble judges + 1 confirm judge + k checkers, each up to 3×
on parse-retry (`decomposed_ipi.py:232-237`), so order 10+ LLM calls per trace per turn.
No judge-result caching exists, and `CachedAnthropicProvider` (`providers/anthropic_cached.py`)
that would prompt-cache the rubric is **dead code** — nothing constructs it.
- **Fix:** wire prompt caching (or a result cache keyed on rubric+response) into the judge
  path; cap judge fan-out.

### P1-8. Dashboard is not safe to run with multiple workers
`LiveTestController` holds a live `subprocess.Popen`, status, and a log `deque` in process
(`dashboard/live_tests.py:209-215`). A run started on worker A is invisible to worker B, so
start/stop/status break behind a load balancer. The Dockerfile also runs a single uvicorn
process with no `--workers` and no gunicorn.
- **Fix:** externalize run state (DB/Redis) or move run orchestration out of the web process
  into a dedicated worker/queue; then run multiple replicas.

### P1-9. Artifact-store abstraction is bypassed by local-disk writes
Only the replay ZIP uses the `ArtifactStore`/GCS abstraction. Per-trace JSON
(`orchestrator.py:283-288`), the JSONL sink (`storage/jsonl.py:51`), and the screenshot
source read by the replay bundler (`storage/replay.py:135-138`) all touch local disk. On an
ephemeral worker these are lost.
- **Fix:** route per-trace JSON, JSONL, and screenshots through the artifact store.

---

## 4. P2 — maturity

- **Multi-phase warmup:** each phase builds a fresh orchestrator and target and calls
  `setup()` again (`cli.py:231-246`, `orchestrator.py:416`), so a browser target is torn
  down and relaunched per phase. Reuse the target across matching phases.
  (`orchestrator.py:444`)
- **No Alembic:** `create_all` + an ADD-COLUMN-only self-heal (`database.py:52-82`) never
  creates new indexes on existing tables or handles type/constraint changes. Adopt Alembic
  before first prod deploy.
- **Missing indexes on dashboard ORDER BY columns:** `Trace.timestamp`, `Finding.promoted_at`,
  `HitlItem.enqueued_at`, `GoldLabel.reviewed_at` are unindexed (`repository.py:337,350,840,579`),
  forcing filesorts on large runs.
- **Heavy unpaginated queries:** `_overview_run_diff` pulls `limit=100_000` twice into memory
  (`app.py:239`); `_build_run_context` loads a whole run and reads intercepted files per trace
  (`app.py:113-138`); `build_overview` is N+1 over `run.traces` (`overview.py:89-90`);
  `build_surfaces_context` runs unscoped full-table scans (`app.py:368-438`). Paginate and
  aggregate in SQL.
- **Per-process budget only:** `max_cost_usd`/`max_queries` and the cost ledger are
  per-process (`orch_budget.py`, `providers/cost.py`), so caps are not enforced across
  workers. Centralize if global budget matters.
- **Planted-artifact leakage:** browser-minted Sheets/Docs are deliberately left in Drive
  (`sheets/ui_mode/multi_turn_target.py:121-125`), and planted emails have no cleanup.
  Track minted artifact ids and sweep them.
- **Browser/process leak on hard kill:** teardown is best-effort (`session.py:202-220`); a
  SIGKILL leaks the Chrome subprocess and a profile lock that blocks the next run. Add a
  process-group reaper and stale-lock clearing at launch.
- **MCTS is fully serial:** `max_concurrency` is ignored by the MCTS strategy
  (`orchestrator.py:1112-1146`); document or batch it.

---

## 5. What is already in good shape

- **Blast-radius containment:** a raising target/judge becomes an error-verdict trace, not a
  run abort, and a failing attack iterator aborts only its own pair
  (`orchestrator.py:519-543,965-978`). Errors are excluded from the ASR denominator.
- **Dashboard auth:** optional bearer middleware with constant-time compare, health probes
  exempted (`app.py:691-704`). Just ensure `ADVTEST_DASHBOARD_TOKEN` (or IAP) is set in prod;
  it is commented out in `docker-compose.yml`.
- **Health probes:** `/healthz` (liveness) and `/readyz` (DB `SELECT 1`, 503 on failure)
  exist and are wired into the compose healthcheck.
- **Postgres portability:** the schema is otherwise portable (JSON not JSONB, tz-aware
  DateTime, integer PKs); the one Postgres-only break is the boolean default in P1-4.
- **CI/CD:** CI runs ruff, mypy, and pytest on 3.11/3.12; CD builds and pushes an image to
  GHCR with a gated Cloud Run deploy (`.github/workflows/ci.yml`, `cd.yml`).
- **Idempotent trace insert** (`repository.py:108-110`) already supports at-least-once
  redelivery, which a resume path (P0-3) can build on.

---

## 6. Recommended sequencing

1. **Unblock the engine (P0-1, P0-2, P0-3, P0-4).** Off-loop/batched DB writes, retries with
   backoff, a run status + lease, and real budget caps. This makes the api_mode engine
   deployable on one box at modest concurrency, and makes runs survivable.
2. **Make the dashboard a real service (P0-1 dashboard, P1-8).** Unblock its routes, move run
   orchestration out of the web process, then run replicas. Ship it first — it is closest.
3. **Harden storage for multi-process (P1-4, P1-9, P2 Alembic + indexes).** Postgres with
   pool_pre_ping, artifact store for all outputs, migrations.
4. **Tame LLM cost and concurrency (P1-6, P1-7).** Shared provider limiter, client reuse,
   judge caching. Do this before turning concurrency up.
5. **Build the browser farm (P0-5, P1-1, P1-2, P1-3).** Headless support, profile hydration,
   per-profile sharding + warm pool, account rotation, flag detection, a runtime selector
   health gate. Sequence last and gate on the Google test-tenant/allowlist ask, which decides
   whether the farm is tractable at all.

The dependency order matters: the event-loop fix (P0-1) is a prerequisite for every
throughput gain, and the Google ask is a prerequisite for the browser farm being worth
building. Everything else fits between those two.
