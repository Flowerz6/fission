"""``RegexMatch`` — fires when a regex pattern matches in a chosen
surface.

For multi-token patterns where ``TokenAppears`` would need a
loop: e.g. "any of these internal tool names appears" via a
single alternation regex, or "an obvious phishing URL shape"
via a URL pattern.

Regex flags can be passed as ``re``-style integers (e.g. 2 for
IGNORECASE, 8 for MULTILINE). For YAML readability the operator
can also pass a list of flag-name strings — ``["IGNORECASE"]``
becomes ``re.IGNORECASE`` at construction time.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import ClassVar

from advtest_ext.judges.signals.base import (
    Signal,
    SignalContext,
    SignalResult,
    Surface,
)


class RegexMatch(Signal):
    """Regex pattern match against one surface."""

    KIND: ClassVar[str] = "regex_match"

    def __init__(
        self,
        *,
        name: str,
        pattern: str,
        where: Surface | str = Surface.FULL_RESPONSE,
        flags: int | Iterable[str] = 0,
    ) -> None:
        super().__init__(name=name)
        if not pattern:
            raise ValueError("pattern must be non-empty")
        if isinstance(where, Surface):
            self.where: Surface = where
        else:
            try:
                self.where = Surface(where)
            except ValueError as exc:
                valid = ", ".join(s.value for s in Surface)
                raise ValueError(f"unknown surface {where!r}; expected one of: {valid}") from exc
        self.pattern = pattern
        self._flags_int = _coerce_flags(flags)
        # Compile at construction — a malformed pattern fails the
        # config load, not the run.
        try:
            self._compiled = re.compile(pattern, self._flags_int)
        except re.error as exc:
            raise ValueError(f"invalid regex pattern {pattern!r}: {exc}") from exc

    async def evaluate(self, *, context: SignalContext) -> SignalResult:
        surface_text = context.surface(self.where)
        matches = self._compiled.findall(surface_text)
        if matches:
            # findall returns strings for no-group / single-group
            # patterns and tuples for multi-group. Normalise to a
            # string for the evidence preview.
            first = matches[0]
            preview = first if isinstance(first, str) else str(first)
            return SignalResult(
                fired=True,
                reasoning=(f"regex matched {len(matches)} time(s) in {self.where.value}"),
                evidence={
                    "match_count": len(matches),
                    "first_match": preview[:200],
                    "pattern": self.pattern,
                    "surface": self.where.value,
                },
            )
        return SignalResult(
            fired=False,
            reasoning=f"regex did not match in {self.where.value}",
            evidence={
                "pattern": self.pattern,
                "surface": self.where.value,
            },
        )


def _coerce_flags(flags: int | Iterable[str]) -> int:
    """Accept either an int bitmask or a YAML-friendly list of flag
    names (``["IGNORECASE", "MULTILINE"]``) and return the bitmask."""
    if isinstance(flags, int):
        return flags
    bitmask = 0
    for name in flags:
        if not hasattr(re, name):
            raise ValueError(f"unknown regex flag {name!r}")
        value = getattr(re, name)
        if not isinstance(value, (int, re.RegexFlag)):
            raise ValueError(f"re.{name} is not a flag value")
        bitmask |= int(value)
    return bitmask
