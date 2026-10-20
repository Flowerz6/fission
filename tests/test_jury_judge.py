"""Tests for the ``jury`` confirm-judge factory and its panel spec.

The jury is an ``EnsembleAsJudge`` over a panel of IPI judges (mixed logic +
model family), majority vote. These tests exercise the assembly and the
env-driven panel override without needing live providers — juror construction
is stubbed at :func:`_ipi_judge_over_role`.
"""

from __future__ import annotations

import pytest
import typer

from advtest_ext import _cli_factories as f
from advtest_ext.core.models import JudgeScore
from advtest_ext.judges.base import JudgeBase
from advtest_ext.judges.ensemble import EnsembleAsJudge


class _StubJudge(JudgeBase):
    """A juror double: reports its (kind, role) so we can assert the panel."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__()

    async def evaluate(self, *, trace, behavior) -> JudgeScore:  # type: ignore[no-untyped-def]
        return JudgeScore(judge_name=self.name, score=1.0, flagged=True)


def test_jury_is_registered() -> None:
    assert f._JUDGE_REGISTRY["jury"] is f._jury_judge_factory


def test_jury_panel_spec_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADVTEST_JURY_PANEL", raising=False)
    assert f._jury_panel_spec() == f._DEFAULT_JURY_PANEL


def test_jury_panel_spec_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ADVTEST_JURY_PANEL",
        " decomposed_ipi@CLAUDE_JUDGE , ipi_verdict@CHECKER_MODEL ,",
    )
    assert f._jury_panel_spec() == (
        ("decomposed_ipi", "CLAUDE_JUDGE"),
        ("ipi_verdict", "CHECKER_MODEL"),
    )


def test_jury_panel_spec_blank_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVTEST_JURY_PANEL", "   ")
    assert f._jury_panel_spec() == f._DEFAULT_JURY_PANEL


def test_jury_panel_spec_rejects_entry_without_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADVTEST_JURY_PANEL", "decomposed_ipi")
    with pytest.raises(typer.BadParameter):
        f._jury_panel_spec()


def test_jury_builds_ensemble_and_skips_missing_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A juror whose role is absent (KeyError) is skipped; the rest form the
    majority-vote panel."""
    monkeypatch.delenv("ADVTEST_JURY_PANEL", raising=False)

    def fake_build(kind: str, role: str) -> JudgeBase:
        if role == "CHECKER_MODEL":
            raise KeyError(role)  # optional model-diverse juror not hosted
        return _StubJudge(f"{kind}@{role}")

    monkeypatch.setattr(f, "_ipi_judge_over_role", fake_build)
    judge = f._jury_judge_factory()

    assert isinstance(judge, EnsembleAsJudge)
    names = [j.name for j in judge.ensemble.judges]
    assert names == ["decomposed_ipi@CLAUDE_JUDGE", "ipi_verdict@CLAUDE_JUDGE"]
    assert judge.ensemble.aggregation == "majority_vote"


def test_jury_requires_two_loadable_jurors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADVTEST_JURY_PANEL", raising=False)

    def only_one(kind: str, role: str) -> JudgeBase:
        if role == "CLAUDE_JUDGE" and kind == "decomposed_ipi":
            return _StubJudge("solo")
        raise KeyError(role)

    monkeypatch.setattr(f, "_ipi_judge_over_role", only_one)
    with pytest.raises(typer.BadParameter, match="jury needs >=2"):
        f._jury_judge_factory()
