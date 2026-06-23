"""Shared pytest fixtures for advtest_ext.

Also auto-skips ``gpu``- and ``integration``-marked tests unless the
user opts in with ``-m gpu`` / ``-m integration``. Marker registration
lives in ``pyproject.toml`` (``markers = [...]``); auto-skip lives
here so a plain ``pytest`` invocation never accidentally hits a real
LLM endpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from advtest_ext.providers import ProviderConfig

if TYPE_CHECKING:
    from collections.abc import Iterable


_OPT_IN_MARKERS = ("gpu", "integration")


def pytest_collection_modifyitems(config: pytest.Config, items: Iterable[pytest.Item]) -> None:
    selected = config.getoption("-m") or ""
    for marker_name in _OPT_IN_MARKERS:
        if marker_name in selected:
            return  # user opted in explicitly; honour their selection
    skip_markers = {
        name: pytest.mark.skip(reason=f"opt-in only — run with `-m {name}`")
        for name in _OPT_IN_MARKERS
    }
    for item in items:
        for name, mark in skip_markers.items():
            if name in item.keywords:
                item.add_marker(mark)
                break


@pytest.fixture
def vllm_config() -> ProviderConfig:
    return ProviderConfig(
        provider_type="local_vllm",
        endpoint="http://localhost:8000/v1",
        model_id="Qwen/Qwen3-32B",
    )
