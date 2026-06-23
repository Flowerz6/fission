#!/usr/bin/env python3
"""BRANCHING-tree variant of demo_flow (horizontal + vertical).

Same to-and-fro mails as demo_flow.py, but each docs_gemini EXPLOIT step declares
a "parent" (the earlier exploit it branches from), so the dashboard tree FANS OUT
(width) AND grows DOWNWARD (depth) - like the original project's TAP tree.
One node = one exploit; mails are delivery (not nodes).

Does NOT touch demo_flow.py. Pair with scripts/demo_scenario_tree.json.

RUN (operator):
    .venv/Scripts/python.exe scripts/demo_flow_tree.py --scenario scripts/demo_scenario_tree.json
    # offline tree preview (no browser / no Claude / no email):
    .venv/Scripts/python.exe scripts/demo_flow_tree.py --scenario scripts/demo_scenario_tree.json --plan
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

# demo_flow.py lives next to this file; running as a script puts scripts/ on sys.path.
from demo_flow import DemoRunner, _load_env_file


class TreeRunner(DemoRunner):
    """DemoRunner whose exploit steps form a BRANCHING tree via step['parent'].

    Inherits the entire mail / Claude / browser flow unchanged; only the
    docs_gemini exploit step is overridden so each node links to the parent
    named in the scenario (instead of the immediately-previous node)."""

    def __init__(self, *, scenario: dict[str, Any], args: argparse.Namespace) -> None:
        super().__init__(scenario=scenario, args=args)
        self._id2trace: dict[str, str] = {}  # scenario step id -> persisted trace id
        self._depth: dict[str, int] = {}      # scenario step id -> tree depth

    async def _step_docs_gemini(self, step: dict[str, Any]) -> dict[str, Any]:
        prompt = step["prompt"]
        document_id = str(step.get("document_id") or self.document_id).strip()
        if not document_id:
            raise SystemExit("docs_gemini step needs a document_id")
        print(f"  [gemini]  doc={document_id} | prompt={prompt[:90]!r}")
        if self.plan_only:
            text = f"[gemini placeholder {step.get('id')}]"
        else:
            from advtest_ext.adapters.google_docs.ui_mode import ui_mode_invoke

            page = await self._ensure_docs_page()
            reset = not self._docs_turn_done
            self._docs_turn_done = True
            resp = await ui_mode_invoke(
                document_id=document_id,
                user_prompt=prompt,
                trace_id=f"demo-{uuid.uuid4().hex[:8]}",
                output_dir=self.output_dir,
                external_page=page,
                reset_conversation=reset,
            )
            text = resp.text or ""
            self.gemini_replies.append(text)
            print(f"  [gemini]  reply = {text[:160]!r}{'...' if len(text) > 160 else ''}")
        self._persist_branch(
            step_id=str(step.get("id")),
            parent_step=step.get("parent"),
            prompt=prompt,
            response=text,
        )
        return {"response": text, "document_id": document_id}

    def _persist_branch(self, *, step_id: str, parent_step: Any, prompt: str, response: str) -> None:
        if self._factory is None:
            return
        from advtest_ext.core.models import AttackTrace, Payload
        from advtest_ext.storage import insert_trace, session_scope

        parent_tid = self._id2trace.get(parent_step) if parent_step else None
        depth = (self._depth.get(parent_step, 0) + 1) if parent_step else 0
        tid = f"trace-{self.run_id}-{len(self._exploit_traces):02d}"
        fired = bool(self.canary_token) and self.canary_token.lower() in response.lower()
        trace = AttackTrace(
            id=tid,
            run_id=self.run_id,
            behavior_id="demo_multiapp_ipi",
            payload=Payload(id=f"pl-{tid}", text=f"exploit {step_id}", generator_name="demo_flow_tree"),
            target_name="google_docs_multiturn",
            raw_request=str(prompt)[:2000],
            raw_response=str(response)[:4000],
            verdict=("success" if fired else "refused"),
            parent_id=parent_tid,  # branch from the named parent => width + depth
            depth=depth,
            metadata={"branch": {"step": step_id, "parent": parent_step}},
        )
        try:
            with session_scope(self._factory) as s:
                insert_trace(s, run_id=self.run_id, trace=trace)
            self._exploit_traces.append(trace)
            self._id2trace[step_id] = tid
            self._depth[step_id] = depth
            print(f"  [db] +node {step_id} depth={depth} parent={parent_step}")
        except Exception as exc:
            print(f"  [db] persist failed ({type(exc).__name__}: {exc})")

    def _print_summary(self) -> None:
        print("\n" + "=" * 64)
        print("BRANCHING DEMO COMPLETE")
        print("=" * 64)
        if self._exploit_traces:
            max_depth = max(t.depth for t in self._exploit_traces)
            widths: dict[int, int] = {}
            for t in self._exploit_traces:
                widths[t.depth] = widths.get(t.depth, 0) + 1
            print(f"Run: {self.run_id}  ({len(self._exploit_traces)} exploit nodes, max depth {max_depth})")
            print(f"Width by depth: {dict(sorted(widths.items()))}")
            print(f"Tree (width + depth): http://127.0.0.1:8000/runs/{self.run_id}/tree")
        if not self.plan_only and self.canary_token:
            fired = self.canary_token.lower() in "\n".join(self.gemini_replies).lower()
            print(f"Canary {self.canary_token!r}: " + ("FIRED (Gemini echoed it)" if fired else "not detected (tree still demonstrated)"))


def main() -> None:
    _load_env_file()
    ap = argparse.ArgumentParser(description="Branching-tree multi-app IPI demo (horizontal + vertical)")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--outlook-profile", default=None)
    ap.add_argument("--gmail-profile", default=None)
    ap.add_argument("--document-id", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--db-url", default=None)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--keep-open", action="store_true")
    args = ap.parse_args()

    scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    asyncio.run(TreeRunner(scenario=scenario, args=args).run())


if __name__ == "__main__":
    main()
