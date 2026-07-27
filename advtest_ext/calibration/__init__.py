"""Oracle calibration set — ingest PUBLIC labelled adversarial
benchmarks so the oracle can be measured against a known ground truth
*before* any in-house human labelling exists.

This is the cold-start half of the measurement spine
(``docs/FISSION_MEASUREMENT_SPINE.md`` §7, refinement 2): the spine's
gold set grows slowly from expert review, but a calibration set of
externally-labelled examples lets you report the oracle's F1 / ECE from
day one and gives the multi-layer verifier a held-out validation set.

* ``datasets`` — the vendored source registry
  (``configs/calibration_datasets.yaml``): what benchmarks exist, which
  carry static ground-truth labels, and how each maps onto the
  :class:`advtest_ext.storage.models.CalibrationExample` schema.
* ``ingest`` — normalise a downloaded dump (JSONL) into
  ``CalibrationExample`` rows via a dataset's registry mapping.
"""

from advtest_ext.calibration.datasets import (
    CalibrationDatasetSource,
    LabelSpec,
    load_dataset_registry,
)
from advtest_ext.calibration.ingest import (
    CalibrationRecord,
    ingest_records,
    normalize_verdict,
    records_from_jsonl,
)

__all__ = [
    "CalibrationDatasetSource",
    "CalibrationRecord",
    "LabelSpec",
    "ingest_records",
    "load_dataset_registry",
    "normalize_verdict",
    "records_from_jsonl",
]
