"""Wiring tests for the QUILLFEATHER multi-turn attacks in the CLI.

These check registration, config parsing, signal specs, and the small
factory helpers WITHOUT invoking the provider-loading factories (which
would construct a real Anthropic client). No LLM, no browser.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from advtest_ext import _cli_factories, _cli_providers, cli
from advtest_ext.core.models import load_seed_behaviors
from advtest_ext.judges.heuristic import HeuristicJudge
from advtest_ext.judges.retrieval_verdict_judge import RetrievalVerdictJudge

if TYPE_CHECKING:
    from advtest_ext.core.models import RunConfig

_CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_new_attacks_and_target_registered() -> None:
    assert "crescendo" in _cli_factories._ATTACK_REGISTRY
    assert "tap_chain" in _cli_factories._ATTACK_REGISTRY
    assert "tap_crescendo" in _cli_factories._ATTACK_REGISTRY
    assert "google_docs_multiturn" in _cli_factories._TARGET_REGISTRY


def test_tap_crescendo_config_parses() -> None:
    cfg = cli.load_run_config(_CONFIGS / "run_quillfeather_tap_crescendo.yaml")
    assert cfg.attacks == ["tap_crescendo"]
    assert cfg.target_name == "google_docs_multiturn"
    opts = cfg.attack_options["tap_crescendo"]
    assert opts["branching_factor"] == 2
    assert opts["max_depth"] == 3
    assert opts["keep_top"] == 2


def test_crescendo_config_parses() -> None:
    cfg = cli.load_run_config(_CONFIGS / "run_quillfeather_crescendo.yaml")
    assert cfg.target_name == "google_docs_multiturn"
    assert cfg.attacks == ["crescendo"]
    assert cfg.behaviors == ["quillfeather_v1_persona"]
    assert cfg.max_concurrency == 1
    kinds = {s["kind"] for s in cfg.signals}
    assert kinds == {"canary_appears", "token_appears"}


def test_tap_chain_config_parses() -> None:
    cfg = cli.load_run_config(_CONFIGS / "run_quillfeather_tap_chain.yaml")
    assert cfg.attacks == ["tap_chain"]
    assert cfg.attack_options["tap_chain"]["handshake_token"] == "QUILLFEATHER-ENGINEER"


def test_signal_specs_build() -> None:
    from advtest_ext.judges.signals.registry import build_signal

    cfg = cli.load_run_config(_CONFIGS / "run_quillfeather_crescendo.yaml")
    for spec in cfg.signals:
        sig = build_signal(spec)
        assert sig is not None


def test_default_persona_ladder() -> None:
    ladder = _cli_factories._default_persona_ladder("Project QUILLFEATHER", "QUILLFEATHER-ENGINEER")
    assert len(ladder) == 4
    assert "QUILLFEATHER-ENGINEER" in ladder[-1]
    assert "Project QUILLFEATHER" in ladder[1]


def test_retrieval_judge_from_opts() -> None:
    j = _cli_factories._retrieval_judge_from_opts(
        {"entity": "QUILLFEATHER", "canary": "CODE-QF-4417"}
    )
    assert isinstance(j, RetrievalVerdictJudge)
    # No entity → generic heuristic judge.
    assert isinstance(_cli_factories._retrieval_judge_from_opts({}), HeuristicJudge)


def test_quillfeather_behavior_in_catalogue() -> None:
    behaviors = {b.id: b for b in load_seed_behaviors()}
    assert "quillfeather_v1_persona" in behaviors
    b = behaviors["quillfeather_v1_persona"]
    assert b.category == "instruction_override"
    assert "QUILLFEATHER-ENGINEER" in b.success_criteria


def test_retrieval_verdict_judge_registered_and_resolves() -> None:
    from advtest_ext.judges.retrieval_verdict_judge import RetrievalVerdictJudge

    assert "retrieval_verdict" in _cli_factories._JUDGE_REGISTRY
    judge = cli._resolve_judge("retrieval_verdict")  # type: ignore[attr-defined]
    assert isinstance(judge, RetrievalVerdictJudge)


def test_quillfeather_configs_use_retrieval_verdict_judge() -> None:
    for name in (
        "run_quillfeather_crescendo",
        "run_quillfeather_tap_chain",
        "run_quillfeather_tap_crescendo",
    ):
        cfg = cli.load_run_config(_CONFIGS / f"{name}.yaml")
        assert cfg.judges == ["retrieval_verdict"], name


def test_model_override_env_mapping() -> None:
    # The three CLI flags map to the three provider roles.
    assert _cli_providers._MODEL_OVERRIDE_ENV["CLAUDE_ATTACKER"] == "ADVTEST_ATTACKER_MODEL"
    assert _cli_providers._MODEL_OVERRIDE_ENV["CLAUDE_JUDGE"] == "ADVTEST_JUDGE_MODEL"
    assert _cli_providers._MODEL_OVERRIDE_ENV["GEMINI_TARGET"] == "ADVTEST_TARGET_MODEL"


# ----------------------------------------------------------------------
# Multi-phase runs: two sequential trees under one run_id.
# Uses the registry-resolvable `stub` target + `static_payload` attack
# so the whole run executes with no LLM and no browser.
# ----------------------------------------------------------------------


def _two_phase_config(tmp_path: Path) -> RunConfig:
    from advtest_ext.core.models import RunConfig, RunPhase

    behaviors = [b.id for b in load_seed_behaviors()][:2]
    return RunConfig(
        phases=[
            RunPhase(
                label="Gmail → Docs",
                target_name="stub",
                attacks=["static_payload"],
                behaviors=[behaviors[0]],
            ),
            RunPhase(
                label="Docs → Docs",
                target_name="stub",
                attacks=["static_payload"],
                behaviors=[behaviors[1]],
            ),
        ],
        judges=["heuristic"],
        output_dir=tmp_path,
    )


def test_multi_phase_config_parses_and_validates(tmp_path: Path) -> None:
    cfg = _two_phase_config(tmp_path)
    assert len(cfg.phases) == 2
    assert cfg.target_name is None  # top-level target omitted in phased mode
    assert [p.label for p in cfg.phases] == ["Gmail → Docs", "Docs → Docs"]


def test_unified_gmail_then_docs_config_parses() -> None:
    """The shipped unified two-phase config loads and carries the two
    phases with their distinct targets/attacks, shared run-level judge."""
    cfg = cli.load_run_config(_CONFIGS / "run_unified_gmail_then_docs.yaml")
    assert cfg.target_name is None
    assert [p.label for p in cfg.phases] == [
        "Gmail retrieval from Docs",
        "Docs retrieval from Docs",
    ]
    # BOTH phases drive the Gemini Docs side panel; they differ only in
    # what Gemini is steered to ground on — planted emails (phase 1) vs a
    # planted Drive doc (phase 2).
    assert cfg.phases[0].target_name == "google_docs_multiturn"
    assert cfg.phases[0].behaviors == ["injection_via_summary"]  # grounds on emails
    assert cfg.phases[1].target_name == "google_docs_multiturn"
    assert cfg.phases[1].behaviors == ["retrieval_via_docs"]  # grounds on a Drive doc
    assert cfg.phases[0].attacks == ["tap_crescendo"]
    assert cfg.phases[1].attacks == ["tap_crescendo"]
    # One shared LLM judge (StrongREJECT) across both phases, plus the
    # shared deterministic signals that flag the leak/marker wins.
    assert cfg.judges == ["ipi_verdict"]
    assert {s["name"] for s in cfg.signals} == {
        "hide_white",
        "system_prompt_leak",
        "beacon_exfil",
    }


def test_multi_phase_run_executes_sequentially_one_run_id(tmp_path: Path) -> None:
    """A 2-phase run produces ONE run_id whose traces are tagged with
    distinct, ordered phase metadata — the data the dashboard groups
    into one tree per phase."""
    import asyncio

    cfg = _two_phase_config(tmp_path)
    result = asyncio.run(
        cli._run_async(strategy="grid", config=cfg, output_dir=tmp_path, session_factory=None)
    )
    # Single shared run_id across all phases.
    assert result.run_id.startswith("run-")
    assert {t.run_id for t in result.traces} == {result.run_id}
    # Every trace carries a phase tag; both phases are represented, ordered.
    phases = {
        t.metadata["phase"]["index"]: t.metadata["phase"]["label"]
        for t in result.traces
        if "phase" in t.metadata
    }
    assert phases == {0: "Gmail → Docs", 1: "Docs → Docs"}
    assert all(t.metadata["phase"]["total"] == 2 for t in result.traces)


def test_single_phase_run_has_no_phase_metadata(tmp_path: Path) -> None:
    """Regression: a non-phased config runs exactly as before, with no
    phase tag stamped on traces."""
    import asyncio

    from advtest_ext.core.models import RunConfig

    behavior = next(b.id for b in load_seed_behaviors())
    cfg = RunConfig(
        target_name="stub",
        attacks=["static_payload"],
        behaviors=[behavior],
        judges=["heuristic"],
        output_dir=tmp_path,
    )
    result = asyncio.run(
        cli._run_async(strategy="grid", config=cfg, output_dir=tmp_path, session_factory=None)
    )
    assert result.traces
    assert all("phase" not in t.metadata for t in result.traces)
