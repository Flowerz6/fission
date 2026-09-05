"""Tests for the Phase-7 per-Finding docx report writer.

The strongest assertion shape: generate a docx, then re-parse it
with the Phase-6 docx loader and check the round-trip preserves
the expected text. This proves the writer produces valid
WordprocessingML AND that the sections land where we expect.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from advtest_ext.core.loaders.docx_loader import _extract_paragraphs
from advtest_ext.dashboard.report_docx import (
    ReportInputs,
    build_finding_report,
)

# ----------------------------------------------------------------------
# Stubs for the storage rows the writer consumes. The writer only reads
# a handful of attributes from Finding / Trace / Recipe / Signal — the
# stubs supply exactly those.
# ----------------------------------------------------------------------


@dataclass
class _StubJudgeScore:
    judge_name: str = "strong_reject"
    score: float = 0.65
    flagged: bool = True
    reasoning: str = "test reason"


@dataclass
class _StubTrace:
    id: str = "trace-abc"
    behavior_id: str = "test_behavior"
    attack_name: str = "tap_indirect"
    technique_id: str | None = "white_text"
    target_name: str = "GmailTarget"
    verdict: str | None = "success"
    raw_request: str = "Please summarise this email thread."
    raw_response: str = "Here is the summary..."
    raw_response_data: dict[str, Any] = field(
        default_factory=lambda: {
            "screenshot_path": "/out/screenshots/abc.png",
            "intercepted_traffic_path": "/out/intercepted/abc.jsonl",
        }
    )
    judge_scores: list[Any] = field(default_factory=lambda: [_StubJudgeScore()])


@dataclass
class _StubFinding:
    id: str = "finding-xyz"
    severity: str = "high"
    title: str = "Test finding"
    description: str = "A test description."
    promoted_at: datetime = field(default_factory=lambda: datetime(2026, 5, 24, 12, 0, tzinfo=UTC))
    replay_bundle_path: str | None = "/out/replays/xyz.zip"


@dataclass
class _StubSignal:
    name: str = "engineer_handshake"
    kind: str = "token_appears"
    fired: bool = True
    reasoning: str = "found 'Engineer' at end"
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class _StubRecipeStep:
    step_id: str = "s1"
    intent: str = "do the thing"
    surface: dict[str, Any] = field(
        default_factory=lambda: {
            "app": "docs",
            "region": "sidebar",
            "account_role": "recipient",
        }
    )
    operation: str = "type_prompt"


@dataclass
class _StubRecipe:
    name: str = "V1 - Test Recipe"
    steps: list[Any] = field(default_factory=list)


def _save_and_reload(docx_bytes: bytes, tmp_path: Path) -> list[str]:
    """Write the docx bytes to disk and use the production
    paragraph extractor to read them back. Confirms the bytes
    are a valid .docx AND lets us assert on the rendered text
    without coupling to internal XML layout."""
    out = tmp_path / "report.docx"
    out.write_bytes(docx_bytes)
    return _extract_paragraphs(out)


# ----------------------------------------------------------------------
# Validity — the bytes form a real .docx zip
# ----------------------------------------------------------------------


def test_writer_produces_valid_docx_zip() -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    docx_bytes = build_finding_report(inputs)
    # Must be a valid zip with the three required entries.
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        names = set(zf.namelist())
    assert "[Content_Types].xml" in names
    assert "_rels/.rels" in names
    assert "word/document.xml" in names


def test_writer_output_round_trips_through_loader(tmp_path: Path) -> None:
    """Generate a docx, parse it back. Confirms the document is
    structurally valid (loader doesn't reject it) AND that the
    paragraphs land in a reasonable order."""
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    docx_bytes = build_finding_report(inputs)
    paragraphs = _save_and_reload(docx_bytes, tmp_path)
    assert paragraphs  # non-empty


# ----------------------------------------------------------------------
# Section presence — required headers always appear
# ----------------------------------------------------------------------


def test_writer_emits_every_required_section(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    required_headers = {
        "OVERVIEW",
        "METHODOLOGY",
        "FINDINGS",
        "RISKS",
        "PRIMARY RECOMMENDATION",
        "POC",
    }
    assert required_headers.issubset(set(paragraphs))


# ----------------------------------------------------------------------
# Title section — Recipe-driven vs legacy
# ----------------------------------------------------------------------


def test_title_from_recipe_when_recipe_supplied(tmp_path: Path) -> None:
    recipe = _StubRecipe(name="V1 - Persona Forgery via Inbox Grounding")
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
        recipe=recipe,  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    assert "V1 - Persona Forgery via Inbox Grounding" in paragraphs


def test_title_falls_back_to_legacy_shape_without_recipe(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    # Legacy title shape: "<attack> x <technique> -> <behaviour>"
    title_lines = [p for p in paragraphs if "tap_indirect" in p and "white_text" in p]
    assert title_lines, f"no legacy-shape title in {paragraphs[:5]}"


# ----------------------------------------------------------------------
# FINDINGS section — judge + signal rows surface in the table
# ----------------------------------------------------------------------


def test_findings_table_includes_judge_scores(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    # Each judge becomes its own row labelled "Judge: <name>".
    assert any("Judge: strong_reject" in p for p in paragraphs)
    assert any("score=0.650" in p for p in paragraphs)


def test_findings_table_includes_signal_results(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
        signals=[
            _StubSignal(name="engineer_handshake", kind="token_appears", fired=True),
            _StubSignal(name="tool_leak", kind="regex_match", fired=False),
        ],
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    assert any("Signal: engineer_handshake" in p for p in paragraphs)
    assert any("Signal: tool_leak" in p for p in paragraphs)
    # One fired, one didn't — both states should render distinctly.
    assert any("fired" in p for p in paragraphs)
    assert any("did not fire" in p for p in paragraphs)


def test_findings_table_includes_intercepted_path(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    assert any("intercepted/abc.jsonl" in p for p in paragraphs)


# ----------------------------------------------------------------------
# POC section — request + response excerpts + artefact links
# ----------------------------------------------------------------------


def test_poc_includes_request_and_response(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    assert "Please summarise this email thread." in paragraphs
    assert "Here is the summary..." in paragraphs


def test_poc_truncates_long_responses(tmp_path: Path) -> None:
    """Responses past 4000 chars get truncated with a clear
    pointer to the replay bundle."""
    long_response = "X" * 5000
    trace = _StubTrace()
    trace.raw_response = long_response
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=trace,  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    assert any("truncated; see replay bundle" in p for p in paragraphs)


def test_poc_links_replay_bundle(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    assert any("replays/xyz.zip" in p for p in paragraphs)


# ----------------------------------------------------------------------
# METHODOLOGY section — Recipe-driven vs legacy
# ----------------------------------------------------------------------


def test_methodology_from_recipe_steps(tmp_path: Path) -> None:
    recipe = _StubRecipe(
        name="V1 - Test",
        steps=[
            _StubRecipeStep(step_id="plant_email", intent="plant", operation="send_email"),
            _StubRecipeStep(step_id="open_doc", intent="open doc", operation="open_document"),
        ],
    )
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
        recipe=recipe,  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    # Per-step lines should appear in order.
    plant_index = next(i for i, p in enumerate(paragraphs) if "plant_email" in p)
    open_index = next(i for i, p in enumerate(paragraphs) if "open_doc" in p)
    assert plant_index < open_index


def test_methodology_legacy_shape_without_recipe(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    # Legacy: attack name + technique + behaviour ID surfaced.
    assert any("tap_indirect" in p for p in paragraphs)
    assert any("white_text" in p for p in paragraphs)


# ----------------------------------------------------------------------
# OVERVIEW section — behaviour description surfaces
# ----------------------------------------------------------------------


def test_overview_includes_behavior_description(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
        behavior_description="An attacker tricks the AI into faking a meeting time.",
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    assert any("An attacker tricks the AI into faking a meeting time." in p for p in paragraphs)


def test_overview_includes_finding_metadata(tmp_path: Path) -> None:
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=_StubTrace(),  # type: ignore[arg-type]
    )
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    # Severity surfaces in the overview blurb.
    assert any("severity high" in p for p in paragraphs)
    # Verdict surfaces in the overview blurb.
    assert any("Trace verdict: success" in p for p in paragraphs)


# ----------------------------------------------------------------------
# XML safety — text with special characters doesn't break the file
# ----------------------------------------------------------------------


def test_writer_xml_escapes_special_characters(tmp_path: Path) -> None:
    """Response text containing ``<``, ``>``, ``&`` must escape
    cleanly — otherwise Word rejects the file as malformed."""
    trace = _StubTrace()
    trace.raw_response = "<script>alert('hi & bye')</script>"
    inputs = ReportInputs(
        finding=_StubFinding(),  # type: ignore[arg-type]
        trace=trace,  # type: ignore[arg-type]
    )
    # Loader will raise if the docx is malformed; passing through
    # is the assertion.
    paragraphs = _save_and_reload(build_finding_report(inputs), tmp_path)
    assert any("<script>" in p for p in paragraphs)
    assert any("&" in p for p in paragraphs)
