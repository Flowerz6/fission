"""AgentDojo rollout backend — the cheap, deterministic grey-box track.

Implements :class:`~advtest_ext.strategy.targets.AttackTarget` against
AgentDojo (Debenedetti et al. 2024): it substitutes the attacker's
injection into a sample's placeholder vector, runs AgentDojo's simulated
tool-using agent against a target model, and returns AgentDojo's own
deterministic ``utility``/``security`` checks plus the trajectory. No
browser, no Workspace account — the success signal is objective and free
to recompute, which is why the PIMiner loop trains its strategy library
here before transferring to live targets.

The execution helpers (``build_pipeline``, ``run_trajectory``,
``extract_final_output``, ``serialize_trajectory``) are adapted from
PIMiner (github.com/Wang-Yanting/PIMiner, MIT) — its
``iterative_attack_common.py`` / ``benchmarks/agentdojo/reward.py``. All
``agentdojo`` imports are lazy (inside functions) so importing this module
never requires the heavy optional dependency; it is needed only to *run* a
rollout. Install it with ``pip install agentdojo``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import warnings
from typing import Any, Protocol

from advtest_ext.strategy.models import RolloutResult, Sample
from advtest_ext.strategy.targets import AttackTarget  # noqa: F401  (documents intent)

_PLACEHOLDER_RE = re.compile(r"\{INJECTION:([^}]+)\}")

_INSTALL_HINT = (
    "the AgentDojo backend needs the optional `agentdojo` package: "
    "`pip install agentdojo` (heavy — langchain + a pinned OpenAI SDK)."
)


def _require_agentdojo() -> None:
    try:
        import agentdojo  # noqa: F401
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(_INSTALL_HINT) from exc


# ---------------------------------------------------------------------------
# Pipeline construction (target model -> AgentDojo AgentPipeline)
# ---------------------------------------------------------------------------


def build_pipeline(
    target_model: str,
    *,
    endpoint: str | None = None,
    api_key_env: str | None = None,
) -> Any:
    """Build an AgentDojo ``AgentPipeline`` for ``target_model``.

    Routes by model-name prefix to the matching AgentDojo LLM element:
    ``claude-*`` -> AnthropicLLM, ``gemini-*`` -> GoogleLLM, an ``endpoint``
    (OpenAI-compatible, e.g. local vLLM) -> OpenAILLM pointed there, and
    otherwise the bare name (OpenAI enum) or an OpenAILLM for gpt-5/4.1.
    """
    _require_agentdojo()
    from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig

    def _cfg(llm: Any) -> Any:
        return PipelineConfig(
            llm=llm,
            model_id=None,
            defense=None,
            system_message_name=None,
            system_message=None,
        )

    if target_model.startswith("claude"):
        # NB: AgentDojo's AnthropicLLM sends an explicit `temperature`, which
        # 5.x Claude models reject (400 "temperature is deprecated for this
        # model"). Use a 4.x Claude target (e.g. claude-haiku-4-5), or an
        # OpenAI/Gemini/local target instead.
        from agentdojo.agent_pipeline.llms.anthropic_llm import AnthropicLLM
        from anthropic import AsyncAnthropic

        key = os.environ.get(api_key_env or "ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(f"{api_key_env or 'ANTHROPIC_API_KEY'} must be set")
        llm = AnthropicLLM(AsyncAnthropic(api_key=key), target_model)
        llm.name = target_model
        return AgentPipeline.from_config(_cfg(llm))

    if target_model.startswith("gemini"):
        from agentdojo.agent_pipeline.llms.google_llm import GoogleLLM
        from google import genai

        key = os.environ.get(api_key_env or "GEMINI_API_KEY") or os.environ.get(
            "GOOGLE_API_KEY"
        )
        if not key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) must be set")
        llm = GoogleLLM(target_model, genai.Client(api_key=key))
        return AgentPipeline.from_config(_cfg(llm))

    if endpoint:  # OpenAI-compatible server (local vLLM, OpenRouter, ...)
        import openai
        from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM

        key = os.environ.get(api_key_env or "OPENAI_API_KEY", "EMPTY")
        llm = OpenAILLM(openai.OpenAI(base_url=endpoint, api_key=key), target_model)
        llm.name = target_model
        return AgentPipeline.from_config(_cfg(llm))

    # Plain OpenAI model. Names in AgentDojo's ModelsEnum pass through as a
    # string; gpt-5/4.1 (not in the enum) get an explicit OpenAILLM.
    from agentdojo.models import ModelsEnum

    if target_model in {m.value for m in ModelsEnum}:
        return AgentPipeline.from_config(_cfg(target_model))
    import openai
    from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM

    llm = OpenAILLM(openai.OpenAI(), target_model)
    llm.name = target_model
    return AgentPipeline.from_config(_cfg(llm))


# ---------------------------------------------------------------------------
# Trajectory execution + rendering (adapted from PIMiner, MIT)
# ---------------------------------------------------------------------------


def run_trajectory(
    suite: Any, user_task: Any, injection_task: Any, injections: dict[str, str], pipeline: Any
) -> tuple[bool, bool, list[Any], str]:
    """Run one AgentDojo trajectory, returning ``(utility, security,
    messages, model_output_text)``. Reproduces ``run_task_with_pipeline``'s
    body so the intermediate ``messages`` (the trajectory) are captured."""
    _require_agentdojo()
    from agentdojo.functions_runtime import FunctionsRuntime
    from agentdojo.task_suite.task_suite import (
        functions_stack_trace_from_messages,
        model_output_from_messages,
    )

    # Injecting the attacker text into the environment can itself fail (e.g.
    # AgentDojo YAML-parses the injected field and a payload with unescaped
    # colons/brackets breaks it). That is a FAILED attack, not a harness
    # crash — return a clean miss so the loop keeps going and the attacker
    # learns to craft a payload that survives the carrier.
    try:
        environment = suite.load_and_inject_default_environment(injections)
        task_environment = (
            user_task.init_environment(environment)
            if hasattr(user_task, "init_environment")
            else environment
        )
    except Exception as e:
        return False, False, [], f"(environment injection failed: {e})"
    pre_environment = task_environment.model_copy(deep=True)

    runtime = FunctionsRuntime(suite.tools)
    prompt = user_task.PROMPT
    messages: list[Any] = []
    model_output = None
    for _ in range(3):
        try:
            _, _, task_environment, messages, _ = pipeline.query(
                prompt, runtime, task_environment
            )
        except Exception as e:  # AbortAgentError or similar carries partial state
            te = getattr(e, "task_environment", None)
            ms = getattr(e, "messages", None)
            if te is not None:
                task_environment = te
            if ms is not None:
                messages = ms
        model_output = model_output_from_messages(messages) if messages else None
        if model_output is not None:
            break
    if model_output is None:
        warnings.warn(
            f"Model output was None for {user_task.ID}+{getattr(injection_task, 'ID', None)}",
            stacklevel=2,
        )
        model_output = []

    stack = functions_stack_trace_from_messages(messages)
    utility = bool(
        suite._check_task_result(user_task, model_output or [], pre_environment, task_environment, stack)
    )
    security = False
    if injection_task is not None:
        security = bool(
            suite._check_task_result(
                injection_task, model_output or [], pre_environment, task_environment, stack
            )
        )
    return utility, security, messages, _model_output_text(model_output)


def _model_output_text(model_output: Any) -> str:
    text = ""
    if isinstance(model_output, list):
        for blk in model_output:
            t = blk.get("content") if isinstance(blk, dict) else getattr(blk, "content", "")
            if isinstance(t, str):
                text += t
            elif isinstance(t, list):
                for sub in t:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        text += sub.get("content", "") or sub.get("text", "")
    return text


def _block_text_and_calls(content: Any) -> tuple[str, list[tuple[str, str]]]:
    text, calls = "", []
    if isinstance(content, str):
        return content, calls
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict):
                if blk.get("type") == "text":
                    text += blk.get("content", "") or blk.get("text", "")
                elif blk.get("type") == "tool_use":
                    calls.append((blk.get("name"), json.dumps(blk.get("input") or {}, default=str)))
    return text, calls


def extract_final_output(messages: list[Any]) -> str:
    """Black-box view: the final assistant message's text + tool call(s)."""
    last = None
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role == "assistant":
            last = m
    if last is None:
        return "(no assistant message)"
    content = last.get("content") if isinstance(last, dict) else getattr(last, "content", "")
    text, calls = _block_text_and_calls(content)
    if isinstance(last, dict) and last.get("tool_calls"):
        for tc in last["tool_calls"]:
            fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", "")
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            calls.append((fn, json.dumps(args, default=str)))
    parts = [text.strip() or "(no assistant text)"]
    parts += [f"[tool_call] {n}({(a or '')[:1000]})" for n, a in calls] or ["(no tool call)"]
    return "\n".join(parts)


def serialize_trajectory(messages: list[Any], *, max_chars: int = 25_000) -> str:
    """White-box view: a compact, attacker-readable dump of the trajectory."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "?")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        text, calls = _block_text_and_calls(content)
        for n, a in calls:
            text += f"\n[tool_call] {n}({a[:1000]})"
        if isinstance(m, dict) and isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    body = blk.get("content", "")
                    if isinstance(body, list):
                        body = " ".join(
                            b.get("content", "") for b in body if isinstance(b, dict)
                        )
                    text += f"\n[tool_result] {str(body)[:2000]}"
        lines.append(f"<|{role}|>\n{text.strip()[:4000]}")
    out = "\n\n".join(lines)
    if len(out) > max_chars:
        half = max_chars // 2
        out = out[:half] + f"\n... [truncated {len(out) - max_chars} chars] ...\n" + out[-half:]
    return out


# ---------------------------------------------------------------------------
# The runner seam (real vs. fake) + the AttackTarget
# ---------------------------------------------------------------------------


class AgentDojoRunner(Protocol):
    def run(
        self,
        suite_name: str,
        user_task_id: str,
        injection_task_id: str | None,
        injections: dict[str, str],
    ) -> tuple[bool, bool, list[Any], str]: ...


class _RealRunner:
    """Caches the loaded suite and the constructed pipeline across rollouts
    (building either is expensive; a run reuses one target model)."""

    def __init__(
        self,
        target_model: str,
        *,
        benchmark_version: str = "v1.2.2",
        endpoint: str | None = None,
        api_key_env: str | None = None,
    ) -> None:
        self._target_model = target_model
        self._benchmark_version = benchmark_version
        self._endpoint = endpoint
        self._api_key_env = api_key_env
        self._suites: dict[str, Any] = {}
        self._pipeline: Any | None = None

    def _suite(self, suite_name: str) -> Any:
        _require_agentdojo()
        if suite_name not in self._suites:
            from agentdojo.task_suite.load_suites import get_suite

            self._suites[suite_name] = get_suite(self._benchmark_version, suite_name)
        return self._suites[suite_name]

    def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            self._pipeline = build_pipeline(
                self._target_model, endpoint=self._endpoint, api_key_env=self._api_key_env
            )
        return self._pipeline

    def run(
        self,
        suite_name: str,
        user_task_id: str,
        injection_task_id: str | None,
        injections: dict[str, str],
    ) -> tuple[bool, bool, list[Any], str]:
        suite = self._suite(suite_name)
        user_task = suite.get_user_task_by_id(user_task_id)
        injection_task = (
            suite.get_injection_task_by_id(injection_task_id) if injection_task_id else None
        )
        return run_trajectory(suite, user_task, injection_task, injections, self._get_pipeline())


class AgentDojoTarget:
    """A :class:`~advtest_ext.strategy.targets.AttackTarget` backed by
    AgentDojo. Pass ``runner`` to inject a fake for tests; otherwise a real
    AgentDojo pipeline is built lazily on first rollout."""

    def __init__(
        self,
        target_model: str = "gpt-4o-mini",
        *,
        benchmark_version: str = "v1.2.2",
        endpoint: str | None = None,
        api_key_env: str | None = None,
        threat_model: str = "black_box",
        runner: AgentDojoRunner | None = None,
    ) -> None:
        self._target_model = target_model
        self._black_box = threat_model == "black_box"
        self._runner: AgentDojoRunner = runner or _RealRunner(
            target_model,
            benchmark_version=benchmark_version,
            endpoint=endpoint,
            api_key_env=api_key_env,
        )

    def _vectors(self, sample: Sample) -> list[str]:
        if sample.injection_candidates:
            return list(sample.injection_candidates)
        # Fall back to the vector ids named in the placeholder context.
        return _PLACEHOLDER_RE.findall(sample.context_with_placeholder)

    async def rollout(self, sample: Sample, injection: str) -> RolloutResult:
        vectors = self._vectors(sample)
        if not vectors:
            raise ValueError(
                f"sample {sample.sample_key} has no injection vector to target "
                "(set injection_candidates or include {INJECTION:<id>} in the context)"
            )
        injections = {v: injection for v in vectors}
        # AgentDojo's pipeline.query is synchronous; keep the async loop free.
        utility, security, messages, model_text = await asyncio.to_thread(
            self._runner.run,
            sample.suite_name,
            sample.user_task_id,
            sample.injection_task_id or None,
            injections,
        )
        final = extract_final_output(messages) if messages else (model_text or "(empty)")
        if self._black_box:
            trajectory = ""
        else:  # white-box: show the trajectory, or the error when there is none
            trajectory = serialize_trajectory(messages) if messages else final
        return RolloutResult(
            utility=utility,
            security=security,
            final_output=final,
            trajectory=trajectory,
            extra={
                "target_model": self._target_model,
                "verdict_source": "agentdojo",
                "vectors": vectors,
            },
        )
