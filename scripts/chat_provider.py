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


def _read_file(path: str) -> str | None:
    """Read a text file's contents, or None with a printed reason. The model is
    text-only, so a file means its text — binaries/images aren't supported."""
    p = Path(path).expanduser()
    if not p.is_file():
        print(f"(no such file: {p})\n")
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"({p.name} isn't UTF-8 text — this model is text-only, no binaries/images)\n")
        return None
    print(f"(attached {p.name}, {len(text)} chars — rides with your next message)\n")
    return f"[Attached file: {p.name}]\n```\n{text}\n```\n\n"


async def _run(role: str, config: str, system: str | None, preload: str | None) -> None:
    from advtest_ext.providers.base import ChatMessage
    from advtest_ext.providers.factory import load_provider_from_yaml

    provider = load_provider_from_yaml(config, role)
    print(f"chatting with role {role!r} via {config}")
    print("(/file <path> attaches a text file, /reset clears history, /exit quits)\n")

    history: list[ChatMessage] = []
    if system:
        history.append(ChatMessage(role="system", content=system))
    pending_file = _read_file(preload) if preload else None

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
            pending_file = None
            print("(history cleared)\n")
            continue
        if user.startswith("/file"):
            _, _, arg = user.partition(" ")
            if not arg.strip():
                print("(usage: /file <path>)\n")
                continue
            loaded = _read_file(arg.strip())
            if loaded is not None:
                pending_file = loaded
            continue

        content = user if pending_file is None else pending_file + user
        pending_file = None
        history.append(ChatMessage(role="user", content=content))
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
    ap.add_argument("--file", default=None, help="preload a text file into the first message")
    args = ap.parse_args()
    _quiet_logs()
    try:
        asyncio.run(_run(args.role, args.config, args.system, args.file))
    except KeyboardInterrupt:
        print("\n(bye)")


if __name__ == "__main__":
    main()
