"""Offline tests for the pilot pipeline orchestrator (stages 1-2).

No browser, no DB — proves selection and assembly work so an operator can flip
--live to produce the deliverable.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "run_pilot_pipeline", ROOT / "scripts" / "run_pilot_pipeline.py"
)
_pipe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pipe)

from advtest_ext.guides.completeness import check_guide  # noqa: E402
from advtest_ext.guides.corpus import load_guide  # noqa: E402
from advtest_ext.guides.to_tap_crescendo import map_guide  # noqa: E402

GUIDES = ROOT / "guides"


def test_gaps_selection_picks_only_gap_harms() -> None:
    paths = _pipe.select_guides(GUIDES, "gaps")
    assert paths, "expected some gap-harm families"
    for p in paths:
        data = _pipe._cov._load(p)
        assert data and _pipe._cov._harm(data) in _pipe.GAP_HARMS


def test_explicit_id_selection() -> None:
    paths = _pipe.select_guides(GUIDES, "docs_unauth_data_modification,sheets_pii_exfil_formula")
    stems = {p.stem for p in paths}
    assert stems == {"docs_unauth_data_modification", "sheets_pii_exfil_formula"}


def test_every_gap_family_assembles_to_a_runnable_plan() -> None:
    # The core pipeline guarantee: each selected family compiles to a plan and
    # has no blocking oracle errors — i.e. it is READY to run live.
    for p in _pipe.select_guides(GUIDES, "gaps"):
        g = load_guide(p)
        errs = [i for i in check_guide(g) if str(getattr(i, "level", "")).lower().endswith("error")]
        assert not errs, f"{g.id} has blocking issues: {errs}"
        plan = map_guide(g)  # must not raise
        assert plan is not None


def test_new_gap_seeds_are_selected_by_gaps() -> None:
    stems = {p.stem for p in _pipe.select_guides(GUIDES, "gaps")}
    for expected in (
        "docs_unauth_data_modification",
        "sheets_unauth_data_modification",
        "gmail_malware_payload_delivery",
        "gmail_pii_thread_harvest_exfil",
        "docs_misinformation_false_metric",
    ):
        assert expected in stems, f"{expected} should be a selected gap family"
