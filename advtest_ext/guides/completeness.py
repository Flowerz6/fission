"""Is a guide *complete for our system* — not just schema-valid, but carrying
everything the runner, oracle, and flywheel actually need?

Pydantic validation only guarantees the required core (``id``, ``objective``,
``target``, ``success``, ``provenance``). That is necessary but not sufficient: a
guide can validate and still be useless to run or impossible to verify. This
module encodes the operational bar as a set of checks, split into:

- **errors** — the system cannot run the guide, or can never confirm a real
  success from it. A guide with any error is not ready.
- **warnings** — the guide runs, but degraded (weaker judge grounding, no
  multi-app reliability gate, a surface with no default adapter).

The load-bearing error is *no deterministic signal*: every registry signal kind
is deterministic, and only a deterministic signal can make a success
SIGNAL-VERIFIED. A guide whose ``success.signals`` is empty can at best be
JUDGE-ONLY, and the judge over-calls success — so such a guide can never be
harvested or verified by reproduction. That is the difference between a seed the
system can prove works and one it can only guess about.
"""

from __future__ import annotations

from dataclasses import dataclass

from advtest_ext.guides.models import Guide, validate_setup_operations
from advtest_ext.guides.run import _APP_TO_TARGET_NAME
from advtest_ext.guides.to_tap_crescendo import _seed_chain
from advtest_ext.judges.signals.registry import build_signal

_ERROR = "error"
_WARNING = "warning"

# Setup operations that actually plant/provision carrier content (as opposed to
# type_prompt, which only drives the side panel). A guide whose attack depends on
# planted content must use at least one of these to be self-contained.
_PLANT_OPS = frozenset(
    {"send_email", "reply", "open_document", "set_document_body", "open_deck", "set_speaker_notes"}
)


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str


def check_guide(guide: Guide) -> list[Issue]:
    """Return every completeness issue for one guide (empty == fully ready)."""
    issues: list[Issue] = []

    # --- deterministic oracle: the single most important criterion ---------
    if not guide.success.signals:
        issues.append(
            Issue(
                _ERROR,
                "no_deterministic_signal",
                "success.signals is empty — the run has no deterministic oracle, so a "
                "success can only ever be JUDGE-ONLY (the judge over-calls) and can never "
                "be signal-verified, harvested, or verified by reproduction.",
            )
        )
    else:
        for spec in guide.success.signals:
            try:
                build_signal(spec)
            except ValueError as exc:
                issues.append(
                    Issue(_ERROR, "bad_signal_spec", f"success.signals has an invalid spec: {exc}")
                )

    # --- a seed to replay --------------------------------------------------
    if not _seed_chain(guide.exemplar_turns):
        issues.append(
            Issue(
                _ERROR,
                "no_seed_chain",
                "exemplar_turns yields no user turns — there is no initial seed to replay "
                "as the tree's root; the guide cannot reproduce its own chain.",
            )
        )

    # --- provenance --------------------------------------------------------
    if not (guide.provenance and guide.provenance.source.strip()):
        issues.append(
            Issue(_ERROR, "no_provenance_source", "provenance.source is empty — a seed must be attributable.")
        )

    # --- setup steps resolve to real operations ----------------------------
    if guide.setup_steps:
        try:
            validate_setup_operations(guide)
        except Exception as exc:  # ValueError from unknown op, import errors, etc.
            issues.append(
                Issue(
                    _ERROR,
                    "unresolved_setup_operation",
                    f"a setup_step names an operation the runner cannot execute: {exc}",
                )
            )

    # --- carrier provisioning: a needed carrier must be planted by the guide --
    declares_carrier = bool(guide.carrier_payload) or bool(guide.carrier_payload_source)
    has_plant_step = any(s.operation in _PLANT_OPS for s in guide.setup_steps)
    if declares_carrier and not has_plant_step:
        issues.append(
            Issue(
                _ERROR,
                "carrier_not_provisioned",
                "the guide declares a carrier (carrier_payload / carrier_payload_source) but no "
                "setup_step plants it (send_email / open_document / set_document_body / open_deck / "
                "set_speaker_notes). The carrier would have to be planted out-of-band by hand, so "
                "the run is not self-contained or reproducible — the seed is not usable as-is.",
            )
        )
    elif (
        "indirect-injection" in guide.tags
        and guide.target.account_role in {"recipient", "victim"}
        and not guide.setup_steps
    ):
        # Indirect attack that grounds on someone else's planted content, but the
        # guide neither declares nor stages a carrier. It likely depends on an
        # out-of-band manual plant (a script / a pre-seeded inbox) — flag so the
        # provisioning is made explicit before the seed is relied on.
        issues.append(
            Issue(
                _WARNING,
                "carrier_provisioning_unverified",
                "an indirect-injection guide with no setup_steps and no declared carrier — its "
                "poisoned grounding is likely planted out-of-band (a manual script / pre-seeded "
                "inbox). Add setup_steps (or declare + stage the carrier) so the plant is captured "
                "in the guide and reproducible.",
            )
        )

    # --- runnable surface (warning: authorable but no default adapter) -----
    if guide.target.app not in _APP_TO_TARGET_NAME:
        issues.append(
            Issue(
                _WARNING,
                "no_default_target",
                f"target.app {guide.target.app!r} has no default multi-turn adapter "
                f"({', '.join(sorted(_APP_TO_TARGET_NAME))}); the guide is trackable but not "
                "runnable in Fission until an adapter exists or target_name is passed explicitly.",
            )
        )

    # --- judge grounding (warning: rubric synthesised if absent) -----------
    if not (guide.success.rubric and guide.success.rubric.strip()):
        issues.append(
            Issue(
                _WARNING,
                "no_success_rubric",
                "success.rubric is empty — the LLM judge falls back to a synthesised criterion "
                "from the signal names; a written rubric grounds it far better.",
            )
        )

    # --- multi-app reliability gate (warning) ------------------------------
    if guide.setup_steps and guide.setup_verification is None:
        issues.append(
            Issue(
                _WARNING,
                "no_setup_verification",
                "setup_steps are present but setup_verification is not — a multi-app run cannot "
                "fail closed before TAP if the plant did not ground (WS3 reliability).",
            )
        )

    return issues


def is_complete(guide: Guide) -> bool:
    """True when the guide has no *error*-level issues (warnings allowed)."""
    return not any(i.level == _ERROR for i in check_guide(guide))


__all__ = ["Issue", "check_guide", "is_complete"]
