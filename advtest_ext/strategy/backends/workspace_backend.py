"""Live Workspace rollout backend — the black-box track.

Implements :class:`~advtest_ext.strategy.targets.AttackTarget` against the
real Gemini-in-Workspace surface: it plants the attacker's injection into
a Google Doc, triggers the Gemini side panel (a journey such as
"summarise this document") through the logged-in Playwright browser, and
scores the reply with fission's layered oracle (deterministic signals +
the eight-axis rubric IPI judge) to decide whether the injected effect occurred.

Wiring (verified call sequence):
  plant  : ``corpus.docs_seeder.DocsCorpusSeeder.plant(visible_body=...)`` -> doc id
  trigger: ``adapters.google_docs.ui_mode.invoker.ui_mode_invoke(document_id=..., user_prompt=...)``
  score  : ``judges.signals`` (``build_signal`` + ``SignalContext``) +
           ``judges.rubric_ipi.RubricIpiJudge`` combined via
           ``core.orch_trace_meta`` (``classify_from_judge`` / ``resolve_guide_verdict``).

All of the above import lazily (inside :class:`_RealRunner`) so importing
this module needs neither Playwright nor a browser profile. A real run
needs ``ADVTEST_PLAYWRIGHT_PROFILE_DIR`` (a Chrome profile already signed
into the Workspace account) and a judge provider; there is NO
``GEMINI_API_KEY`` — the side panel *is* the model. Pass ``runner`` to
inject a fake for tests.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from advtest_ext.strategy.loop import DEFAULT_BENIGN_TRIGGER, splice_injection
from advtest_ext.strategy.models import RolloutResult, Sample


class WorkspaceRunner(Protocol):
    async def run(
        self, *, product: str, carrier_body: str, trigger_prompt: str, sample_key: str
    ) -> tuple[bool, bool, str, str, dict[str, Any]]: ...


class _RealRunner:
    """Plant -> trigger -> score against the live Docs side panel.

    Caches the warm Playwright session and the judge across rollouts.
    ``signal_specs`` are raw success-signal dicts (``{"kind","name",...}``,
    e.g. a guide's ``success.signals``); when given, a deterministic signal
    AND a judge-success are both required for a ``success`` verdict (the
    guide policy). With no specs, the verdict is judge-only.
    """

    def __init__(
        self,
        *,
        judge_provider: Any,
        signal_specs: list[dict[str, Any]] | None,
        output_dir: str,
        black_box: bool,
    ) -> None:
        self._judge_provider = judge_provider
        self._signal_specs = list(signal_specs or [])
        self._output_dir = output_dir
        self._black_box = black_box
        self._session: Any = None
        self._judge: Any = None

    async def _page(self) -> Any:
        from advtest_ext.adapters._ui_mode_common.config import resolve_profile_dir
        from advtest_ext.adapters._ui_mode_common.session import UiModeSession

        if self._session is None:
            self._session = UiModeSession(profile_dir=resolve_profile_dir())
        return await self._session.ensure_page()

    def _get_judge(self) -> Any:
        """Build the judge for this runner.

        NOTE: this black-box path always uses ``RubricIpiJudge`` — it takes no
        judge name from config (unlike the orchestrator path, where ``judges:``
        selects one). Consequence: with no live side-effect observation here,
        arrival renders ``unknown``, so exfiltration classes cap at ``partial``;
        text classes are unaffected.
        """
        from advtest_ext.judges.rubric_ipi import RubricIpiJudge

        if self._judge is None:
            if self._judge_provider is None:
                raise RuntimeError("WorkspaceTarget needs a judge provider to score")
            self._judge = RubricIpiJudge(provider=self._judge_provider)
        return self._judge

    async def run(
        self, *, product: str, carrier_body: str, trigger_prompt: str, sample_key: str
    ) -> tuple[bool, bool, str, str, dict[str, Any]]:
        from pathlib import Path

        from advtest_ext.adapters.google_docs.ui_mode.invoker import ui_mode_invoke
        from advtest_ext.corpus.docs_seeder import DocsCorpusSeeder

        if product != "google_docs":
            raise ValueError(f"WorkspaceTarget currently drives google_docs only; got {product!r}")

        seeder = DocsCorpusSeeder()
        # The injection is already spliced into carrier_body, so the whole doc
        # body is the summarize target; no extra hidden channel from the seeder.
        artifact = seeder.plant(visible_body=carrier_body, injection="")
        trace_id = f"ws-{uuid.uuid4().hex[:12]}"
        try:
            page = await self._page()
            resp = await ui_mode_invoke(
                document_id=artifact.artifact_id,
                user_prompt=trigger_prompt,
                trace_id=trace_id,
                output_dir=Path(self._output_dir),
                external_page=page,
                reset_conversation=True,
            )
        finally:
            try:
                seeder.teardown(artifact)
            except Exception:
                pass

        utility, security, verdict, judge_reason, fired, judge_unparsed, gated = await self._score(
            sample_key, trigger_prompt, resp
        )
        final = resp.text or "(no reply)"
        traj = ""
        if not self._black_box:
            traj = "\n".join(
                filter(
                    None,
                    [
                        resp.raw.get("document_body", ""),
                        resp.raw.get("model_thoughts", ""),
                    ],
                )
            )
        extra = {
            "verdict_source": "workspace_oracle",
            "verdict": verdict,
            "judge_reasoning": judge_reason,
            "fired_signals": fired,
            "judge_unparsed": judge_unparsed,
            "doc_id": artifact.artifact_id,
            "intercepted_traffic_path": resp.raw.get("intercepted_traffic_path"),
        }
        # Surface a rubric-gated effect the same way the orchestrator does
        # (orchestrator.py:320-322) so the operator sees it went through a gate.
        if gated:
            extra["rubric_gated"] = True
        return utility, security, final, traj, extra

    async def _score(
        self, sample_key: str, trigger_prompt: str, resp: Any
    ) -> tuple[bool, bool, str, str, list[str], bool, bool]:
        from advtest_ext.core.models import AttackTrace, Payload
        from advtest_ext.core.orch_trace_meta import (
            classify_from_judge,
            resolve_guide_verdict,
            safe_objective_class,
        )
        from advtest_ext.core.rescore import placeholder_behavior
        from advtest_ext.judges.signals.base import SignalContext
        from advtest_ext.judges.signals.registry import build_signal

        behavior = placeholder_behavior(sample_key)
        payload = Payload(id=sample_key, text=trigger_prompt, generator_name="piminer")
        trace = AttackTrace(
            id=f"wst-{uuid.uuid4().hex[:12]}",
            run_id="workspace-rollout",
            behavior_id=sample_key,
            payload=payload,
            target_name="WorkspaceTarget",
            raw_request=trigger_prompt,
            raw_response=resp.text or "",
            raw_response_data=dict(resp.raw or {}),
        )
        ctx = SignalContext(trace=trace, behavior=behavior)
        fired: list[str] = []
        for spec in self._signal_specs:
            result = await build_signal(spec).evaluate(context=ctx)
            if result.fired:
                fired.append(str(spec.get("kind", "signal")))

        judge = self._get_judge()
        judge_score = await self._evaluate_judge(judge, trace, behavior)
        # A crashed/unparseable judge is NOT a decision. Honour the error the way
        # rescore does — otherwise a judge failure silently becomes "refused" and
        # security=False, hiding a non-measurement as a real negative.
        if judge_score.error:
            return False, False, "error", judge_score.reasoning, fired, True, False

        base, gated = classify_from_judge(
            judge_score, objective_class=safe_objective_class(sample_key)
        )
        verdict = (
            resolve_guide_verdict(base, fired, is_seed_repro=False) if self._signal_specs else base
        )
        security = verdict == "success"
        utility = bool(resp.text) and verdict != "refused"
        return utility, security, verdict, judge_score.reasoning, fired, False, gated

    async def _evaluate_judge(self, judge: Any, trace: Any, behavior: Any) -> Any:
        """Call ``judge.evaluate``, supplying the R4 ground-truth packet and the
        R5 arrival summary when (and only when) the judge is the rubric judge.

        The packet is built from the run's success-signal specs (which carry the
        concrete canary/token/marker identifiers R4 joins against); same-shape
        decoys catch a fabrication. There is no live side-effect observation on
        this black-box path yet, so arrival renders ``unknown`` — exfil classes
        stay ``partial`` until an observer is wired, text classes (R5 exempt) can
        still reach ``success``."""
        from advtest_ext.judges.rubric_ipi import RubricIpiJudge

        if not isinstance(judge, RubricIpiJudge):
            return await judge.evaluate(trace=trace, behavior=behavior)

        from types import SimpleNamespace

        from advtest_ext.judges.arrival import render_arrival
        from advtest_ext.judges.ground_truth import build_packet

        guide = SimpleNamespace(success=SimpleNamespace(signals=self._signal_specs))
        packet = build_packet(trace, guide=guide).render()
        arrival = render_arrival(None)  # no side-effect observer on the black-box path
        return await judge.evaluate(
            trace=trace,
            behavior=behavior,
            ground_truth_packet=packet,
            arrival_observation=arrival,
        )


class WorkspaceTarget:
    """A :class:`~advtest_ext.strategy.targets.AttackTarget` driving live
    Gemini-in-Workspace. Maps a :class:`Sample` (carrier in
    ``context_with_placeholder``, trigger in ``user_task_prompt``) onto the
    plant/trigger/score runner. Inject ``runner`` for tests."""

    def __init__(
        self,
        *,
        product: str = "google_docs",
        trigger_prompt: str | None = None,
        judge_provider: Any = None,
        signal_specs: list[dict[str, Any]] | None = None,
        output_dir: str = "out/workspace_rollout",
        threat_model: str = "black_box",
        runner: WorkspaceRunner | None = None,
    ) -> None:
        self._product = product
        self._trigger_prompt = trigger_prompt
        self._black_box = threat_model == "black_box"
        self._runner: WorkspaceRunner = runner or _RealRunner(
            judge_provider=judge_provider,
            signal_specs=signal_specs,
            output_dir=output_dir,
            black_box=self._black_box,
        )

    async def rollout(self, sample: Sample, injection: str) -> RolloutResult:
        trigger = self._trigger_prompt or sample.user_task_prompt or DEFAULT_BENIGN_TRIGGER
        utility, security, final, traj, extra = await self._runner.run(
            product=self._product,
            carrier_body=splice_injection(sample.context_with_placeholder, injection),
            trigger_prompt=trigger,
            sample_key=sample.sample_key,
        )
        extra.setdefault("product", self._product)
        return RolloutResult(
            utility=utility,
            security=security,
            final_output=final,
            # The target owns the threat-model policy: black-box hides the
            # trajectory regardless of what the runner returns.
            trajectory="" if self._black_box else traj,
            extra=extra,
        )
