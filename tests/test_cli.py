"""Tests for ``advtest_ext.cli``: YAML loading, name resolution, and
the ``advtest run`` Typer entrypoint.

The CLI test invokes the real Typer app with a synthetic RunConfig
YAML and a stub target; the orchestrator runs against
``StubDocumentTarget`` so no real LLM calls happen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from advtest_ext.cli import (
    _resolve_attack,
    _resolve_judge,
    _resolve_target,
    app,
    load_run_config,
)

_RUNNER = CliRunner()


def _write_run_config(
    tmp_path: Path,
    *,
    attacks: list[str],
    target: str = "stub",
    judges: list[str] | None = None,
    behaviors: list[str] | None = None,
    max_queries: int | None = 1,
    output_dir: Path | None = None,
) -> Path:
    cfg_path = tmp_path / "run.yaml"
    out = output_dir if output_dir is not None else tmp_path / "out"
    payload: dict[str, object] = {
        "target_name": target,
        "attacks": attacks,
        "behaviors": behaviors or [],
        "judges": judges or ["heuristic"],
        "output_dir": str(out),
        "max_concurrency": 1,
    }
    if max_queries is not None:
        payload["budget"] = {"max_queries": max_queries}
    cfg_path.write_text(yaml.safe_dump(payload))
    return cfg_path


# ----------------------------------------------------------------------
# Registry resolution
# ----------------------------------------------------------------------


def test_resolve_attack_known_names() -> None:
    for name in ("static_payload", "indirect_injection", "pap"):
        plugin = _resolve_attack(name)
        assert plugin.name == name or plugin.name.startswith(name)


def test_resolve_attack_unknown_raises_clean_error() -> None:
    from typer import BadParameter

    with pytest.raises(BadParameter, match="unknown attack"):
        _resolve_attack("no_such_attack")


def test_resolve_target_and_judge() -> None:
    target = _resolve_target("stub")
    assert type(target).__name__ == "StubDocumentTarget"
    judge = _resolve_judge("heuristic")
    assert judge.name == "heuristic"


def test_resolve_workspace_targets_load_gemini_from_provider_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``google_docs`` / ``gmail`` / ``sheets`` resolve via a factory
    that reads ``GEMINI_TARGET`` from a provider YAML
    (``ADVTEST_PROVIDER_CONFIG``, default ``configs/dev.yaml``).
    Locks the registry contract so a future env-var refactor doesn't
    silently break ``advtest run --config configs/run_workspace_docs.yaml``."""
    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(
        yaml.safe_dump(
            {
                "GEMINI_TARGET": {
                    "provider_type": "gemini",
                    "model_id": "gemini-2.5-flash",
                    "api_key_env": "GEMINI_API_KEY",
                }
            }
        )
    )
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))

    for name, expected_cls in (
        ("google_docs", "GoogleDocsTarget"),
        ("gmail", "GmailTarget"),
        ("sheets", "SheetsTarget"),
    ):
        target = _resolve_target(name)
        assert type(target).__name__ == expected_cls


def test_resolve_workspace_target_missing_provider_yaml_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``ADVTEST_PROVIDER_CONFIG`` points at a non-existent file,
    the factory raises a Typer-friendly error naming the missing
    path — better than a stack trace from an arbitrary I/O depth."""
    from typer import BadParameter

    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(tmp_path / "no_such.yaml"))
    with pytest.raises(BadParameter, match="provider config not found"):
        _resolve_target("google_docs")


def test_resolve_workspace_target_missing_role_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the provider YAML exists but has no ``GEMINI_TARGET``
    role, the factory says so explicitly. A ``KeyError`` from deep
    inside the provider loader would be confusing on a fresh
    checkout."""
    from typer import BadParameter

    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(yaml.safe_dump({"OTHER_ROLE": {}}))
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))
    with pytest.raises(BadParameter, match="no GEMINI_TARGET role"):
        _resolve_target("google_docs")


def test_run_workspace_docs_config_loads_cleanly() -> None:
    """The ``configs/run_workspace_docs.yaml`` file ships in the repo
    root and must round-trip through ``load_run_config`` without
    errors — otherwise ``advtest run --config …`` would fail before
    even hitting the orchestrator. Locks the example config so a
    schema drift on RunConfig doesn't silently break the demo."""
    cfg_path = Path(__file__).resolve().parents[1] / "configs" / "run_workspace_docs.yaml"
    cfg = load_run_config(cfg_path)
    assert cfg.target_name == "google_docs"
    assert cfg.attacks == ["indirect_injection"]
    assert "exfil_doc_via_image_url" in cfg.behaviors
    assert "leak_system_prompt" in cfg.behaviors
    assert cfg.judges == ["strong_reject"]
    assert cfg.budget.max_queries == 30
    assert cfg.budget.max_cost_usd == 2.0
    assert cfg.max_concurrency == 2


def test_resolve_strong_reject_judge_loads_claude_from_provider_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``strong_reject`` resolves via a factory that reads
    ``CLAUDE_JUDGE`` from the provider YAML
    (``ADVTEST_PROVIDER_CONFIG``, default ``configs/dev.yaml``).
    Locks the registry contract so a future env-var refactor doesn't
    silently break ``judges: [strong_reject]`` in run configs."""
    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(
        yaml.safe_dump(
            {
                "CLAUDE_JUDGE": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "temperature": 0.0,
                }
            }
        )
    )
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))

    judge = _resolve_judge("strong_reject")
    assert judge.name == "strong_reject"
    # Provider plumbed through — the rubric calls .complete on this
    # exact provider, so it has to be the one we configured (not a
    # default fallback).
    assert judge._provider.provider_type == "anthropic"  # type: ignore[attr-defined]
    assert judge._provider.model_id == "claude-sonnet-4-6"  # type: ignore[attr-defined]


def test_resolve_strong_reject_missing_provider_yaml_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer import BadParameter

    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(tmp_path / "no_such.yaml"))
    with pytest.raises(BadParameter, match="provider config not found"):
        _resolve_judge("strong_reject")


def test_resolve_pair_indirect_loads_claude_attacker_and_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``pair_indirect`` resolves via a factory that loads BOTH
    ``CLAUDE_ATTACKER`` (PAIR's payload generator) and ``CLAUDE_JUDGE``
    (PAIR's inner-loop scorer). Locks the registry contract so a
    future env-var refactor doesn't silently break
    ``configs/run_workspace_pair.yaml``."""
    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(
        yaml.safe_dump(
            {
                "CLAUDE_ATTACKER": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "temperature": 0.7,
                },
                "CLAUDE_JUDGE": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "temperature": 0.0,
                },
            }
        )
    )
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))
    # Pin max_rounds via env so the test asserts the env path works
    # without coupling to the factory's default constant.
    monkeypatch.setenv("ADVTEST_PAIR_MAX_ROUNDS", "3")

    plugin = _resolve_attack("pair_indirect")
    assert plugin.name == "pair_indirect"
    # Integrated PairIndirectAttack owns the attacker / judge / round
    # bound directly — no nested PairAttack to drill into now that the
    # loop runs the indirect-injection delivery in-band.
    assert plugin._attacker.provider_type == "anthropic"  # type: ignore[attr-defined]
    assert plugin._attacker.model_id == "claude-sonnet-4-6"  # type: ignore[attr-defined]
    assert plugin._judge.name == "strong_reject"  # type: ignore[attr-defined]
    assert plugin._judge._provider.model_id == "claude-sonnet-4-6"  # type: ignore[attr-defined]
    assert plugin._max_rounds == 3  # type: ignore[attr-defined]


def test_resolve_pair_indirect_missing_attacker_role_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``CLAUDE_ATTACKER`` is missing, the factory says so by name —
    a ``KeyError`` at load time would be confusing on a fresh
    checkout where the user only set up CLAUDE_JUDGE for the simpler
    indirect_injection run."""
    from typer import BadParameter

    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(
        yaml.safe_dump(
            {
                "CLAUDE_JUDGE": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                }
            }
        )
    )
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))
    with pytest.raises(BadParameter, match="no CLAUDE_ATTACKER role"):
        _resolve_attack("pair_indirect")


def test_resolve_tap_indirect_loads_claude_attacker_and_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``tap_indirect`` resolves via a factory that loads BOTH
    ``CLAUDE_ATTACKER`` (TAP's branch generator) and ``CLAUDE_JUDGE``
    (per-branch scorer used to prune the frontier). Locks the
    registry contract for ``configs/run_workspace_tap.yaml`` and
    confirms the env-var overrides for tree shape work."""
    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(
        yaml.safe_dump(
            {
                "CLAUDE_ATTACKER": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "temperature": 0.7,
                },
                "CLAUDE_JUDGE": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "temperature": 0.0,
                },
            }
        )
    )
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))
    # Pin tree shape via env so the assertions don't couple to the
    # factory's default constants — we're locking the env-driven
    # override path, not the defaults.
    monkeypatch.setenv("ADVTEST_TAP_MAX_DEPTH", "2")
    monkeypatch.setenv("ADVTEST_TAP_BRANCHING_FACTOR", "4")
    monkeypatch.setenv("ADVTEST_TAP_KEEP_TOP", "2")

    plugin = _resolve_attack("tap_indirect")
    assert plugin.name == "tap_indirect"
    # Integrated TapIndirectAttack owns the attacker / judge / tree
    # shape directly — no nested TapAttack to drill into now that the
    # loop runs the indirect-injection delivery in-band.
    assert plugin._attacker.provider_type == "anthropic"  # type: ignore[attr-defined]
    assert plugin._attacker.model_id == "claude-sonnet-4-6"  # type: ignore[attr-defined]
    assert plugin._judge.name == "strong_reject"  # type: ignore[attr-defined]
    assert plugin._judge._provider.model_id == "claude-sonnet-4-6"  # type: ignore[attr-defined]
    assert plugin._max_depth == 2  # type: ignore[attr-defined]
    assert plugin._branching_factor == 4  # type: ignore[attr-defined]
    assert plugin._keep_top == 2  # type: ignore[attr-defined]


def test_resolve_tap_indirect_missing_attacker_role_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer import BadParameter

    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(
        yaml.safe_dump(
            {
                "CLAUDE_JUDGE": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                }
            }
        )
    )
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))
    with pytest.raises(BadParameter, match="no CLAUDE_ATTACKER role"):
        _resolve_attack("tap_indirect")


def test_run_workspace_tap_config_loads_cleanly() -> None:
    """``configs/run_workspace_tap.yaml`` must round-trip through
    ``load_run_config``; locks the example config so a schema drift
    on RunConfig doesn't silently break the demo."""
    cfg_path = Path(__file__).resolve().parents[1] / "configs" / "run_workspace_tap.yaml"
    cfg = load_run_config(cfg_path)
    assert cfg.target_name == "google_docs"
    assert cfg.attacks == ["tap_indirect"]
    assert cfg.judges == ["strong_reject"]
    # Empty behaviours list = whole seed catalogue (the CLI's
    # ``_resolve_behaviors`` does the expansion). Locks the
    # demo-grade default of "test everything we know how to test"
    # rather than the earlier hand-picked subset.
    assert cfg.behaviors == []
    assert cfg.budget.max_queries == 500
    assert cfg.budget.max_cost_usd == 80.0


def test_run_workspace_pair_config_loads_cleanly() -> None:
    """``configs/run_workspace_pair.yaml`` must round-trip through
    ``load_run_config`` — locks the example config so a schema drift
    on RunConfig doesn't silently break the demo."""
    cfg_path = Path(__file__).resolve().parents[1] / "configs" / "run_workspace_pair.yaml"
    cfg = load_run_config(cfg_path)
    assert cfg.target_name == "google_docs"
    assert cfg.attacks == ["pair_indirect"]
    assert cfg.judges == ["strong_reject"]
    assert cfg.behaviors == []  # whole seed catalogue
    assert cfg.budget.max_queries == 500
    assert cfg.budget.max_cost_usd == 80.0


# ----------------------------------------------------------------------
# target_options / attack_options plumbing
# ----------------------------------------------------------------------


def _gemini_only_provider_yaml(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "GEMINI_TARGET": {
                    "provider_type": "gemini",
                    "model_id": "gemini-2.5-flash",
                    "api_key_env": "GEMINI_API_KEY",
                }
            }
        )
    )


def test_resolve_google_docs_target_honours_ui_mode_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``target_options.mode = "ui_mode"`` must reach the
    GoogleDocsTarget constructor so the run actually drives the
    Playwright side panel instead of the Gemini API. Locks the
    config-to-constructor wire so a refactor of the factory shape
    can't silently downgrade ui_mode runs to api_mode."""
    provider_yaml = tmp_path / "providers.yaml"
    _gemini_only_provider_yaml(provider_yaml)
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))

    target = _resolve_target("google_docs", {"mode": "ui_mode"})
    # Private attribute, but it IS the load-bearing field — the
    # invoke method branches on it. Asserting against the public
    # surface would require actually invoking, which costs a real
    # Chrome dance.
    assert target._mode == "ui_mode"  # type: ignore[attr-defined]


def test_resolve_google_docs_target_unknown_option_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo'd or out-of-schema key in target_options should fail
    fast with a Typer-friendly message naming the unknown key — not
    silently fall through to an ignored constructor kwarg."""
    from typer import BadParameter

    provider_yaml = tmp_path / "providers.yaml"
    _gemini_only_provider_yaml(provider_yaml)
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))

    with pytest.raises(BadParameter, match="unknown target_options"):
        _resolve_target("google_docs", {"made_up_key": "x"})


def test_resolve_google_docs_target_invalid_mode_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``target_options.mode`` is constrained to a small enum.
    A typo (``"ui-mode"`` vs ``"ui_mode"``) must raise rather than
    fall through to the api_mode default."""
    from typer import BadParameter

    provider_yaml = tmp_path / "providers.yaml"
    _gemini_only_provider_yaml(provider_yaml)
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))

    with pytest.raises(BadParameter, match=r"target_options\.mode"):
        _resolve_target("google_docs", {"mode": "ui-mode"})


def test_resolve_pair_indirect_honours_technique_restriction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``attack_options.pair_indirect.indirect_techniques`` should
    narrow PairIndirectAttack to exactly the named techniques —
    the ui_mode demo needs this so each PAIR round dispatches one
    Chrome dance instead of 21."""
    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(
        yaml.safe_dump(
            {
                "CLAUDE_ATTACKER": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
                "CLAUDE_JUDGE": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
            }
        )
    )
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))

    plugin = _resolve_attack("pair_indirect", {"indirect_techniques": ["white_text"]})
    # Integrated PairIndirectAttack owns the technique list directly.
    assert [t.id for t in plugin._techniques] == ["white_text"]  # type: ignore[attr-defined]


def test_resolve_pair_indirect_unknown_technique_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo'd technique ID in attack_options must raise here —
    not silently produce an empty cross-product at run time."""
    from typer import BadParameter

    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(
        yaml.safe_dump(
            {
                "CLAUDE_ATTACKER": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
                "CLAUDE_JUDGE": {
                    "provider_type": "anthropic",
                    "model_id": "claude-sonnet-4-6",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
            }
        )
    )
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))

    with pytest.raises(BadParameter, match="unknown technique ids"):
        _resolve_attack(
            "pair_indirect", {"indirect_techniques": ["white_text", "made_up_technique"]}
        )


def test_resolve_static_payload_rejects_options() -> None:
    """Attack types that don't accept options must fail fast when
    a YAML mistakenly passes them — silent drop would be worse
    than a clean error."""
    from typer import BadParameter

    with pytest.raises(BadParameter, match="doesn't accept attack_options"):
        _resolve_attack("static_payload", {"whatever": "x"})


def test_run_workspace_pair_ui_config_loads_cleanly() -> None:
    """``configs/run_workspace_pair_ui.yaml`` round-trips through
    ``load_run_config`` and carries the ui_mode + technique-restriction
    shape the demo relies on."""
    cfg_path = Path(__file__).resolve().parents[1] / "configs" / "run_workspace_pair_ui.yaml"
    cfg = load_run_config(cfg_path)
    assert cfg.target_name == "google_docs"
    assert cfg.target_options == {"mode": "ui_mode"}
    assert cfg.attacks == ["tap_indirect"]
    assert cfg.attack_options == {
        "tap_indirect": {
            "indirect_techniques": ["white_text"],
            "seed_prompt_path": "test_prompt.txt",
            "max_depth": 4,
            "branching_factor": 4,
            "keep_top": 2,
            "success_threshold": 0.95,
        }
    }
    assert cfg.behaviors == [
        "override_summarise_to_translate",
        "inject_fake_system_message",
    ]
    assert cfg.judges == ["strong_reject"]
    # Hard cap — ui_mode shares one Chrome profile.
    assert cfg.max_concurrency == 1


def test_resolve_strong_reject_missing_role_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the provider YAML exists but has no ``CLAUDE_JUDGE``
    role, the factory says so explicitly. A ``KeyError`` from deep
    inside the provider loader would be confusing on a fresh
    checkout — surface the exact role name + a hint at the wire
    format."""
    from typer import BadParameter

    provider_yaml = tmp_path / "providers.yaml"
    provider_yaml.write_text(yaml.safe_dump({"OTHER_ROLE": {}}))
    monkeypatch.setenv("ADVTEST_PROVIDER_CONFIG", str(provider_yaml))
    with pytest.raises(BadParameter, match="no CLAUDE_JUDGE role"):
        _resolve_judge("strong_reject")


# ----------------------------------------------------------------------
# YAML loading
# ----------------------------------------------------------------------


def test_load_run_config_round_trips_a_minimal_yaml(tmp_path: Path) -> None:
    cfg_path = _write_run_config(
        tmp_path,
        attacks=["static_payload"],
        max_queries=5,
    )
    cfg = load_run_config(cfg_path)
    assert cfg.target_name == "stub"
    assert cfg.attacks == ["static_payload"]
    assert cfg.judges == ["heuristic"]
    assert cfg.budget.max_queries == 5


def test_load_run_config_rejects_non_mapping(tmp_path: Path) -> None:
    from typer import BadParameter

    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump([1, 2, 3]))
    with pytest.raises(BadParameter, match="YAML mapping"):
        load_run_config(bad)


# ----------------------------------------------------------------------
# CLI invocation: grid + mcts strategies, budget enforcement
# ----------------------------------------------------------------------


def test_cli_run_grid_strategy_persists_results(tmp_path: Path) -> None:
    """End-to-end: synthetic RunConfig → ``advtest run --strategy grid``
    → run completes, run_result.json lands on disk, stdout JSON
    summary matches."""
    cfg_path = _write_run_config(
        tmp_path,
        attacks=["static_payload"],
        max_queries=1,
    )
    out_dir = tmp_path / "out"
    result = _RUNNER.invoke(
        app,
        [
            "run",
            "--config",
            str(cfg_path),
            "--strategy",
            "grid",
            "--output-dir",
            str(out_dir),
            # Disable DB sink so the test doesn't drop an
            # ``advtest.db`` next to the runner.
            "--db-url",
            "",
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["strategy"] == "grid"
    assert summary["total_traces"] == 1  # max_queries=1 caps to a single trace
    persisted = Path(summary["output_dir"])
    assert persisted.exists()
    assert (persisted / "run_result.json").exists()
    assert list((persisted / "traces").glob("*.json"))


def test_cli_run_mcts_strategy_invokes_mcts(tmp_path: Path) -> None:
    cfg_path = _write_run_config(
        tmp_path,
        attacks=["static_payload"],
        max_queries=2,
    )
    out_dir = tmp_path / "out"
    result = _RUNNER.invoke(
        app,
        [
            "run",
            "--config",
            str(cfg_path),
            "--strategy",
            "mcts",
            "--output-dir",
            str(out_dir),
            "--db-url",
            "",
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["strategy"] == "mcts"
    assert summary["total_traces"] == 2


def test_cli_rejects_unknown_strategy(tmp_path: Path) -> None:
    cfg_path = _write_run_config(tmp_path, attacks=["static_payload"])
    result = _RUNNER.invoke(app, ["run", "--config", str(cfg_path), "--strategy", "no_such"])
    assert result.exit_code != 0
    assert "unknown strategy" in result.output


def test_cli_rejects_unknown_attack(tmp_path: Path) -> None:
    cfg_path = _write_run_config(tmp_path, attacks=["no_such_attack"])
    result = _RUNNER.invoke(app, ["run", "--config", str(cfg_path)])
    assert result.exit_code != 0
    assert "unknown attack" in result.output
