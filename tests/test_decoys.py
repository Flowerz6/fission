"""Tests for ``judges.decoys`` — deterministic, plausible, collision-free."""

from __future__ import annotations

from advtest_ext.judges.decoys import decoys_for


def test_determinism_under_fixed_seed() -> None:
    a = decoys_for("tool_ids", ["docs_agent:editor"], "abc123def456", count=3)
    b = decoys_for("tool_ids", ["docs_agent:editor"], "abc123def456", count=3)
    assert a == b
    assert len(a) == 3


def test_different_seeds_differ() -> None:
    a = decoys_for("doc_titles", [], "seed-one", count=3)
    b = decoys_for("doc_titles", [], "seed-two", count=3)
    assert a != b  # vanishingly unlikely to coincide across the title pool


def test_no_collision_with_real_items() -> None:
    real = ["docs_agent:editor", "docs_agent:set_metadata"]
    out = decoys_for("tool_ids", real, "s", count=4)
    low = {r.lower() for r in real}
    assert all(o.lower() not in low for o in out)


def test_tool_ids_reuse_real_namespace() -> None:
    out = decoys_for("tool_ids", ["gmail_agent:draft_reply"], "seed", count=2)
    assert out, "should produce decoys"
    assert any(o.startswith("gmail_agent:") for o in out)


def test_addresses_sit_on_the_real_domain() -> None:
    out = decoys_for("senders", ["alice@acme-corp.test"], "seed", count=3)
    assert out
    assert all(o.endswith("@acme-corp.test") for o in out)


def test_file_ids_look_like_drive_ids() -> None:
    out = decoys_for("file_ids", ["1realFileId"], "seed", count=2)
    assert len(out) == 2
    assert all(len(o) >= 20 and o[0] == "1" for o in out)


def test_canary_shape() -> None:
    out = decoys_for("canary", ["CANARY-0001"], "seed", count=2)
    assert len(out) == 2
    assert all(o.startswith("CANARY-") for o in out)
    assert "CANARY-0001" not in out


def test_unknown_kind_yields_nothing() -> None:
    assert decoys_for("not_a_kind", ["x"], "seed", count=3) == []
