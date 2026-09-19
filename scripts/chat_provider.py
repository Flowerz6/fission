#!/usr/bin/env python
"""Interactive terminal chat with a configured provider role.

Loads a role from the provider YAML (default: your ADVTEST_PROVIDER_CONFIG, i.e.
hybrid.yaml) and holds a back-and-forth conversation with it, keeping history.
Handy for poking the RunPod Qwen attacker (CLAUDE_ATTACKER) directly.

Run it interactively (its stdin is a prompt), e.g. from this session:
    ! poetry run python scripts/chat_provider.py
    ! poetry run python scripts/chat_provider.py --role CLAUDE_JUDGE --system "You are terse."

Commands inside the chat:  /reset  (clear history)   /exit  (quit)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _quiet_logs() -> None:
    """Drop the per-call provider telemetry (structlog info events) so the chat
    reads cleanly. The repo doesn't configure structlog, so it prints every
    level by default — filter to WARNING+ here."""
    logging.disable(logging.INFO)
    try:
        import structlog

        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    except Exception:
        pass


async def _run(role: str, config: str, system: str | None) -> None:
    from advtest_ext.providers.base import ChatMessage
    from advtest_ext.providers.factory import load_provider_from_yaml

    provider = load_provider_from_yaml(config, role)
    print(f"chatting with role {role!r} via {config}")
    print("(/reset clears history, /exit quits, Ctrl-D quits)\n")

    history: list[ChatMessage] = []
    if system:
        history.append(ChatMessage(role="system", content=system))

    while True:
        try:
            user = input("you> ").strip()
        except EOFError:
            print()
            break
        if not user:
            continue
        if user == "/exit":
            break
        if user == "/reset":
            history = [h for h in history if h.role == "system"]
            print("(history cleared)\n")
            continue

        history.append(ChatMessage(role="user", content=user))
        try:
            resp = await provider.complete(history)
        except Exception as exc:  # keep the REPL alive on a transient error
            print(f"[error: {type(exc).__name__}: {exc}]\n")
            history.pop()  # drop the unanswered turn
            continue
        text = resp.text or "(empty response)"
        history.append(ChatMessage(role="assistant", content=text))
        print(f"\n{role.split('_')[-1].lower()}> {text}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--role", default="CLAUDE_ATTACKER", help="provider role from the YAML")
    ap.add_argument(
        "--config",
        default=os.environ.get("ADVTEST_PROVIDER_CONFIG", str(_REPO_ROOT / "configs" / "hybrid.yaml")),
    )
    ap.add_argument("--system", default=None, help="optional system prompt")
    args = ap.parse_args()
    _quiet_logs()
    try:
        asyncio.run(_run(args.role, args.config, args.system))
    except KeyboardInterrupt:
        print("\n(bye)")


if __name__ == "__main__":
    main()
