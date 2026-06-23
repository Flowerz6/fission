"""Integration tests for the orchestrator → SQLAlchemy → dashboard
chain (Prompt 11).

The orchestrator now writes Run / Trace / Finding rows to a
configured SQLAlchemy database in parallel with the existing JSON
sink. These tests lock the wiring three ways:

1. An orchestrator handed a ``session_factory`` materialises rows
   the dashboard's repository helpers can read back.
2. An end-to-end pass — orchestrator persists to an in-memory
   SQLite, dashboard's TestClient hits ``/runs`` against the same
   engine, the seeded run shows up.
3. ``advtest import-run`` reads an on-disk run directory and
   inserts idempotently — re-importing doesn't violate the trace
   primary key, and the second pass updates rather than duplicates.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from advtest_ext.attacks.base import (
    AttackAttempt,
    AttackPlugin,
    AttackPluginClass,
)
from advtest_ext.cli import app
from advtest_ext.core.models import Behavior, Budget, Payload
from advtest_ext.core.orchestrator import GridOrchestrator
from advtest_ext.dashboard import create_app
from advtest_ext.document import StubDocumentTarget
from advtest_ext.judges.base import JudgeBase
from advtest_ext.storage import (
    list_findings,
    list_runs,
    list_traces_for_run,
    make_engine,
    make_session_factory,
    session_scope,
)

_RUNNER = CliRunner()


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------


class _SuccessAttack(AttackPlugin):
    """Yields one attempt that always scores HIGH (judge picks it
    up as success). Used to verify auto-promotion to Findings runs
    end-to-end."""

    attack_class: AttackPluginClass = "single_turn"

    def __init__(self) -> None:
        self.name = "success_attack"
        self.references = ["test"]
        super().__init__()

    async def generate(
        self,
        *,
        behavior: Any,
        target: Any,
        budget: Any,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        del target, budget, context
        yield AttackAttempt(
            payload=Payload(
                id=f"sa-{behavior.id}-0",
                text="HIGH",
                modality="text",
                generator_name=self.name,
            ),
            user_prompt="HIGH",
        )


class _AlwaysHighJudge(JudgeBase):
    name: str = "always_high"
    references: list[str] = []  # noqa: RUF012

    async def evaluate(self, *, trace: Any, behavior: Any) -> Any:
        del trace, behavior
        from advtest_ext.core.models import JudgeScore

        return JudgeScore(
            judge_name=self.name,
            score=0.95,
            reasoning="constant high",
            flagged=True,
            judge_version="test",
        )


def _behavior() -> Behavior:
    return Behavior(
        id="exfil_via_doc",
        description="exfiltration via embedded doc",
        success_criteria="hits canary",
        severity="high",
        source_citation="test",
        category="exfiltration",
    )


# ----------------------------------------------------------------------
# 1. Orchestrator persists to the database
# ----------------------------------------------------------------------


async def test_orchestrator_persists_run_trace_finding_when_factory_set(
    tmp_path: Path,
) -> None:
    """With ``session_factory`` set, the orchestrator writes the Run
    row at start, every Trace as it lands, and auto-promotes
    successful traces to Findings (with a replay-bundle ZIP under
    ``<output_dir>/replays/``)."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[_SuccessAttack()],
        behaviors=[_behavior()],
        judge=_AlwaysHighJudge(),
        output_dir=tmp_path,
        session_factory=factory,
    )
    result = await orch.run()
    assert len(result.traces) == 1

    with session_scope(factory) as s:
        runs = list_runs(s)
        assert len(runs) == 1
        assert runs[0].id == orch.run_id
        # Summary cached on the Run row matches the in-memory result.
        assert runs[0].summary["total_traces"] == 1
        assert runs[0].summary["successful_traces"] == 1

        traces = list_traces_for_run(s, orch.run_id)
        assert len(traces) == 1
        assert traces[0].verdict == "success"
        # Hoisted columns survive the orchestrator → DB write path.
        assert traces[0].depth == 0
        assert traces[0].parent_id is None

        findings = list_findings(s, run_id=orch.run_id)
        assert len(findings) == 1
        finding = findings[0]
        assert finding.trace_id == traces[0].id
        # Replay bundle zip materialised under output_dir/replays/.
        assert finding.replay_bundle_path is not None
        assert Path(finding.replay_bundle_path).exists()
        assert Path(finding.replay_bundle_path).suffix == ".zip"


async def test_orchestrator_skips_db_when_no_factory(tmp_path: Path) -> None:
    """No ``session_factory`` → no rows. Confirms the DB sink really
    is opt-in (matters for back-compat with notebook callers)."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[_SuccessAttack()],
        behaviors=[_behavior()],
        judge=_AlwaysHighJudge(),
        output_dir=tmp_path,
        # session_factory deliberately not passed
    )
    await orch.run()
    with session_scope(factory) as s:
        assert list_runs(s) == []


# ----------------------------------------------------------------------
# 2. End-to-end: orchestrator + dashboard share the same engine
# ----------------------------------------------------------------------


async def test_dashboard_lists_orchestrator_produced_run(
    tmp_path: Path,
) -> None:
    """The dashboard reads from the same engine the orchestrator
    wrote to: ``/runs`` lists the new run, ``/runs/{id}`` shows
    its trace + finding, and the heatmap renders."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)

    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[_SuccessAttack()],
        behaviors=[_behavior()],
        judge=_AlwaysHighJudge(),
        output_dir=tmp_path,
        session_factory=factory,
    )
    await orch.run()

    app_ = create_app(engine=engine)
    with TestClient(app_) as client:
        index = client.get("/runs")
        assert index.status_code == 200
        assert orch.run_id in index.text

        run_page = client.get(f"/runs/{orch.run_id}")
        assert run_page.status_code == 200
        # Heatmap appears with the seeded behaviour's family.
        assert "Heatmap" in run_page.text
        assert "exfil" in run_page.text  # behaviour family bucket
        # Auto-promoted finding shows up on the run page.
        assert "exfil_via_doc" in run_page.text

        findings_page = client.get("/findings")
        assert findings_page.status_code == 200
        assert orch.run_id in findings_page.text


# ----------------------------------------------------------------------
# 3. import-run idempotency
# ----------------------------------------------------------------------


def _bare_run_dir(tmp_path: Path) -> Path:
    """Produce a minimal on-disk run directory matching what
    ``advtest run`` writes: ``run_result.json`` plus a ``traces/``
    subdir. We synthesize it directly so the test doesn't depend on
    actually invoking the orchestrator (and on a side-effecting DB
    write under the runner's cwd)."""
    from advtest_ext.core.models import (
        AttackTrace,
        RunConfig,
        RunResult,
        SummaryStats,
    )
    from advtest_ext.core.models import (
        JudgeScore as DomainJudge,
    )

    run_dir = tmp_path / "out" / "run-import-test"
    (run_dir / "traces").mkdir(parents=True)
    payload = Payload(id="p", text="t", modality="text", generator_name="static_payload")
    trace = AttackTrace(
        id="t-import-1",
        run_id="run-import-test",
        behavior_id="exfil_x",
        payload=payload,
        target_name="StubDocumentTarget",
        raw_request="x",
        raw_response="y",
        verdict="success",
        judge_scores=[
            DomainJudge(
                judge_name="heuristic",
                score=0.9,
                flagged=True,
                reasoning="x",
                judge_version="0.1",
            )
        ],
    )
    cfg = RunConfig(
        target_name="StubDocumentTarget",
        attacks=["static_payload"],
        behaviors=[],
        judges=["heuristic"],
        output_dir=run_dir,
    )
    result = RunResult(
        run_id="run-import-test",
        config=cfg,
        traces=[trace],
        summary_stats=SummaryStats(total_traces=1, successful_traces=1, overall_asr=1.0),
    )
    (run_dir / "run_result.json").write_text(result.model_dump_json(indent=2))
    (run_dir / "traces" / f"{trace.id}.json").write_text(trace.model_dump_json(indent=2))
    return run_dir


def test_import_run_inserts_into_db(tmp_path: Path) -> None:
    """First import: trace + finding land in the configured DB."""
    run_dir = _bare_run_dir(tmp_path)
    db_path = tmp_path / "import.db"
    db_url = f"sqlite:///{db_path}"

    result = _RUNNER.invoke(app, ["import-run", str(run_dir), "--db-url", db_url])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == "run-import-test"
    assert payload["imported_traces"] == 1
    assert payload["promoted_findings"] == 1

    factory = make_session_factory(make_engine(db_url))
    with session_scope(factory) as s:
        runs = list_runs(s)
        assert len(runs) == 1
        assert runs[0].id == "run-import-test"
        traces = list_traces_for_run(s, "run-import-test")
        assert len(traces) == 1
        assert traces[0].verdict == "success"
        findings = list_findings(s, run_id="run-import-test")
        assert len(findings) == 1


def test_import_run_is_idempotent(tmp_path: Path) -> None:
    """Re-importing the same run directory must not duplicate rows
    or violate the Trace primary key — locks the upsert semantics."""
    run_dir = _bare_run_dir(tmp_path)
    db_path = tmp_path / "import.db"
    db_url = f"sqlite:///{db_path}"

    first = _RUNNER.invoke(app, ["import-run", str(run_dir), "--db-url", db_url])
    assert first.exit_code == 0, first.output
    second = _RUNNER.invoke(app, ["import-run", str(run_dir), "--db-url", db_url])
    assert second.exit_code == 0, second.output

    factory = make_session_factory(make_engine(db_url))
    with session_scope(factory) as s:
        traces = list_traces_for_run(s, "run-import-test")
        assert len(traces) == 1  # not duplicated
        findings = list_findings(s, run_id="run-import-test")
        assert len(findings) == 1


def test_import_run_rejects_directory_without_run_result(tmp_path: Path) -> None:
    """A directory with no ``run_result.json`` should fail clearly
    rather than crashing on the missing file."""
    bare = tmp_path / "empty"
    bare.mkdir()
    result = _RUNNER.invoke(app, ["import-run", str(bare), "--db-url", "sqlite:///:memory:"])
    assert result.exit_code != 0
    assert "run_result.json" in result.output


def test_import_run_rejects_empty_db_url(tmp_path: Path) -> None:
    """``import-run --db-url ''`` is meaningless (it'd disable the
    sink the command exists to populate) and must fail cleanly."""
    run_dir = _bare_run_dir(tmp_path)
    result = _RUNNER.invoke(app, ["import-run", str(run_dir), "--db-url", ""])
    assert result.exit_code != 0
    assert "import-run requires" in result.output


# ----------------------------------------------------------------------
# RunConfig.db_url survives YAML round-trip
# ----------------------------------------------------------------------


def test_run_config_db_url_round_trips() -> None:
    """``RunConfig`` carries the new ``db_url`` field through Pydantic
    JSON dump/validate so the dashboard's persisted config view shows
    which DB the run was wired to."""
    from advtest_ext.core.models import RunConfig

    cfg = RunConfig(
        target_name="stub",
        attacks=["static_payload"],
        behaviors=[],
        judges=["heuristic"],
        output_dir=Path("/tmp/x"),
        budget=Budget(),
        db_url="sqlite:///./advtest.db",
    )
    rebuilt = RunConfig.model_validate_json(cfg.model_dump_json())
    assert rebuilt.db_url == "sqlite:///./advtest.db"


def test_run_config_db_url_optional_default_none() -> None:
    from advtest_ext.core.models import RunConfig

    cfg = RunConfig(
        target_name="stub",
        attacks=["static_payload"],
        behaviors=[],
        judges=["heuristic"],
        output_dir=Path("/tmp/x"),
    )
    assert cfg.db_url is None
