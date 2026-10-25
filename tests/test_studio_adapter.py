"""Wiring + offline-flow tests for the Workspace Studio adapter.

The live ui_mode invoker needs a browser and the real Studio surface (its
selectors and app-URL shape are SEEDED and must be healed against live Studio),
so it is exercised by the opt-in integration suite, not here. These tests lock
the offline-testable parts: registry wiring, per-target option validation,
journey resolution, operation-registry entries, the Surface vocabulary, and the
multi-turn target's invoke/reset flow with an injected driver + session.
"""

from __future__ import annotations

from typing import Any

import pytest
import typer

from advtest_ext._cli_factories import _resolve_target
from advtest_ext.adapters.studio.ui_mode.multi_turn_target import GeminiStudioMultiTurnTarget
from advtest_ext.adapters.studio.ui_mode.selectors import EXPECTED_KEYS, SELECTORS
from advtest_ext.core.builtin_operations import default_operations
from advtest_ext.core.surface import Surface
from advtest_ext.delivery.journeys import resolve_journey
from advtest_ext.document.target import Response

# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def test_studio_is_a_valid_surface_app() -> None:
    s = Surface(app="studio", region="main", account_role="recipient")
    assert s.app == "studio"


def test_studio_target_resolves_from_the_registry() -> None:
    assert isinstance(
        _resolve_target("google_studio_multiturn", {"app_id": "https://x/app/1"}),
        GeminiStudioMultiTurnTarget,
    )


def test_studio_factory_rejects_unknown_options() -> None:
    with pytest.raises(typer.BadParameter, match="google_studio_multiturn"):
        _resolve_target("google_studio_multiturn", {"deck_id": "x"})


def test_studio_factory_validates_app_ids_type() -> None:
    with pytest.raises(typer.BadParameter, match="app_ids"):
        _resolve_target("google_studio_multiturn", {"app_ids": "not-a-list"})


def test_studio_journey_resolves() -> None:
    assert (
        resolve_journey(target_name="", guide_app="google_studio", guide_surface="agent")
        == "J-STUDIO-AGENT"
    )
    # fallback by target-name substring
    assert resolve_journey(target_name="GeminiStudioMultiTurnTarget") == "J-STUDIO-AGENT"


def test_studio_operations_registered() -> None:
    reg = default_operations()
    assert reg.has("studio", "create_app")
    assert reg.has("studio", "run_agent")


def test_studio_selector_keys_match_expected() -> None:
    assert set(SELECTORS.keys()) == set(EXPECTED_KEYS)


def test_studio_target_is_multi_turn() -> None:
    t = GeminiStudioMultiTurnTarget(output_dir="./out", app_id="https://x/app/1")
    assert t.capabilities().multi_turn is True
    assert t.supported_techniques() == {"*"}


# --------------------------------------------------------------------------
# multi-turn flow (injected driver + session; no browser)
# --------------------------------------------------------------------------


class _FakePage:
    url = ""


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def ensure_page(self) -> Any:
        return _FakePage()

    async def close(self) -> None:
        self.closed = True


def _recording_invoke_fn(calls: list[dict[str, Any]]) -> Any:
    async def _fn(**kwargs: Any) -> Response:
        calls.append(kwargs)
        return Response(text="ran the app", raw={})

    return _fn


@pytest.mark.asyncio
async def test_invoke_resets_first_turn_then_continues() -> None:
    calls: list[dict[str, Any]] = []
    target = GeminiStudioMultiTurnTarget(
        output_dir="./out",
        app_id="https://studio/app/abc",
        session=_FakeSession(),
        invoke_fn=_recording_invoke_fn(calls),
    )
    ctx = await target.render_document(doc=object())  # type: ignore[arg-type]
    assert ctx.rendered == {"app_id": "https://studio/app/abc"}

    await target.invoke(ctx, "turn one")
    await target.invoke(ctx, "turn two")
    # First turn resets the run; later turns continue it.
    assert calls[0]["reset_conversation"] is True
    assert calls[1]["reset_conversation"] is False
    assert calls[0]["app_id"] == "https://studio/app/abc"
    # turn provenance is stamped
    r = await target.invoke(ctx, "turn three")
    assert r.raw["turn"] == 3
    assert r.raw["is_multi_turn"] is True


@pytest.mark.asyncio
async def test_reset_starts_a_fresh_run_and_rotates_app_ids() -> None:
    calls: list[dict[str, Any]] = []
    target = GeminiStudioMultiTurnTarget(
        output_dir="./out",
        app_ids=["https://studio/app/one", "https://studio/app/two"],
        session=_FakeSession(),
        invoke_fn=_recording_invoke_fn(calls),
    )
    ctx1 = await target.render_document(doc=object())  # type: ignore[arg-type]
    await target.invoke(ctx1, "node0 turn1")
    assert ctx1.rendered["app_id"] == "https://studio/app/one"

    target.reset()  # next node
    ctx2 = await target.render_document(doc=object())  # type: ignore[arg-type]
    await target.invoke(ctx2, "node1 turn1")
    assert ctx2.rendered["app_id"] == "https://studio/app/two"
    # both nodes' first turns reset the run
    assert calls[0]["reset_conversation"] is True
    assert calls[1]["reset_conversation"] is True


@pytest.mark.asyncio
async def test_create_app_without_id_or_session_raises() -> None:
    # No app_id, no app_ids, no browser_mint, no app_factory -> no way to mint.
    target = GeminiStudioMultiTurnTarget(output_dir="./out", session=_FakeSession())
    with pytest.raises(RuntimeError, match="no Workspace Studio API"):
        await target.render_document(doc=object())  # type: ignore[arg-type]


def test_bind_carrier_requires_app_id() -> None:
    target = GeminiStudioMultiTurnTarget(output_dir="./out")
    with pytest.raises(ValueError, match="app_id"):
        target.bind_carrier(deck_id="wrong")
    target.bind_carrier(app_id="https://studio/app/bound")
    assert target._app_id == "https://studio/app/bound"
