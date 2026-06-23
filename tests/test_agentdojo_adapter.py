"""Tests for the AgentDojo adapter.

The agentdojo package is not a runtime dependency — the adapter takes
a ``suite_loader`` callable so different agentdojo versions can be
wrapped without bumping a pin (see module docstring of
``advtest_ext.adapters.agentdojo.target``). These tests pass a
hand-rolled ``_FakeSuite`` that implements the same surface
(``inject_data`` / ``run_with_agent`` / ``teardown``).

Coverage:

* :class:`AgentDojoTarget` end-to-end: setup → render_document →
  invoke → teardown, with the fake suite recording every interaction
  so assertions on argument flow are direct.
* Each of the three concrete subclasses (Banking, Slack, Workspace)
  pins its expected ``suite_name`` and user prompt.
* ``capabilities`` and ``supported_techniques`` shape — Workspace
  widens the surface; the other two stay on the text-only baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from advtest_ext.adapters.agentdojo import (
    AgentDojoTarget,
    BankingTarget,
    SlackTarget,
    WorkspaceTarget,
)
from advtest_ext.document import AbstractDocument
from advtest_ext.document.blocks import TextBlock

# ----------------------------------------------------------------------
# Fake AgentDojo suite
# ----------------------------------------------------------------------


@dataclass
class _FakeAgentRunResult:
    """Shape the adapter reads on the suite's ``run_with_agent``
    return value. Mirrors the public surface AgentDojo exposes on
    its AgentRunResult; only the fields the adapter touches are
    modelled here."""

    text: str = ""
    attacker_objective_met: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


class _FakeSuite:
    """In-memory stand-in for an agentdojo task suite.

    Behaviour:
      * ``inject_data(text)`` stashes the text under a fresh state
        token and returns the token.
      * ``run_with_agent(state, prompt)`` returns a configurable
        result and records the (state, prompt) it was called with.
      * ``teardown(state)`` drops the state from the in-memory map.
    """

    def __init__(
        self,
        *,
        result: _FakeAgentRunResult | None = None,
    ) -> None:
        self.injections: dict[str, str] = {}
        self.runs: list[tuple[str, str]] = []
        self.teardowns: list[str] = []
        self._result = result or _FakeAgentRunResult(text="ok", attacker_objective_met=False)
        self._n = 0

    def inject_data(self, payload: str) -> str:
        token = f"state-{self._n}"
        self._n += 1
        self.injections[token] = payload
        return token

    def run_with_agent(self, state: str, prompt: str) -> _FakeAgentRunResult:
        self.runs.append((state, prompt))
        return self._result

    def teardown(self, state: str) -> None:
        self.teardowns.append(state)
        self.injections.pop(state, None)


def _make_doc(text: str = "hello world") -> AbstractDocument:
    return AbstractDocument(
        id="doc-x",
        title="Test Doc",
        body_blocks=[TextBlock(text=text)],
    )


# ----------------------------------------------------------------------
# Base class lifecycle
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agentdojo_lifecycle_injects_text_and_runs_agent() -> None:
    """render_document injects the linearised text into the suite;
    invoke runs the agent with the resulting state token; teardown
    drops the state. Whole loop exercised against the fake suite."""
    suite = _FakeSuite(
        result=_FakeAgentRunResult(
            text="agent said hello",
            attacker_objective_met=True,
            tool_calls=[{"name": "send_email", "args": {"to": "attacker@evil"}}],
        )
    )
    target = AgentDojoTarget(
        suite_loader=lambda: suite,
        suite_name="testsuite",
    )

    ctx = await target.render_document(_make_doc(text="payload-marker"))
    # Title + body text get linearised into the injection slot.
    [(token, injected)] = list(suite.injections.items())
    assert "payload-marker" in injected
    assert "Test Doc" in injected
    assert ctx.rendered["state_token"] == token

    response = await target.invoke(ctx, "do the thing")
    assert suite.runs == [(token, "do the thing")]
    assert response.text == "agent said hello"
    assert response.complied is True
    assert response.raw["tool_calls"] == [{"name": "send_email", "args": {"to": "attacker@evil"}}]
    assert response.raw["suite"] == "testsuite"

    await target.teardown(ctx)
    assert suite.teardowns == [token]


@pytest.mark.asyncio
async def test_agentdojo_invoke_falls_back_to_default_prompt() -> None:
    """When the caller passes an empty user prompt the adapter uses
    the constructor default — the orchestrator can pass `""` for
    document-only attacks and the agent still gets a tool-calling
    nudge."""
    suite = _FakeSuite()
    target = AgentDojoTarget(
        suite_loader=lambda: suite,
        suite_name="t",
        user_prompt="DEFAULT-PROMPT",
    )
    ctx = await target.render_document(_make_doc())
    await target.invoke(ctx, "")
    assert suite.runs[0][1] == "DEFAULT-PROMPT"


@pytest.mark.asyncio
async def test_agentdojo_setup_loads_suite_lazily() -> None:
    """The suite_loader callable runs on first setup(), not at
    construction time — keeps unit tests cheap when the loader
    would otherwise import the agentdojo package."""
    calls = {"n": 0}

    def loader() -> _FakeSuite:
        calls["n"] += 1
        return _FakeSuite()

    target = AgentDojoTarget(suite_loader=loader, suite_name="lazy")
    assert calls["n"] == 0
    await target.setup()
    assert calls["n"] == 1
    # Calling setup again is a no-op (suite cached on first run).
    await target.setup()
    assert calls["n"] == 1


# ----------------------------------------------------------------------
# Concrete subclass shape
# ----------------------------------------------------------------------


def test_banking_target_pins_suite_name_and_prompt() -> None:
    suite = _FakeSuite()
    target = BankingTarget(suite_loader=lambda: suite)
    assert target.name == "agentdojo:banking"
    assert "bank statement" in target._user_prompt.lower()


def test_slack_target_pins_suite_name_and_prompt() -> None:
    suite = _FakeSuite()
    target = SlackTarget(suite_loader=lambda: suite)
    assert target.name == "agentdojo:slack"
    assert "channel" in target._user_prompt.lower()


def test_workspace_target_widens_block_types_and_techniques() -> None:
    """WorkspaceTarget claims a richer surface — it carries the
    spreadsheet-shaped tasks AgentDojo's workspace suite includes —
    so table_cell blocks and the table_cell_hidden technique should
    appear on top of the text-only baseline."""
    suite = _FakeSuite()
    target = WorkspaceTarget(suite_loader=lambda: suite)
    caps = target.capabilities()
    assert "table_cell" in caps.block_types
    techniques = target.supported_techniques()
    assert "table_cell_hidden" in techniques
    # Text-only techniques are still inherited from the base.
    assert "zero_width_chars" in techniques


def test_banking_target_inherits_text_only_techniques() -> None:
    suite = _FakeSuite()
    target = BankingTarget(suite_loader=lambda: suite)
    techniques = target.supported_techniques()
    # Banking doesn't widen — text-only set only.
    assert "table_cell_hidden" not in techniques
    assert {"zero_width_chars", "unicode_homoglyph", "markdown_injection"} <= techniques


def test_capabilities_marks_tool_use() -> None:
    """AgentDojo agents always use tools — that's the whole point of
    the benchmark — so ``capabilities().tool_use`` must be True. The
    orchestrator routes tool-aware attacks (tool_format_mimicry,
    etc.) only to tool-use targets."""
    suite = _FakeSuite()
    for target_cls in (BankingTarget, SlackTarget, WorkspaceTarget):
        target = target_cls(suite_loader=lambda: suite)
        assert target.capabilities().tool_use is True
