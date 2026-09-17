"""``advtest rescore`` — re-judge persisted traces without re-running
the attack.

Operator surface for :mod:`advtest_ext.core.rescore`. Kept in its own
module (like ``_cli_run_recipe``) so ``cli.py`` stays a thin registry;
``register`` is the only public symbol.

The command exists because a run scored by the wrong judge leaves the
whole downstream spine empty — no Findings (promotion gates on the
score), no review queue (sampling runs at completion), and therefore no
gold set and no calibrated ASR. Re-scoring rebuilds all of that from
stored responses for the price of one judge call per trace.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from advtest_ext.core.rescore import RescoreStats

__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Attach the ``rescore`` command to ``app``."""

    @app.command(name="rescore")
    def rescore(
        run_id: list[str] = typer.Option(  # noqa: B008
            [],
            "--run",
            help="Run id to re-score. Repeatable. Omit with --all for every run.",
        ),
        all_runs: bool = typer.Option(False, "--all", help="Re-score every run in the database."),
        judge_name: str = typer.Option(
            "decomposed_ipi",
            "--judge",
            help=(
                "Judge to score with. 'decomposed_ipi' emits separable "
                "attended/complied/harmful_effect sub-verdicts and is the "
                "right default for IPI traces."
            ),
        ),
        threshold: float = typer.Option(
            0.5, "--threshold", help="Success cut for verdict + Finding promotion."
        ),
        review_budget: int = typer.Option(
            0,
            "--review-budget",
            help=(
                "How many traces to enqueue for expert review after scoring "
                "(0 = re-score only, don't touch the queue)."
            ),
        ),
        uniform_fraction: float = typer.Option(
            0.6,
            "--uniform-fraction",
            help=(
                "Share of the review budget spent on the UNIFORM slice — the "
                "one that keeps the PPI estimate unbiased. Keep it high at "
                "cold start."
            ),
        ),
        seed: int = typer.Option(0, "--seed", help="Sampler seed (reproducible queues)."),
        output_dir: str | None = typer.Option(
            None,
            "--output-dir",
            help="Where replay bundles are written for promoted Findings.",
        ),
        limit: int | None = typer.Option(
            None, "--limit", help="Cap traces per run (smoke-testing)."
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Score and report, write nothing. Shows verdict churn first.",
        ),
        db_url: str | None = typer.Option(
            None, "--db-url", help="Defaults to ADVTEST_DB_URL or sqlite:///./advtest.db."
        ),
    ) -> None:
        """Re-score stored traces with a different judge, then refill the
        review queue.

        Offline: reads persisted responses, makes one judge call per
        trace, never opens a browser or contacts a target.

            advtest rescore --all --judge decomposed_ipi --dry-run
            advtest rescore --all --judge decomposed_ipi --review-budget 40
        """
        from advtest_ext._cli_factories import _resolve_judge
        from advtest_ext._cli_providers import _resolve_db_url
        from advtest_ext.core import load_seed_behaviors
        from advtest_ext.core.rescore import RescoreStats, rescore_traces
        from advtest_ext.storage import (
            list_runs,
            make_engine,
            make_session_factory,
            session_scope,
        )

        if not run_id and not all_runs:
            raise typer.BadParameter("pass --run <id> (repeatable) or --all")

        resolved_db = _resolve_db_url(cli_flag=db_url, config_value=None)
        if not resolved_db:
            raise typer.BadParameter("no database configured; pass --db-url")

        judge = _resolve_judge(judge_name)
        try:
            behaviors = list(load_seed_behaviors())
        except Exception:
            behaviors = []

        factory = make_session_factory(make_engine(resolved_db))

        with session_scope(factory) as session:
            targets = [r.id for r in list_runs(session, limit=1000)] if all_runs else list(run_id)

        if not targets:
            typer.echo("no runs found")
            raise typer.Exit(code=1)

        typer.echo(
            f"rescoring {len(targets)} run(s) with {judge_name!r} "
            f"(threshold {threshold}){' [DRY RUN]' if dry_run else ''}"
        )

        totals = RescoreStats()

        # ONE event loop for every run. Calling asyncio.run() per run
        # would close the loop between runs while the judge's HTTP
        # client still holds connections bound to it — the second run
        # then dies with "Event loop is closed" during client teardown.
        async def _rescore_all() -> None:
            for rid in targets:
                with session_scope(factory) as session:
                    stats = await rescore_traces(
                        session,
                        run_id=rid,
                        judge=judge,
                        behaviors=behaviors,
                        threshold=threshold,
                        review_budget=review_budget,
                        uniform_fraction=uniform_fraction,
                        seed=seed,
                        output_dir=output_dir,
                        dry_run=dry_run,
                        limit=limit,
                    )
                # Echo inside the loop so a long multi-run pass reports
                # progress as it goes rather than all at the end.
                _echo_run(rid, stats)
                _accumulate(totals, stats)

        asyncio.run(_rescore_all())

        typer.echo("")
        typer.echo(
            f"TOTAL  {totals.n_scored}/{totals.n_traces} scored · "
            f"{totals.n_changed} verdict change(s) · "
            f"{totals.n_signal_upgrades} L0 upgrade(s) · "
            f"{totals.n_findings} finding(s) · "
            f"queue +{totals.n_enqueued_uniform}u/{totals.n_enqueued_disagreement}d"
        )
        if totals.errors:
            typer.echo(f"  {len(totals.errors)} judge error(s); first: {totals.errors[0][1]}")
        if dry_run:
            typer.echo("  (dry run — nothing written)")

    def _echo_run(rid: str, stats: RescoreStats) -> None:
        typer.echo(
            f"  {rid}: {stats.n_scored}/{stats.n_traces} scored, "
            f"{stats.n_changed} changed, {stats.n_findings} finding(s)"
            + (f", {stats.n_skipped_empty} empty" if stats.n_skipped_empty else "")
        )
        # Show a few concrete flips — the operator needs to see that the
        # numbers moved for a reason, not just that they moved.
        for trace_id, old, new in stats.verdict_changes[:3]:
            typer.echo(f"      {trace_id[:16]} {old or '(none)'} -> {new}")
        if stats.n_changed > 3:
            typer.echo(f"      ... and {stats.n_changed - 3} more")

    def _accumulate(totals: RescoreStats, stats: RescoreStats) -> None:
        totals.n_traces += stats.n_traces
        totals.n_scored += stats.n_scored
        totals.n_skipped_empty += stats.n_skipped_empty
        totals.n_signal_upgrades += stats.n_signal_upgrades
        totals.n_findings += stats.n_findings
        totals.n_enqueued_uniform += stats.n_enqueued_uniform
        totals.n_enqueued_disagreement += stats.n_enqueued_disagreement
        totals.verdict_changes.extend(stats.verdict_changes)
        totals.errors.extend(stats.errors)
