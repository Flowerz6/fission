"""Tests for ``advtest_ext.storage``: SQLAlchemy round-trips, JSONL
backup, and replay-bundle ZIP packaging.

In-memory SQLite (``sqlite:///:memory:``) is the default — the
schema is portable so any test that runs here also passes against a
real Postgres.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from advtest_ext.core.models import (
    AttackTrace,
    JudgeScore,
    Payload,
    RunConfig,
    SummaryStats,
)
from advtest_ext.document import AbstractDocument, TextBlock
from advtest_ext.storage import (
    JsonlTraceWriter,
    asr_heatmap,
    build_replay_bundle,
    enqueue_hitl,
    finalize_run,
    get_finding,
    get_run,
    insert_trace,
    list_findings,
    list_hitl,
    list_runs,
    list_traces_for_run,
    make_engine,
    make_session_factory,
    promote_finding,
    read_jsonl_traces,
    read_replay_bundle,
    session_scope,
    upsert_run,
)


def _domain_trace(
    trace_id: str = "t1",
    behavior_id: str = "behaviour_x",
    *,
    verdict: str = "success",
    score: float = 0.9,
    parent_id: str | None = None,
    depth: int = 0,
    attack_name: str = "static_payload",
) -> AttackTrace:
    return AttackTrace(
        id=trace_id,
        run_id="run-test",
        behavior_id=behavior_id,
        payload=Payload(
            id=f"{trace_id}-payload",
            text="payload text",
            modality="text",
            generator_name=attack_name,
        ),
        target_name="StubDocumentTarget",
        raw_request="user prompt",
        raw_response="ai response",
        raw_response_data={"tool_calls": []},
        judge_scores=[
            JudgeScore(
                judge_name="heuristic",
                score=score,
                reasoning="x",
                flagged=score >= 0.5,
                judge_version="0.1",
            )
        ],
        verdict=verdict,  # type: ignore[arg-type]
        parent_id=parent_id,
        depth=depth,
    )


# ----------------------------------------------------------------------
# SQLAlchemy round-trip
# ----------------------------------------------------------------------


def test_run_and_trace_round_trip_through_sqlite() -> None:
    """Insert → re-read: every column the orchestrator populates
    survives. ``parent_id``/``depth`` are first-class columns, so the
    on-disk schema reflects them rather than burying them in
    metadata JSON."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    cfg = RunConfig(
        target_name="stub",
        attacks=["static_payload"],
        behaviors=[],
        judges=["heuristic"],
        output_dir=Path("/tmp/test"),
    )
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-test", target_name="stub", strategy="grid", config=cfg)
        insert_trace(s, run_id="run-test", trace=_domain_trace())
        insert_trace(
            s,
            run_id="run-test",
            trace=_domain_trace(
                trace_id="t2", parent_id="t1", depth=1, score=0.3, verdict="partial"
            ),
        )
        finalize_run(
            s,
            run_id="run-test",
            summary=SummaryStats(total_traces=2, successful_traces=1, overall_asr=0.5),
        )

    with session_scope(factory) as s:
        runs = list_runs(s)
        assert len(runs) == 1
        run = runs[0]
        assert run.id == "run-test"
        assert run.target_name == "stub"
        assert run.strategy == "grid"
        assert run.summary["overall_asr"] == 0.5
        assert run.config["attacks"] == ["static_payload"]
        traces = list_traces_for_run(s, "run-test")
        assert len(traces) == 2
        # parent_id/depth survive the round-trip as real columns.
        depths = sorted(t.depth for t in traces)
        assert depths == [0, 1]
        child = next(t for t in traces if t.parent_id is not None)
        assert child.parent_id == "t1"


def test_promote_finding_is_idempotent_per_trace() -> None:
    """Promoting twice updates rather than duplicates — matches the
    upsert semantics of the storage layer."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-x", target_name="stub", strategy="grid")
        insert_trace(s, run_id="run-x", trace=_domain_trace("t1"))
        promote_finding(
            s,
            run_id="run-x",
            trace_id="t1",
            severity="high",
            title="first",
        )
        promote_finding(
            s,
            run_id="run-x",
            trace_id="t1",
            severity="critical",
            title="updated",
        )

    with session_scope(factory) as s:
        findings = list_findings(s)
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert findings[0].title == "updated"


def test_asr_heatmap_aggregates_per_technique_and_behaviour() -> None:
    """Heatmap query collapses N traces into one row per
    (technique, behaviour) with success-count / attempt-count /
    derived ASR. Error verdicts are excluded from the denominator."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-h", target_name="stub", strategy="grid")
        # Two successes + one refusal for behaviour_a; one error
        # excluded from denominator.
        for i in range(2):
            insert_trace(
                s,
                run_id="run-h",
                trace=_domain_trace(f"a{i}", "behaviour_a", verdict="success"),
            )
        insert_trace(
            s,
            run_id="run-h",
            trace=_domain_trace("a-fail", "behaviour_a", verdict="refused"),
        )
        insert_trace(
            s,
            run_id="run-h",
            trace=_domain_trace("a-err", "behaviour_a", verdict="error", score=0.0),
        )

    with session_scope(factory) as s:
        heatmap = asr_heatmap(s, "run-h")
        # technique_id is None on these traces → key uses "-".
        cell = heatmap[("-", "behaviour_a")]
        assert cell["successes"] == 2
        assert cell["attempts"] == 3  # error excluded
        assert cell["asr"] == pytest.approx(2 / 3)


def test_make_engine_backfills_columns_on_a_preexisting_db(tmp_path: Path) -> None:
    """A dev DB created before a column was added must self-heal: create_all
    makes missing TABLES but not missing COLUMNS, so make_engine also runs an
    additive back-fill (no ``no such column: traces.uncertainty`` on old DBs)."""
    from sqlalchemy import create_engine, inspect, text

    db = tmp_path / "old.db"
    url = f"sqlite:///{db}"
    # Simulate an OLD schema: a traces table missing this session's columns.
    old = create_engine(url)
    with old.begin() as c:
        c.execute(text("CREATE TABLE traces (id VARCHAR(64) PRIMARY KEY, run_id VARCHAR(64))"))
    old.dispose()

    engine = make_engine(url)  # should back-fill columns + create new tables
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("traces")}
    assert "uncertainty" in cols
    assert "uncertainty_reason" in cols
    assert "verdict" in cols  # every model column is present
    assert "gold_labels" in insp.get_table_names()  # new tables created too

    # Idempotent: a second make_engine on the now-current DB doesn't error.
    make_engine(url)

    # And it actually works end-to-end: an insert-then-read round-trips.
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-mig", target_name="stub", strategy="grid")
        insert_trace(s, run_id="run-mig", trace=_domain_trace("tm1"))
    with session_scope(factory) as s:
        assert list_traces_for_run(s, "run-mig")[0].uncertainty == 0.0


def test_hitl_queue_round_trips() -> None:
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-q", target_name="stub", strategy="grid")
        enqueue_hitl(
            s,
            run_id="run-q",
            trace_id=None,
            reason="judge spread 0.5",
            disagreement=0.5,
        )

    with session_scope(factory) as s:
        items = list_hitl(s)
        assert len(items) == 1
        assert items[0].judge_disagreement == 0.5
        assert items[0].status == "pending"


# ----------------------------------------------------------------------
# JSONL backup
# ----------------------------------------------------------------------


def test_jsonl_writer_round_trips_traces(tmp_path: Path) -> None:
    """Append two traces; read back the same two via
    ``read_jsonl_traces``. JSONL is the grep-friendly backup
    independent of the SQLAlchemy database."""
    writer = JsonlTraceWriter(tmp_path / "run.jsonl")
    a = _domain_trace("a")
    b = _domain_trace("b", verdict="refused", score=0.1)
    writer.append(a)
    writer.append(b)
    restored = read_jsonl_traces(tmp_path / "run.jsonl")
    assert [t.id for t in restored] == ["a", "b"]
    assert restored[1].verdict == "refused"


def test_jsonl_reader_skips_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "with_blanks.jsonl"
    p.write_text("\n\n", encoding="utf-8")
    assert read_jsonl_traces(p) == []


# ----------------------------------------------------------------------
# Replay bundle ZIP
# ----------------------------------------------------------------------


def test_replay_bundle_contains_canonical_files(tmp_path: Path) -> None:
    """Locks the bundle layout: every name FOUNDATIONS.md "open
    problems" #4 commits to (manifest.json, abstract_document.json,
    prompt.txt, response.txt, response_raw.json, judge_scores.json,
    trace.json, replay.py) is present."""
    trace = _domain_trace("t-finding", verdict="success", score=0.95)
    document = AbstractDocument(id="doc-1", title="Q3 review", body_blocks=[TextBlock(text="...")])
    bundle = build_replay_bundle(
        finding_id="finding-abc",
        run_id="run-test",
        trace=trace,
        document=document,
        output_dir=tmp_path,
        severity="high",
    )
    # build_replay_bundle returns a locator string (local path here).
    assert Path(bundle).exists()
    assert Path(bundle).parent.name == "replays"
    contents = read_replay_bundle(bundle)
    expected_files = {
        "manifest.json",
        "abstract_document.json",
        "prompt.txt",
        "response.txt",
        "response_raw.json",  # only because trace has non-empty raw_response_data
        "judge_scores.json",
        "trace.json",
        "replay.py",
    }
    assert expected_files <= set(contents)


def test_replay_bundle_omits_response_raw_when_empty(tmp_path: Path) -> None:
    trace = _domain_trace("t-empty")
    trace.raw_response_data = {}
    bundle = build_replay_bundle(
        finding_id="finding-empty",
        run_id="run-test",
        trace=trace,
        document=None,
        output_dir=tmp_path,
    )
    contents = read_replay_bundle(bundle)
    assert "response_raw.json" not in contents


def test_replay_bundle_round_trips_trace_json(tmp_path: Path) -> None:
    """The full ``trace.json`` inside the bundle re-validates as an
    ``AttackTrace`` with parent_id/depth preserved — the bundle is
    self-sufficient for replay even if every other artefact is
    lost."""
    trace = _domain_trace("t-rt", parent_id="t-root", depth=3, attack_name="tap")
    bundle = build_replay_bundle(
        finding_id="finding-rt",
        run_id="run-test",
        trace=trace,
        document=None,
        output_dir=tmp_path,
    )
    contents = read_replay_bundle(bundle)
    rebuilt = AttackTrace.model_validate_json(contents["trace.json"])
    assert rebuilt == trace
    assert rebuilt.parent_id == "t-root"
    assert rebuilt.depth == 3


def test_replay_bundle_replay_py_references_finding_id(tmp_path: Path) -> None:
    """The generated replay.py mentions the finding_id so a checked-in
    bundle ZIP is self-identifying when extracted alongside others."""
    trace = _domain_trace("t")
    bundle = build_replay_bundle(
        finding_id="finding-xyz",
        run_id="run-test",
        trace=trace,
        document=None,
        output_dir=tmp_path,
    )
    contents = read_replay_bundle(bundle)
    assert isinstance(contents["replay.py"], str)
    assert "finding-xyz" in contents["replay.py"]


def test_replay_bundle_includes_ui_mode_screenshot(tmp_path: Path) -> None:
    """When a ui_mode trace records ``screenshot_path`` on
    ``raw_response_data`` and the PNG exists on disk, the bundle
    pulls the bytes in under ``screenshot.png`` and notes its
    presence in the manifest. The orchestrator relies on this to
    expose the side-panel artefact alongside the replay."""
    import json as _json

    png_bytes = b"\x89PNG\r\n\x1a\nfake-payload"
    screenshot_src = tmp_path / "screenshots" / "trace-ui.png"
    screenshot_src.parent.mkdir(parents=True, exist_ok=True)
    screenshot_src.write_bytes(png_bytes)

    trace = _domain_trace("t-ui", verdict="success")
    trace.raw_response_data = {
        "ui_mode": True,
        "screenshot_path": str(screenshot_src),
    }

    bundle = build_replay_bundle(
        finding_id="finding-ui",
        run_id="run-ui",
        trace=trace,
        document=None,
        output_dir=tmp_path,
    )
    contents = read_replay_bundle(bundle)
    assert "screenshot.png" in contents
    assert contents["screenshot.png"] == png_bytes
    manifest = _json.loads(contents["manifest.json"])
    assert manifest["ui_mode_screenshot"] == "screenshot.png"


def test_replay_bundle_skips_missing_screenshot(tmp_path: Path) -> None:
    """If the recorded screenshot path doesn't exist (the invoker
    bailed before writing, or the PNG was cleaned up), the bundle
    falls back to "no screenshot" rather than erroring. The trace
    is still useful; the screenshot is best-effort."""
    import json as _json

    trace = _domain_trace("t-ui-missing", verdict="success")
    trace.raw_response_data = {
        "ui_mode": True,
        "screenshot_path": str(tmp_path / "nope" / "missing.png"),
    }

    bundle = build_replay_bundle(
        finding_id="finding-ui-missing",
        run_id="run-ui",
        trace=trace,
        document=None,
        output_dir=tmp_path,
    )
    contents = read_replay_bundle(bundle)
    assert "screenshot.png" not in contents
    manifest = _json.loads(contents["manifest.json"])
    assert "ui_mode_screenshot" not in manifest


def test_replay_bundle_overwrite_is_idempotent(tmp_path: Path) -> None:
    trace = _domain_trace("t")
    p1 = build_replay_bundle(
        finding_id="finding-iso",
        run_id="run-test",
        trace=trace,
        document=None,
        output_dir=tmp_path,
    )
    # Mutate trace; rewriting under the same finding_id replaces.
    trace.raw_response = "DIFFERENT RESPONSE"
    p2 = build_replay_bundle(
        finding_id="finding-iso",
        run_id="run-test",
        trace=trace,
        document=None,
        output_dir=tmp_path,
    )
    assert p1 == p2
    contents = read_replay_bundle(p2)
    assert contents["response.txt"] == "DIFFERENT RESPONSE"


# ----------------------------------------------------------------------
# get_finding / get_run cover the read API surface
# ----------------------------------------------------------------------


def test_get_run_and_get_finding_lookups() -> None:
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-l", target_name="stub", strategy="mcts")
        insert_trace(s, run_id="run-l", trace=_domain_trace("t-l"))
        finding = promote_finding(s, run_id="run-l", trace_id="t-l", severity="high")
        finding_id = finding.id

    with session_scope(factory) as s:
        assert get_run(s, "run-l") is not None
        assert get_run(s, "no-such") is None
        f = get_finding(s, finding_id)
        assert f is not None
        assert f.severity == "high"
        assert get_finding(s, "no-such") is None
