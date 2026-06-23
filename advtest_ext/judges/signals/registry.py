"""Registry mapping signal-kind strings to Signal classes, plus the
:func:`build_signal` factory used by the orchestrator to turn
config dicts into concrete signal instances.

:class:`LlmJudgeVerdict` is intentionally omitted from the
factory: it requires a live judge instance, not a config-spec
description. The orchestrator constructs LlmJudgeVerdicts
directly from the existing ``judges:`` config block, so
operators don't need to mention them in the new ``signals:``
block.
"""

from __future__ import annotations

from typing import Any

from advtest_ext.judges.signals.base import Signal
from advtest_ext.judges.signals.canary_appears import CanaryAppears
from advtest_ext.judges.signals.http_response_shape import HttpResponseShape
from advtest_ext.judges.signals.regex_match import RegexMatch
from advtest_ext.judges.signals.token_appears import TokenAppears

# Operator-configurable signal kinds. LlmJudgeVerdict is omitted —
# orchestrator constructs it from the existing ``judges:`` block.
SIGNAL_REGISTRY: dict[str, type[Signal]] = {
    cls.KIND: cls for cls in (TokenAppears, RegexMatch, CanaryAppears, HttpResponseShape)
}


def build_signal(spec: dict[str, Any]) -> Signal:
    """Instantiate a :class:`Signal` from a config-spec dict.

    Spec shape::

        {
          "kind": "token_appears",   # required, registry key
          "name": "bazinga_appears", # required, operator label
          ... kind-specific kwargs ...
        }

    Raises :class:`ValueError` if ``kind`` is missing or
    unrecognised, or if the kind-specific constructor rejects
    its kwargs. Errors fail the config load, not the run.
    """
    if not isinstance(spec, dict):
        raise ValueError(f"signal spec must be a dict, got {type(spec).__name__}")
    if "kind" not in spec:
        raise ValueError(f"signal spec missing 'kind': {spec!r}")
    kind = spec["kind"]
    cls = SIGNAL_REGISTRY.get(kind)
    if cls is None:
        valid = ", ".join(sorted(SIGNAL_REGISTRY))
        raise ValueError(f"unknown signal kind {kind!r}; available kinds: {valid}")
    kwargs = {k: v for k, v in spec.items() if k != "kind"}
    if "name" not in kwargs:
        raise ValueError(f"signal spec missing 'name' (kind={kind!r}): {spec!r}")
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ValueError(f"signal kind {kind!r} rejected its arguments: {exc}") from exc
