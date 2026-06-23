"""Tests for ``_load_seed_payload`` in the smoke scripts.

Smoke runs cannot use ``behavior.description`` as the attack payload
— that field is metadata about what the test targets, not a viable
attack prompt; using it gives the agent a behaviour spec rather than
an injected instruction (0 tool_calls fired). Each non-PAIR smoke
script exposes a ``_load_seed_payload(behavior_id)`` that pulls the
first matching seed from ``indirect_injection/payloads.yaml``.

These tests load each smoke module via ``importlib.util`` (the
scripts directory is not a package) and exercise the loader against
a synthetic ``payloads.yaml`` written into ``tmp_path`` — they never
touch the real seed YAML.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
_SMOKE_NAMES = ("msj_smoke", "bon_smoke", "composed_smoke")


def _load_smoke_module(name: str) -> ModuleType:
    path = _SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_synthetic_payloads(tmp_path: Path) -> Path:
    """Synthetic payloads.yaml: two entries, one tagged for the
    behaviour we resolve against, one for a different behaviour. The
    text is intentionally non-operational — we're checking the
    loader's filter, not the payload content."""
    entries: list[dict[str, Any]] = [
        {
            "id": "seed-tagged",
            "text": "TAGGED-PAYLOAD-TEXT",
            "target_behaviors": ["unauthorized_email_send_to_external"],
            "source_citation": "test2024synthetic",
        },
        {
            "id": "seed-other",
            "text": "OTHER-PAYLOAD-TEXT",
            "target_behaviors": ["leak_system_prompt"],
        },
    ]
    path = tmp_path / "fake_payloads.yaml"
    path.write_text(yaml.safe_dump(entries))
    return path


@pytest.mark.parametrize("name", _SMOKE_NAMES)
def test_load_seed_payload_returns_first_match(name: str, tmp_path: Path) -> None:
    """For a behaviour that has at least one tagged payload, the loader
    returns the first matching entry. Locks ``id``, ``text``, and the
    optional ``source_citation`` round-trip."""
    module = _load_smoke_module(name)
    payloads_path = _write_synthetic_payloads(tmp_path)
    payload = module._load_seed_payload(
        "unauthorized_email_send_to_external", payloads_path=payloads_path
    )
    assert payload.id == "seed-tagged"
    assert payload.text == "TAGGED-PAYLOAD-TEXT"
    assert payload.source_citation == "test2024synthetic"
    assert payload.generator_name == "seed"


@pytest.mark.parametrize("name", _SMOKE_NAMES)
def test_load_seed_payload_fails_closed_when_no_match(name: str, tmp_path: Path) -> None:
    """For a behaviour with no tagged payload, the loader fails closed
    via ``SystemExit`` and the message lists which behaviours DO have
    seeds — so the operator can pick a covered behaviour without
    grepping the YAML."""
    module = _load_smoke_module(name)
    payloads_path = _write_synthetic_payloads(tmp_path)
    with pytest.raises(SystemExit) as exc:
        module._load_seed_payload("behaviour_with_no_seed", payloads_path=payloads_path)
    msg = str(exc.value)
    assert "behaviour_with_no_seed" in msg
    # Both tagged behaviours from the synthetic YAML appear in the
    # "available" list.
    assert "unauthorized_email_send_to_external" in msg
    assert "leak_system_prompt" in msg


@pytest.mark.parametrize("name", _SMOKE_NAMES)
def test_load_seed_payload_ignores_unscoped_entries(name: str, tmp_path: Path) -> None:
    """Entries with an empty / missing ``target_behaviors`` list must
    NOT match — the smoke seed has to be explicitly tagged, otherwise
    one unscoped payload would silently win for every behaviour."""
    module = _load_smoke_module(name)
    entries: list[dict[str, Any]] = [
        {"id": "seed-unscoped", "text": "UNSCOPED-TEXT", "target_behaviors": []},
        {"id": "seed-also-unscoped", "text": "OTHER-UNSCOPED"},
    ]
    payloads_path = tmp_path / "fake_payloads.yaml"
    payloads_path.write_text(yaml.safe_dump(entries))
    with pytest.raises(SystemExit):
        module._load_seed_payload(
            "unauthorized_email_send_to_external",
            payloads_path=payloads_path,
        )
