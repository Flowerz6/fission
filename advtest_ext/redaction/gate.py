"""The redaction gate — store full at rest, redact only on egress to the third-party model.

Fission keeps unredacted captured data at rest (the private oracle needs it) and sends only a
redacted projection to the third-party Venice model (FISSION_V2_PLAN §6, CAPTURE_SPEC §7,
data-handling.md). This module is the single, pure, tested chokepoint. It sits on three channels:

1. seed-at-upload   — :func:`scrub_seed` strips real captured data a seed should never carry.
2. evidence-ingest  — :func:`redact_evidence` makes the generator-readable projection of a capture.
3. exemplar / egress — :func:`redact_exemplar` / :func:`redact_for_egress` / :func:`guard` ensure
   nothing raw reaches a model-bound prompt.

Correctness is provable with synthetic real-looking data and no model call: anything matching a
sensitive pattern is replaced with a ``[REDACTED:<category>]`` marker before egress. Simulated
fixture canaries (opaque tokens that are not PII/secrets) pass through unchanged by design.

Findings record the category and count only — never the matched secret value — so the gate's own
reports and logs cannot leak what it redacted.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

# Order matters: multi-line blocks and keyed secrets first, so a value is consumed as one unit
# before a narrower pattern (e.g. email) could pick at a fragment of it.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
    ),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-=]+")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "secret_kv",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|authorization)\b\s*[:=]\s*\S+"
        ),
    ),
    ("cookie", re.compile(r"(?i)\b(?:set-cookie|cookie|session(?:id)?|sid)\b\s*[:=]\s*\S+")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("hex_secret", re.compile(r"\b[A-Fa-f0-9]{32,}\b")),
)

# Seed fields that can reach a model-bound prompt and so must be scrubbed at upload. Artifact
# bodies (the carrier genome) are scrubbed separately in :func:`scrub_seed`.
_EGRESSABLE_SEED_FIELDS: tuple[str, ...] = ("attack_string", "why_it_works", "objective")


@dataclass(frozen=True)
class RedactionResult:
    """A redaction outcome: the cleaned text and per-category counts (never the matched values)."""

    text: str
    findings: tuple[tuple[str, int], ...] = ()

    @property
    def redacted(self) -> bool:
        return bool(self.findings)

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(category for category, _ in self.findings)

    @property
    def total(self) -> int:
        return sum(count for _, count in self.findings)


def redact(text: str) -> RedactionResult:
    """Replace every sensitive span in ``text`` with a typed ``[REDACTED:<category>]`` marker."""
    findings: list[tuple[str, int]] = []
    for category, pattern in _PATTERNS:
        text, count = pattern.subn(f"[REDACTED:{category}]", text)
        if count:
            findings.append((category, count))
    return RedactionResult(text=text, findings=tuple(findings))


# --- channel 1: seed at upload ------------------------------------------------


def scrub_seed(seed: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Return a copy of ``seed`` with real captured data scrubbed, plus a {category: count} report.

    Scrubs every artifact ``body`` (the carrier genome and any other planted content) and the
    free-text fields that can reach a model prompt. A fixture seed with only simulated secrets
    scrubs to itself (no findings).
    """
    scrubbed = dict(seed)
    report: dict[str, int] = {}

    artifacts = seed.get("artifacts")
    if isinstance(artifacts, list):
        new_artifacts: list[Any] = []
        for artifact in artifacts:
            if isinstance(artifact, Mapping) and isinstance(artifact.get("body"), str):
                result = redact(artifact["body"])
                new_artifacts.append({**artifact, "body": result.text})
                _merge(report, result)
            else:
                new_artifacts.append(artifact)
        scrubbed["artifacts"] = new_artifacts

    for field in _EGRESSABLE_SEED_FIELDS:
        value = seed.get(field)
        if isinstance(value, str) and value:
            result = redact(value)
            scrubbed[field] = result.text
            _merge(report, result)

    return scrubbed, report


def _merge(report: dict[str, int], result: RedactionResult) -> None:
    for category, count in result.findings:
        report[category] = report.get(category, 0) + count


# --- channel 2: evidence / output at ingest -----------------------------------


def redact_evidence(target_response: str) -> RedactionResult:
    """The generator-readable projection of a capture's ``target_response``.

    The full, unredacted response is stored separately at rest (the private oracle); this projection
    is the only form any generator-facing path may read (CAPTURE_SPEC §7).
    """
    return redact(target_response)


# --- channel 3: exemplar selection / egress -----------------------------------


def redact_exemplar(text: str) -> str:
    """Redacted exemplar text for the few-shot selector — never raw evidence into a prompt."""
    return redact(text).text


def redact_for_egress(prompt: str) -> RedactionResult:
    """The final gate on a model-bound prompt: redact immediately before it leaves for Venice."""
    return redact(prompt)


Complete = Callable[[str], Awaitable[str]]


def guard(complete: Complete) -> Complete:
    """Wrap a model ``complete`` so every prompt is redacted on egress.

    Any Venice-bound caller (the directed generator, ``generate_cells``) routes through this so raw
    content cannot reach the provider even if a caller forgets to redact first.
    """

    async def guarded(prompt: str) -> str:
        return await complete(redact_for_egress(prompt).text)

    return guarded


__all__ = [
    "Complete",
    "RedactionResult",
    "guard",
    "redact",
    "redact_evidence",
    "redact_exemplar",
    "redact_for_egress",
    "scrub_seed",
]
