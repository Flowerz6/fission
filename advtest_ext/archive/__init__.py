"""MAP-Elites vulnerability-fingerprint archive.

Built ON TOP OF the existing :class:`advtest_ext.guides.models.Guide` and the
per-branch mutation metadata the attack machinery already emits at
``trace.extra_metadata['tap_crescendo']['mutation']`` — no parallel attack-spec
type, no new attack-time instrumentation. See ``docs/INTEGRATION_CONTRACT.md``.

Layout:

* :mod:`signature` — read a leaf trace's cumulative descriptor vector (the
  MAP-Elites cell coordinate) by walking its TAP ancestry (Option A).
* :mod:`posterior` — the per-cell Beta(alpha, beta) belief with a single
  signal-verified success channel, backfill discounting, and
  cross-version carry-forward seeding.
* :mod:`fingerprint` — snapshot a target profile's whole archive for drift
  comparison, and persist/reload it.
* :mod:`backfill` — best-effort, guide-keyed, resolution-per-row reconstruction
  of the archive from historical traces.
"""

from __future__ import annotations

from advtest_ext.archive.posterior import (
    BACKFILL_WEIGHT_DEFAULT,
    NATIVE_WEIGHT,
    BetaPosterior,
    Resolution,
    apply_observation,
)
from advtest_ext.archive.signature import (
    BASELINE,
    HOPS_BUCKETS,
    KEY_MUTATION_AXES,
    Signature,
    build_node_index,
    coarse_projection,
    count_cross_app_transitions,
    hops_bucket,
    resolve_objective_class,
    signature,
)

__all__ = [
    "BACKFILL_WEIGHT_DEFAULT",
    "BASELINE",
    "HOPS_BUCKETS",
    "KEY_MUTATION_AXES",
    "NATIVE_WEIGHT",
    "BetaPosterior",
    "Resolution",
    "Signature",
    "apply_observation",
    "build_node_index",
    "coarse_projection",
    "count_cross_app_transitions",
    "hops_bucket",
    "resolve_objective_class",
    "signature",
]
