"""Seed-corpus coverage vs the Google pilot's 300 target.

Maps every guide in ``guides/`` onto the harm-type x surface matrix from
``docs/SEED_GENERATOR_PLAN.md`` and prints what is covered, the empty cells, and
a rough progress estimate toward the ~36 families / ~300 variants target. A
family == one guide (one lineage); variants are fanned out at run time.

    python scripts/seed_coverage.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GUIDES = ROOT / "guides"

# Google harm taxonomy (+ our custom confidential-disclosure), in severity-ish order.
HARMS = [
    "data_exfiltration",
    "pii_credential_extraction",
    "rogue_actions",
    "unauthorized_data_modification",
    "malware_payload_delivery",
    "misinformation",
    "phishing",
    "disrupting_workflow",
    "confidential_disclosure",
]
SURFACES = ["google_docs", "google_sheets", "gmail", "google_slides", "google_studio", "google_calendar"]

# Target families per the plan matrix (used only to show progress, not enforced).
TARGET_FAMILIES = {
    "data_exfiltration": 7, "pii_credential_extraction": 5, "rogue_actions": 5,
    "unauthorized_data_modification": 6, "malware_payload_delivery": 4,
    "misinformation": 4, "phishing": 3, "disrupting_workflow": 2,
    "confidential_disclosure": 3,
}
TARGET_VARIANTS = 300
VARIANTS_PER_FAMILY = 8

# Keyword -> harm, matched against threat_class + tags + id (first hit wins, in order).
HARM_RULES: list[tuple[str, str]] = [
    ("unauthorized_data_modification", "unauthorized_data_modification"),
    ("data-modification", "unauthorized_data_modification"),
    ("malware", "malware_payload_delivery"),
    ("payload_delivery", "malware_payload_delivery"),
    ("payload-delivery", "malware_payload_delivery"),
    ("phish", "phishing"),
    ("credential", "pii_credential_extraction"),
    ("pii", "pii_credential_extraction"),
    ("exfil", "data_exfiltration"),
    ("data_exfiltration", "data_exfiltration"),
    ("misinfo", "misinformation"),
    ("integrity", "misinformation"),
    ("disclosure", "confidential_disclosure"),
    ("sysprompt", "confidential_disclosure"),
    ("skill_leak", "confidential_disclosure"),
    ("rogue", "rogue_actions"),
    ("unauthorized_action", "rogue_actions"),
    ("disrupt", "disrupting_workflow"),
    ("override", "disrupting_workflow"),
    ("translate", "disrupting_workflow"),
]


def _load(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text()) if path.suffix in (".yaml", ".yml") else json.loads(
            path.read_text()
        )
    except Exception:
        return None
    return data if isinstance(data, dict) and "id" in data and "target" in data else None


def _harm(g: dict) -> str:
    hay = " ".join(
        [str(g.get("threat_class") or ""), " ".join(g.get("tags") or []), str(g.get("id") or "")]
    ).lower()
    for needle, harm in HARM_RULES:
        if needle in hay:
            return harm
    return "data_exfiltration"  # default bucket; most of the corpus is exfil/override


def _surface(g: dict) -> str:
    tgt = g.get("target") or {}
    return str(tgt.get("app") or "unknown")


def main() -> None:
    grid: dict[tuple[str, str], list[str]] = defaultdict(list)
    harm_counts: dict[str, int] = defaultdict(int)
    paths = sorted(list(GUIDES.glob("*.yaml")) + list(GUIDES.glob("*.yml")) + list(GUIDES.glob("*.json")))
    guides = [g for g in (_load(p) for p in paths) if g]
    for g in guides:
        h, s = _harm(g), _surface(g)
        grid[(h, s)].append(g["id"])
        harm_counts[h] += 1

    # --- matrix ---
    col_w = 7
    hdr = "harm \\ surface".ljust(30) + "".join(s.replace("google_", "")[:col_w].ljust(col_w + 1) for s in SURFACES) + " TOTAL"
    print(hdr)
    print("-" * len(hdr))
    for h in HARMS:
        row = h.ljust(30)
        for s in SURFACES:
            n = len(grid.get((h, s), []))
            row += (str(n) if n else "·").ljust(col_w + 1)
        row += str(harm_counts.get(h, 0))
        print(row)

    # --- gaps ---
    print("\nEMPTY high-value cells (harm x surface with 0 families):")
    priority = [
        ("unauthorized_data_modification", "google_sheets"),
        ("unauthorized_data_modification", "google_docs"),
        ("malware_payload_delivery", "gmail"),
        ("data_exfiltration", "google_slides"),
        ("rogue_actions", "google_studio"),
        ("pii_credential_extraction", "google_studio"),
    ]
    for h, s in priority:
        if not grid.get((h, s)):
            print(f"  - {h} x {s.replace('google_','')}")

    # --- progress ---
    families = len(guides)
    print(f"\nFamilies (guides) total: {families}")
    print("Per-harm families vs target:")
    for h in HARMS:
        got, tgt = harm_counts.get(h, 0), TARGET_FAMILIES.get(h, 0)
        flag = "  <-- under" if got < tgt else ""
        print(f"  {h:32} {got:>2} / {tgt:<2}{flag}")
    est_variants = families * VARIANTS_PER_FAMILY
    print(
        f"\nRough progress to {TARGET_VARIANTS}: {families} families x ~{VARIANTS_PER_FAMILY} "
        f"variants = ~{est_variants} candidate variants "
        f"({min(100, round(100 * est_variants / TARGET_VARIANTS))}% of target, before live success-filtering)."
    )


if __name__ == "__main__":
    main()
