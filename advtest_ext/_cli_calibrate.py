"""``advtest calibrate`` — learn the TAP search calibrators from the gold set.

Operator surface for :mod:`advtest_ext.core.search_calibration`. Kept in its
own module (like ``_cli_rescore``) so ``cli.py`` stays a thin registry;
``register`` is the only public symbol.

The TAP attack's search gradient keys off two learned calibrators — a
primary-judge calibration curve and per-partial-signal reliability weights —
but nothing populates them until an operator runs this. It reads the
expert-labelled gold set (``GoldLabel.expert_verdict``, the measurement
spine's ground truth) and fits both calibrators, writing
``judge_calibration.json`` and ``partial_signal_calibration.json`` where the
next run's TAP attack will load them. Calibrating against expert labels (not
the oracle's own verdict) is what keeps the fit honest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from advtest_ext.storage.models import GoldLabel

__all__ = ["register"]


def register(app: typer.Typer) -> None:
    """Attach the ``calibrate`` command to ``app``."""

    @app.command(name="calibrate")
    def calibrate(
        run_id: list[str] = typer.Option(  # noqa: B008
            [],
            "--run",
            help="Run id to calibrate from. Repeatable. Omit with --all for the whole gold set.",
        ),
        all_runs: bool = typer.Option(
            False, "--all", help="Calibrate from every gold label in the database."
        ),
        slice_: str = typer.Option(
            "all",
            "--slice",
            help=(
                "Which gold-label sampling slice to fit on: 'uniform', "
                "'disagreement', or 'all' (default — more labels, better curve)."
            ),
        ),
        judge_out: str | None = typer.Option(
            None,
            "--judge-out",
            help=(
                "Where to write judge_calibration.json. Defaults to the single "
                "target run's output_dir; required with --all or multiple runs."
            ),
        ),
        partial_out: str | None = typer.Option(
            None,
            "--partial-out",
            help="Where to write partial_signal_calibration.json (same defaulting as --judge-out).",
        ),
        min_labels: int = typer.Option(
            10,
            "--min-labels",
            help="Refuse to fit on fewer than this many gold labels (guards a garbage curve).",
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Report what would be learned, write nothing."
        ),
        db_url: str | None = typer.Option(
            None, "--db-url", help="Defaults to ADVTEST_DB_URL or sqlite:///./advtest.db."
        ),
    ) -> None:
        """Fit the TAP search calibrators from the expert-labelled gold set.

        Offline: reads gold labels, fits the two calibrators, writes their
        JSON. Never opens a browser or calls a model.

            advtest calibrate --run <id>
            advtest calibrate --all --judge-out out/judge_calibration.json \\
                --partial-out out/partial_signal_calibration.json
        """
        from pathlib import Path

        from advtest_ext._cli_providers import _resolve_db_url
        from advtest_ext.core.search_calibration import fit_search_calibrators
        from advtest_ext.storage import (
            get_run,
            list_gold_labels,
            make_engine,
            make_session_factory,
            session_scope,
        )

        if not run_id and not all_runs:
            raise typer.BadParameter("pass --run <id> (repeatable) or --all")
        if slice_ not in {"uniform", "disagreement", "all"}:
            raise typer.BadParameter("--slice must be uniform, disagreement, or all")

        resolved_db = _resolve_db_url(cli_flag=db_url, config_value=None)
        if not resolved_db:
            raise typer.BadParameter("no database configured; pass --db-url")

        sample_reason = None if slice_ == "all" else slice_
        factory = make_session_factory(make_engine(resolved_db))

        with session_scope(factory) as session:
            rows: list[GoldLabel] = []
            if all_runs:
                rows = list_gold_labels(session, sample_reason=sample_reason, limit=100_000)
            else:
                for rid in run_id:
                    rows.extend(
                        list_gold_labels(session, run_id=rid, sample_reason=sample_reason)
                    )

            # Resolve output paths while the session is open, so a single target
            # run can default to its own on-disk artifact directory — exactly
            # where the next TAP run loads its calibration files from.
            if judge_out and partial_out:
                resolved_judge, resolved_partial = judge_out, partial_out
            elif not all_runs and len(run_id) == 1:
                run = get_run(session, run_id[0])
                out_dir = run.output_dir if run is not None else None
                if not out_dir:
                    raise typer.BadParameter(
                        "run has no output_dir; pass --judge-out and --partial-out"
                    )
                root = Path(str(out_dir))
                resolved_judge = judge_out or str(root / "judge_calibration.json")
                resolved_partial = partial_out or str(root / "partial_signal_calibration.json")
            else:
                raise typer.BadParameter(
                    "pass --judge-out and --partial-out (no single run output_dir to default to)"
                )

        if len(rows) < min_labels:
            typer.echo(
                f"only {len(rows)} gold label(s) for the requested scope; "
                f"need >= {min_labels}. Review more traces, or lower --min-labels."
            )
            raise typer.Exit(code=1)

        result = fit_search_calibrators(
            rows,
            judge_calibration_path=resolved_judge,
            partial_calibration_path=resolved_partial,
            persist=not dry_run,
        )

        typer.echo(
            f"gold labels: {len(rows)} · judge pairs: {result.judge_pairs} "
            f"(skipped {result.skipped_no_score} without a score) · "
            f"partial-signal reviews: {result.partial_reviews} "
            f"(skipped {result.skipped_no_signals} that fired no signal)"
        )
        if result.judge_curve:
            lo = result.judge_curve[0]
            hi = result.judge_curve[-1]
            typer.echo(
                f"  judge curve: {len(result.judge_curve)} control points "
                f"(raw {lo[0]:.2f}->{lo[1]:.2f} … raw {hi[0]:.2f}->{hi[1]:.2f})"
            )
        for name, stat in sorted(
            result.partial_signal_stats.items(), key=lambda kv: kv[1].get("mean", 0.0), reverse=True
        ):
            typer.echo(
                f"  signal {name!r}: reliability {stat.get('mean', 0.0):.2f} "
                f"(n={int(stat.get('n', 0))})"
            )
        if dry_run:
            typer.echo("  (dry run — nothing written)")
        elif result.persisted:
            typer.echo(f"  wrote {resolved_judge}")
            typer.echo(f"  wrote {resolved_partial}")
        else:
            typer.echo("  nothing to fit (no scored / signal-bearing labels)")
