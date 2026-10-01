"""The Claude-backed seed generator (``guides.seed_generator``).

Every test uses a fake ``complete`` — no LLM. The load-bearing guarantee is
fail-closed validation: a generated seed is only accepted if it builds as a real
Guide, passes completeness with no ERROR, and names only registered setup ops."""

from __future__ import annotations

import json

import pytest

from advtest_ext.guides.seed_generator import (
    _EXEMPLARS,
    generate_seed,
    to_guide_dict,
    validate_seed,
)


def _fake(reply: str):
    async def _complete(_prompt: str) -> str:
        return reply

    return _complete


def test_exemplars_round_trip_to_valid_guides() -> None:
    for ex in _EXEMPLARS:
        ok, errors = validate_seed(to_guide_dict(ex))
        assert ok, (ex["id"], errors)


async def test_generate_accepts_a_valid_fenced_seed() -> None:
    # Claude typically wraps JSON in a fence with a preamble line.
    reply = "Here is the seed:\n```json\n" + json.dumps(_EXEMPLARS[0]) + "\n```"
    result = await generate_seed(_fake(reply))
    assert result.valid
    assert result.guide_dict["id"] == _EXEMPLARS[0]["id"]
    assert result.guide_dict["maturity"] == "discovery"  # filled in code, not by the model
    assert "generated" in result.guide_dict["tags"] and "candidate" in result.guide_dict["tags"]


async def test_generate_rejects_a_seed_without_an_oracle() -> None:
    bad = dict(_EXEMPLARS[0])
    bad["success"] = {"signals": []}  # no signal, no rubric → invalid Guide
    result = await generate_seed(_fake(json.dumps(bad)), max_repair=0)
    assert not result.valid
    assert any("success" in e or "schema" in e for e in result.errors)


async def test_generate_rejects_an_unregistered_setup_operation() -> None:
    bad = dict(_EXEMPLARS[1])  # the planted docs exemplar
    steps = [dict(s) for s in bad["setup_steps"]]
    steps[0] = dict(steps[0])
    steps[0]["operation"] = "definitely_not_a_real_op"
    bad["setup_steps"] = steps
    result = await generate_seed(_fake(json.dumps(bad)), max_repair=0)
    assert not result.valid


async def test_generate_reports_unparseable_reply() -> None:
    result = await generate_seed(_fake("I can't help with that."), max_repair=0)
    assert not result.valid
    assert result.seed is None
