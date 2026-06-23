"""``HttpResponseShape`` — pattern match against the raw intercepted
JSONL capture, optionally URL-filtered to one endpoint.

The motivating use case is V3 from the Google Red Team Report:
reasoning leaks that surface only in the streamed Gemini
response body and never make it into the rendered side panel.
Operationally a HttpResponseShape signal is "regex over the
JSONL file, but only for entries whose URL contains a given
substring."

URL filter examples:

- ``url_filter: streamGenerate`` — only scan generation
  responses, skip ``quotaSummary`` / telemetry noise.
- ``url_filter: /v1/genai/`` — scan every Workspace Gemini API
  call (the default capture surface).
- ``url_filter: null`` (omit) — scan the entire file as a
  single string.
"""

from __future__ import annotations

import json
import re
from typing import ClassVar

from advtest_ext.judges.signals.base import (
    Signal,
    SignalContext,
    SignalResult,
)


class HttpResponseShape(Signal):
    """Regex over the intercepted JSONL, optionally URL-filtered."""

    KIND: ClassVar[str] = "http_response_shape"

    def __init__(
        self,
        *,
        name: str,
        pattern: str,
        url_filter: str | None = None,
        flags: int = 0,
    ) -> None:
        super().__init__(name=name)
        if not pattern:
            raise ValueError("pattern must be non-empty")
        self.pattern = pattern
        self.url_filter = url_filter
        try:
            self._compiled = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"invalid regex pattern {pattern!r}: {exc}") from exc

    async def evaluate(self, *, context: SignalContext) -> SignalResult:
        text = context.intercepted_traffic
        if not text:
            return SignalResult(
                fired=False,
                reasoning="no intercepted traffic captured for this trace",
                evidence={"url_filter": self.url_filter},
            )
        scan_text = _filter_by_url(text, self.url_filter) if self.url_filter else text
        if not scan_text:
            return SignalResult(
                fired=False,
                reasoning=(f"no intercepted entries matched url_filter {self.url_filter!r}"),
                evidence={"url_filter": self.url_filter},
            )
        matches = self._compiled.findall(scan_text)
        if matches:
            first = matches[0]
            preview = first if isinstance(first, str) else str(first)
            return SignalResult(
                fired=True,
                reasoning=(
                    f"pattern matched {len(matches)} time(s) in intercepted "
                    f"traffic (url_filter={self.url_filter!r})"
                ),
                evidence={
                    "match_count": len(matches),
                    "first_match": preview[:200],
                    "pattern": self.pattern,
                    "url_filter": self.url_filter,
                },
            )
        return SignalResult(
            fired=False,
            reasoning=(
                f"pattern did not match in intercepted traffic (url_filter={self.url_filter!r})"
            ),
            evidence={"pattern": self.pattern, "url_filter": self.url_filter},
        )


def _filter_by_url(jsonl_text: str, url_substring: str) -> str:
    """Return only JSONL entries whose URL contains ``url_substring``.

    Each entry is preserved verbatim (one line per entry) so
    pattern matching across multi-line bodies still works the
    same way the unfiltered scan does. Malformed lines are
    silently skipped.
    """
    out: list[str] = []
    for line in jsonl_text.splitlines():
        line = line.rstrip("\n")
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if url_substring in entry.get("url", ""):
            out.append(line)
    return "\n".join(out)
