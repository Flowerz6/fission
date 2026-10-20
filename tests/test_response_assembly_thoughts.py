"""``extract_model_thoughts`` — pull Gemini's streamed chain-of-thought out of an
intercepted streamGenerate capture, without the answer, its snapshots, or the
leading UI chrome (suggestion chips)."""

from __future__ import annotations

import json

from advtest_ext.adapters._ui_mode_common.response_assembly import (
    assemble_full_response,
    extract_model_thoughts,
)


def _write_capture(tmp_path, body_value) -> str:
    entry = {
        "url": "https://appsgenaiserver-pa.clients6.google.com/v1/genai/streamGenerate?key=x",
        "response_body": json.dumps(body_value),
    }
    path = tmp_path / "cap.jsonl"
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return str(path)


def test_extract_thoughts_drops_chrome_answer_and_snapshots(tmp_path) -> None:
    answer = "Sorry, I cannot share internal system details or tool schemas with you today."
    body = [
        # leading suggestion chip (prose, but BEFORE any bold title) → excluded
        "Generate a dashboard with high level metrics and trend visualizations",
        "**Weighing The Request**",  # first bold title → anchor
        "I am considering whether this ask is trying to extract internal details.",
        "**Deciding To Refuse**",
        "I will decline, because revealing the architecture is not appropriate here.",
        "Sorry, I cannot share internal system details",  # a streaming snapshot of the answer
        answer,  # the final answer (longest fragment)
    ]
    path = _write_capture(tmp_path, body)

    assert assemble_full_response(path) == answer  # answer unchanged
    thoughts = extract_model_thoughts(path)
    assert "Weighing The Request" in thoughts
    assert "considering whether this ask" in thoughts
    assert "Deciding To Refuse" in thoughts
    assert "I will decline" in thoughts
    # never leaks chrome, the answer, or its snapshot
    assert "dashboard with high level metrics" not in thoughts
    assert "Sorry, I cannot share internal system details" not in thoughts


def test_extract_thoughts_empty_when_no_bold_titles(tmp_path) -> None:
    # No bold-titled thought summaries → nothing to extract (avoid dumping chrome).
    path = _write_capture(tmp_path, ["Just a plain answer with no reasoning section at all here."])
    assert extract_model_thoughts(path) == ""


def test_extract_thoughts_missing_file_is_empty() -> None:
    assert extract_model_thoughts("/no/such/capture.jsonl") == ""
