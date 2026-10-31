"""Tests for ``judges.ground_truth`` — packet assembly, caps, ordering, emptiness."""

from __future__ import annotations

from advtest_ext.judges.ground_truth import build_packet, packet_seed
from advtest_ext.observe.model import FileCreated, TenantState


class _Trace:
    def __init__(self, trace_id: str) -> None:
        self.id = trace_id


def test_empty_packet_renders_none_and_forces_r4_unknown() -> None:
    pkt = build_packet(_Trace("t1"))
    assert pkt.is_empty()
    assert pkt.render() == "none"


def test_real_items_from_ground_truth_mapping() -> None:
    gt = {
        "tool_names": ["docs_agent:editor", "gmail_agent:draft_reply"],
        "canary": "CANARY-7",
    }
    pkt = build_packet(_Trace("t2"), ground_truth=gt)
    assert "docs_agent:editor" in pkt.real["tool_ids"]
    assert "CANARY-7" in pkt.real["canary"]
    rendered = pkt.render()
    assert "present_in_ground_truth:" in rendered
    assert "plausible_but_absent:" in rendered
    # Decoys were generated for the kinds that have real items.
    assert any(pkt.decoys.values())


def test_cap_8_real_items() -> None:
    gt = {
        "tool_names": [f"ns:tool_{i}" for i in range(6)],
        "file_ids": [f"file{i}" for i in range(6)],
        "canary": "C-1",
    }
    pkt = build_packet(_Trace("t3"), ground_truth=gt)
    total_real = sum(len(v) for v in pkt.real.values())
    assert total_real == 8


def test_cap_4_decoys() -> None:
    gt = {
        "tool_names": ["ns:a", "ns:b"],
        "doc_titles": ["Real Title One", "Real Title Two"],
        "senders": ["x@acme.test", "y@acme.test"],
        "canary": "C-9",
    }
    pkt = build_packet(_Trace("t4"), ground_truth=gt)
    total_decoys = sum(len(v) for v in pkt.decoys.values())
    assert total_decoys <= 4


def test_render_is_ordered_by_kind() -> None:
    gt = {"canary": "C-1", "tool_names": ["ns:tool"]}
    pkt = build_packet(_Trace("t5"), ground_truth=gt)
    rendered = pkt.render()
    # tool_ids must appear before canary in the present list (kind order).
    present = rendered.split("plausible_but_absent:")[0]
    assert present.index("tool_ids") < present.index("canary")


def test_observation_baseline_contributes_file_ids_and_titles() -> None:
    state = TenantState.empty()
    state.add(FileCreated(file_id="1abcFILE", name="Budget 2026"))
    pkt = build_packet(_Trace("t6"), observation=state)
    assert "1abcFILE" in pkt.real.get("file_ids", [])
    assert "Budget 2026" in pkt.real.get("doc_titles", [])


def test_seed_is_stable_for_a_trace_id() -> None:
    assert packet_seed(_Trace("same")) == packet_seed(_Trace("same"))
    assert packet_seed(_Trace("a")) != packet_seed(_Trace("b"))
