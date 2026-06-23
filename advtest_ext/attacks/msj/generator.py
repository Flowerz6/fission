"""``MsjGenerator`` — Many-shot Jailbreaking (Anil et al. 2024).

Builds a long prompt of N alternating user/assistant turns, each
showing the assistant *complying* with a harmful-shaped request, then
appends the actual target query as the final user message. The
attacker LLM picks up the pattern of compliance from the demos and
continues it.

Per Anil 2024, ASR follows a power-law in N (the number of shots) on
frontier models, with the curve plateauing in the high hundreds for
some behaviours. Our default N is 256; the orchestrator records
``n_shots`` in attempt metadata so the demo heatmap can plot
ASR-vs-N.

Reference: anil2024msj — "Many-Shot Jailbreaking", NeurIPS 2024.
See FOUNDATIONS.md ("Attack techniques: Ship these").
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from advtest_ext.providers.base import ChatMessage

_DEFAULT_DEMOS_PATH = Path(__file__).resolve().parent / "demonstrations.yaml"


@dataclass
class MsjDemonstration:
    """One (user, assistant) demonstration pair from the catalogue."""

    user: str
    assistant: str

    def estimate_chars(self) -> int:
        # Cheap token-count proxy; good enough for picking a default N
        # under a target's context limit.
        return len(self.user) + len(self.assistant)


@dataclass
class MsjPrompt:
    """The materialised many-shot prompt: ordered messages + the
    target query that lives on the final user turn. The transcript
    string is a flattened textual rendering used by indirect-injection
    delivery (where the whole conversation is stuffed into a document
    field rather than sent as a real ChatMessage list)."""

    messages: list[ChatMessage]
    target_query: str
    n_shots: int
    seed: int | None = None
    estimated_chars: int = 0
    transcript: str = ""
    used_demonstration_indices: list[int] = field(default_factory=list)


def _load_demonstrations(path: Path) -> list[MsjDemonstration]:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a YAML list at top level")
    out: list[MsjDemonstration] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: each entry must be a mapping")
        out.append(MsjDemonstration(user=str(entry["user"]), assistant=str(entry["assistant"])))
    if not out:
        raise ValueError(f"{path}: no demonstrations loaded")
    return out


class MsjGenerator:
    """Builds many-shot prompts.

    Sampling is **with replacement** when ``n_shots`` exceeds the
    demonstration count — the catalogue at any reasonable size will be
    ≪ 256, and Anil 2024's ablations show repetition doesn't materially
    hurt the effect.
    """

    def __init__(
        self,
        *,
        demonstrations_path: Path | None = None,
        n_shots: int = 256,
        seed: int | None = None,
    ) -> None:
        if n_shots < 1:
            raise ValueError("n_shots must be >= 1")
        self.n_shots = n_shots
        self.seed = seed
        self._demos = _load_demonstrations(demonstrations_path or _DEFAULT_DEMOS_PATH)

    @property
    def demonstrations(self) -> Sequence[MsjDemonstration]:
        return list(self._demos)

    def build(self, *, target_query: str) -> MsjPrompt:
        """Produce the materialised prompt for a single target query."""
        rng = random.Random(self.seed)
        indices: list[int] = [rng.randrange(len(self._demos)) for _ in range(self.n_shots)]
        messages: list[ChatMessage] = []
        transcript_lines: list[str] = []
        estimated = 0
        for idx in indices:
            demo = self._demos[idx]
            messages.append(ChatMessage(role="user", content=demo.user))
            messages.append(ChatMessage(role="assistant", content=demo.assistant))
            transcript_lines.append(f"User: {demo.user}")
            transcript_lines.append(f"Assistant: {demo.assistant}")
            estimated += demo.estimate_chars()
        # Final user turn is the target query.
        messages.append(ChatMessage(role="user", content=target_query))
        transcript_lines.append(f"User: {target_query}")
        estimated += len(target_query)

        return MsjPrompt(
            messages=messages,
            target_query=target_query,
            n_shots=self.n_shots,
            seed=self.seed,
            estimated_chars=estimated,
            transcript="\n\n".join(transcript_lines),
            used_demonstration_indices=indices,
        )

    def metadata(self) -> dict[str, Any]:
        """Return a dict suitable for ``AttackAttempt.metadata['msj']``."""
        return {
            "n_shots": self.n_shots,
            "seed": self.seed,
            "demonstration_pool_size": len(self._demos),
        }


__all__ = ["MsjDemonstration", "MsjGenerator", "MsjPrompt"]
