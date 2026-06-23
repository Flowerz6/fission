"""Provider / config resolution helpers for the ``advtest`` CLI.

Leaf helpers (no dependency on the attack/target/judge factories): resolve the
providers YAML path, load a provider role, apply model overrides, and resolve
indirect-injection technique lists, seed prompts, and the DB URL. Extracted
from ``cli.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import typer

_DEFAULT_DB_URL = "sqlite:///./advtest.db"

# Default path to the providers YAML used to source providers
# (Gemini for Workspace targets, Claude for PAIR/TAP attacker + judge).
# Resolves to ``<repo>/configs/dev.yaml`` relative to this module — so a
# bare ``advtest run`` from anywhere in the project finds it. Override
# via ``ADVTEST_PROVIDER_CONFIG``.
_DEFAULT_PROVIDER_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "dev.yaml"


def _resolve_provider_config_path(*, missing_roles_hint: str) -> Path:
    """Resolve ``ADVTEST_PROVIDER_CONFIG`` to a Path that exists, or
    raise a typer-friendly error.

    ``missing_roles_hint`` is interpolated into the error message
    (e.g. ``"CLAUDE_ATTACKER and CLAUDE_JUDGE roles"`` or
    ``"a GEMINI_TARGET role"``) so the operator sees exactly which
    roles the caller needs.
    """
    cfg_path = Path(os.environ.get("ADVTEST_PROVIDER_CONFIG") or _DEFAULT_PROVIDER_CONFIG)
    if not cfg_path.exists():
        raise typer.BadParameter(
            f"provider config not found at {cfg_path} — "
            f"set ADVTEST_PROVIDER_CONFIG or create the file with {missing_roles_hint}"
        )
    return cfg_path


# Per-role model_id override env vars. The `advtest run` flags
# --attacker-model / --judge-model / --target-model set these before
# the orchestrator is built; every provider load funnels through
# `_apply_model_override`, so one CLI flag retargets the model without
# editing the providers YAML. model_id is immutable after a Provider is
# constructed, so the override rebuilds the ProviderConfig.
_MODEL_OVERRIDE_ENV: dict[str, str] = {
    "CLAUDE_ATTACKER": "ADVTEST_ATTACKER_MODEL",
    "CLAUDE_JUDGE": "ADVTEST_JUDGE_MODEL",
    "GEMINI_TARGET": "ADVTEST_TARGET_MODEL",
}


def _apply_model_override(provider: Any, role: str) -> Any:
    """If a model-override env var is set for ``role``, rebuild the
    provider on a copy of its config with the new ``model_id``.
    Otherwise return the provider unchanged."""
    env_key = _MODEL_OVERRIDE_ENV.get(role)
    override = os.environ.get(env_key) if env_key else None
    if not override:
        return provider
    from advtest_ext.providers.factory import get_provider

    return get_provider(provider.config.model_copy(update={"model_id": override}))


def _load_provider_role(cfg_path: Path, role: str, *, missing_role_hint: str) -> Any:
    """Load a single role out of the providers YAML, with a typer-
    friendly error when the role isn't defined.

    ``missing_role_hint`` is appended to the "no <role> role" message
    so the operator sees what to add (the canonical example
    ``provider_type: anthropic, model_id: …`` shape, or attack-specific
    context like "PAIR's inner loop needs a judge…").

    Applies any ``--attacker/judge/target-model`` override for ``role``
    so the CLI flag wins over the YAML model_id.
    """
    from advtest_ext.providers import load_provider_from_yaml

    try:
        provider = load_provider_from_yaml(cfg_path, role)
    except KeyError as exc:
        raise typer.BadParameter(f"{cfg_path} has no {role} role — {missing_role_hint}") from exc
    return _apply_model_override(provider, role)


def _resolve_indirect_techniques(opts: dict[str, Any], *, option_path: str) -> list[Any] | None:
    """Validate + resolve the optional ``indirect_techniques`` list
    in attack options. Returns ``None`` for "no restriction" so the
    full default registry runs.

    ``option_path`` is interpolated into the error message
    (e.g. ``"attack_options.pair_indirect.indirect_techniques"``).
    """
    technique_ids = opts.get("indirect_techniques")
    if technique_ids is None:
        return None
    if not isinstance(technique_ids, list) or not all(isinstance(t, str) for t in technique_ids):
        raise typer.BadParameter(f"{option_path} must be a list of technique-ID strings")
    from advtest_ext.attacks.indirect_injection.techniques import (
        build_default_techniques,
    )

    registry = build_default_techniques()
    missing = [t for t in technique_ids if t not in registry]
    if missing:
        raise typer.BadParameter(
            f"unknown technique ids in {option_path}: {missing!r}; "
            f"available: {sorted(registry)[:8]}..."
        )
    return [registry[t] for t in technique_ids]


def _resolve_seed_prompt(opts: dict[str, Any], *, option_path: str) -> str | None:
    """Resolve the optional ``seed_prompt_path`` option to the
    prompt body (or ``None`` when the option isn't set).

    Relative paths resolve against CWD so a config-side relative
    path like ``test_prompt.txt`` works from the repo-root
    invocation site.
    """
    seed_prompt_path = opts.get("seed_prompt_path")
    if seed_prompt_path is None:
        return None
    if not isinstance(seed_prompt_path, str):
        raise typer.BadParameter(f"{option_path} must be a string")
    resolved = Path(seed_prompt_path).expanduser()
    if not resolved.is_absolute():
        resolved = Path.cwd() / resolved
    if not resolved.exists():
        raise typer.BadParameter(
            f"{option_path} not found: {resolved} — relative paths "
            "resolve against the current working directory"
        )
    seed_prompt = resolved.read_text(encoding="utf-8").strip()
    if not seed_prompt:
        raise typer.BadParameter(
            f"{option_path} {resolved} is empty — remove the option or fill the file"
        )
    return seed_prompt


def _resolve_db_url(
    *,
    cli_flag: str | None,
    config_value: str | None,
) -> str:
    """Resolve the SQLAlchemy URL using the documented chain:
    ``--db-url`` flag, then RunConfig's ``db_url``, then the
    ``ADVTEST_DB_URL`` env var, then the project-local
    ``sqlite:///./advtest.db``. So a bare ``advtest run`` lands rows
    in the same SQLite the dashboard reads by default.

    The empty string is treated as "explicitly disable the DB sink"
    so a CI run that wants the file-only path can opt out via
    ``--db-url ''``.
    """
    if cli_flag is not None:
        return cli_flag
    if config_value:
        return config_value
    env = os.environ.get("ADVTEST_DB_URL")
    if env is not None:
        return env
    return _DEFAULT_DB_URL
