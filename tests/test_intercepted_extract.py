"""Tests for the intercepted-response text extractor."""

from __future__ import annotations

import json
from pathlib import Path

from advtest_ext.dashboard.intercepted_extract import extract_full_response


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


def test_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert extract_full_response(tmp_path / "nope.jsonl") == ""


def test_returns_empty_for_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    assert extract_full_response(p) == ""


def test_skips_non_streamgenerate_entries(tmp_path: Path) -> None:
    """quotaSummary captures shouldn't contribute fragments."""
    p = tmp_path / "cap.jsonl"
    _write_jsonl(
        p,
        [
            {
                "url": "https://x/v1/genai/quotaSummary",
                "response_body": json.dumps(
                    ["some quota text that is longer than the prose threshold"]
                ),
            },
        ],
    )
    assert extract_full_response(p) == ""


def test_extracts_longest_prose_string(tmp_path: Path) -> None:
    short = "ok"
    medium = "yes please send the document here right away"
    long = (
        "This thread summarises the scheduling and agenda items "
        "for the Q3 Vendor Sync between Acme and Stratos. The "
        "discussion centred on contract renewal timelines."
    )
    p = tmp_path / "cap.jsonl"
    _write_jsonl(
        p,
        [
            {
                "url": "https://x/v1/genai/streamGenerate",
                "response_body": json.dumps(
                    [
                        short,
                        medium,
                        [None, [None, long]],
                        {"k": "irrelevant_id"},
                    ]
                ),
            },
        ],
    )
    assert extract_full_response(p) == long


def test_handles_ndjson_body(tmp_path: Path) -> None:
    """streamGenerate bodies are sometimes NDJSON — multiple JSON
    chunks separated by newlines. The extractor should still walk
    each chunk and find prose in any of them."""
    long = "this is the actual model response text that should win because it is much longer than any short fragment"
    body = "\n".join(
        [
            json.dumps(["short"]),
            json.dumps([None, [None, long]]),
            json.dumps(["also short"]),
        ]
    )
    p = tmp_path / "cap.jsonl"
    _write_jsonl(
        p,
        [
            {"url": "https://x/v1/genai/streamGenerate", "response_body": body},
        ],
    )
    assert extract_full_response(p) == long


def test_filters_out_internal_ids(tmp_path: Path) -> None:
    """Strings without whitespace shouldn't count as prose even if
    long — they're usually IDs / tokens / fingerprints."""
    fake_id = "$AdD5J0hLybeftRGi0LHgCFss" * 5
    prose = "the response from gemini ends with a real sentence please."
    p = tmp_path / "cap.jsonl"
    _write_jsonl(
        p,
        [
            {
                "url": "https://x/v1/genai/streamGenerate",
                "response_body": json.dumps([fake_id, prose]),
            },
        ],
    )
    assert extract_full_response(p) == prose


def test_handles_malformed_body_gracefully(tmp_path: Path) -> None:
    """Malformed bodies shouldn't raise — just contribute nothing."""
    p = tmp_path / "cap.jsonl"
    _write_jsonl(
        p,
        [
            {"url": "https://x/v1/genai/streamGenerate", "response_body": "{not valid"},
            {
                "url": "https://x/v1/genai/streamGenerate",
                "response_body": json.dumps(["this fragment is long enough to count as prose."]),
            },
        ],
    )
    # First entry is unparseable; second contributes one fragment.
    assert "fragment" in extract_full_response(p)


def test_aggregates_across_multiple_streamgenerate_entries(tmp_path: Path) -> None:
    """A trace has multiple streamGenerate calls; the longest prose
    across all of them wins."""
    p = tmp_path / "cap.jsonl"
    summary = (
        "the main summary that gemini wrote is several sentences long "
        "and contains plenty of prose text for the heuristic to pick"
    )
    chip = "what time is the meeting"
    _write_jsonl(
        p,
        [
            {
                "url": "https://x/v1/genai/streamGenerate",
                "response_body": json.dumps([chip]),
            },
            {
                "url": "https://x/v1/genai/streamGenerate",
                "response_body": json.dumps([summary]),
            },
        ],
    )
    assert extract_full_response(p) == summary
