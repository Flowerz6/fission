"""Materialize accepted carrier mutations before a TAP branch runs live."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from advtest_ext.guides.stage import (
    bind_setup_target_options,
    stage_guide,
    verify_setup_result,
)

_log = structlog.get_logger(__name__)

_PALETTE_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "then",
    "with",
    "no",
    "not",
    "all",
    "that",
    "this",
    "into",
    "from",
    "when",
    "which",
    "there",
    "here",
    "them",
}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _PALETTE_STOP}


def cover_offpalette_fields(operator: Any, patch: dict[str, Any]) -> list[str]:
    """Reject a COVER mutation that drifted off the operator's palette.

    The cover operator is meant to vary the benign scenario *within* its
    ``cell_values`` (e.g. vendor-renewal / onboarding / postmortem). A weak model
    ignores the palette and invents an unrelated document ("a sustainable footwear
    product pitch"), which Gemini then summarises instead of our scenario — noise,
    not an attack. A mutated field whose text shares NO content word with any
    cell_value has left the palette; reject-and-don't-record. Only cover-role
    operators with a palette are checked (payload/format operators vary structure,
    not topic, so a word-overlap rule would false-reject them).
    """
    if operator is None or getattr(operator, "carrier_role", "full") != "cover":
        return []
    cells = getattr(operator, "cell_values", None) or []
    palette: set[str] = set()
    for cell in cells:
        if isinstance(cell, str):
            palette |= _content_words(cell)
    if not palette:
        return []
    off: list[str] = []
    for path, value in patch.items():
        if isinstance(value, str) and value.strip() and not (_content_words(value) & palette):
            off.append(path)
    return off


def carrier_invariant_violations(guide: Guide, variant: Guide) -> list[str]:
    """Anchors the attack NEEDS to survive a carrier mutation; any that are missing
    from the mutated carrier mean the variant is inert (it can no longer fire its
    own oracle) and must be rejected before a live run is spent on it.

    Drift (a weak mutator dropping the canary / egress sink / success token while
    "mutating" the payload) is the dominant waste in un-gated fan-out — this is the
    reject-and-don't-record gate. Anchors are the literal strings the guide's success
    depends on: its canary, its entity (e.g. the attacker sink host), and every
    ``token_appears`` / ``canary_appears`` signal's literal. Non-literal signals
    (regex, exfil_sink shape) are not checkable here and are left to live scoring.
    """
    anchors: list[str] = []
    for value in (getattr(guide, "canary", None), getattr(guide, "entity", None)):
        if isinstance(value, str):
            anchors.append(value)
    for spec in guide.success.signals:
        if not isinstance(spec, dict):
            continue
        if spec.get("kind") == "token_appears" and isinstance(spec.get("token"), str):
            anchors.append(spec["token"])
        if spec.get("kind") == "canary_appears":
            canary = spec.get("canary") or spec.get("token")
            if isinstance(canary, str):
                anchors.append(canary)
    anchors = [a for a in dict.fromkeys(anchors) if a and len(a) >= 3]
    if not anchors:
        return []
    texts = [getattr(variant, "carrier_payload", "") or ""]
    for step in getattr(variant, "setup_steps", []) or []:
        for value in (getattr(step, "params", None) or {}).values():
            if isinstance(value, str):
                texts.append(value)
    blob = "\n".join(texts)
    return [a for a in anchors if a not in blob]


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from advtest_ext.adapters._ui_mode_common import UiModeSessionPool
    from advtest_ext.core.account_pool import AccountPool
    from advtest_ext.core.recipe_operations import OperationRegistry
    from advtest_ext.document.target import DocumentTarget
    from advtest_ext.guides.models import Guide


@dataclass
class MaterializedMutation:
    node_id: str
    patch: dict[str, Any]
    bindings: dict[str, Any] = field(default_factory=dict)
    setup_result: Any = None
    verified: bool = False
    cleanup_status: str = "pending"


class GuideMutationMaterializer:
    """Apply an allow-listed Guide patch and bind its staged carrier to a target."""

    def __init__(
        self,
        *,
        guide: Guide,
        target: DocumentTarget,
        variables: dict[str, str] | None = None,
        operations: OperationRegistry | None = None,
        account_pool: AccountPool | None = None,
        session_pool: UiModeSessionPool | None = None,
        cleanup_callback: Callable[[MaterializedMutation], Awaitable[None] | None] | None = None,
    ) -> None:
        self.guide = guide
        self.target = target
        self.variables = variables
        self.operations = operations
        self.account_pool = account_pool
        self.session_pool = session_pool
        self.cleanup_callback = cleanup_callback
        self.records: list[MaterializedMutation] = []

    async def apply(self, node: dict[str, Any]) -> MaterializedMutation | None:
        mutation = node.get("mutation") or {}
        operator_name = mutation.get("operator_name")
        operator = next(
            (op for op in self.guide.mutation_operators if op.name == operator_name),
            None,
        )
        # Deterministic carrier transforms (e.g. TIP encoding) compute the patch
        # in code from the ORIGINAL carrier content, rather than trusting the
        # strategist's free-text artifact_patch. This keeps the encoding correct
        # and reproducible: "tip:base64" re-encodes the seed's carrier byte-for-
        # byte the same way every run. Convention: mutation["transform"] =
        # "tip:<scheme>" (append ":dp" for the depersonalisation lever).
        transform = mutation.get("transform")
        if isinstance(transform, str) and transform.startswith("tip:") and operator is not None:
            patch = self._tip_patch(operator, transform)
        else:
            patch = mutation.get("artifact_patch") or {}
        if not isinstance(patch, dict) or not patch:
            return None
        allowed = {
            field
            for op in self.guide.mutation_operators
            if op.name == operator_name
            for field in op.mutable_fields
        }
        unknown = set(patch) - allowed
        if unknown:
            raise RuntimeError(f"carrier mutation touches non-mutable fields: {sorted(unknown)}")

        variant = self.guide.model_copy(deep=True)
        # Staging a branch must never execute the attack's own prompt-driving
        # setup steps. It only plants/opens the mutated carrier.
        variant.setup_steps = [
            step for step in variant.setup_steps if step.operation not in {"type_prompt"}
        ]
        # Assemble the planted carrier in code from a benign cover and the payload
        # so the attacker never has to (and never can) drop the injection. Per the
        # operator's carrier_role, its free-text proposal supplies ONE part; the
        # other is taken from the guide and the full document is stitched here.
        role = getattr(operator, "carrier_role", "full") if operator else "full"
        payload = (self.guide.carrier_payload or "").strip()
        if role in {"cover", "payload"} and payload and transform is None:
            for path, value in list(patch.items()):
                if not isinstance(value, str):
                    continue
                seed_body = _get_guide_path(self.guide, path)
                seed_body = seed_body if isinstance(seed_body, str) else ""
                cover = (
                    seed_body.replace(payload, "").strip()
                    if payload in seed_body
                    else seed_body.strip()
                )
                if role == "cover":
                    # Proposal is a NEW benign document; keep the seed payload.
                    patch[path] = f"{value.strip()}\n\n{payload}".strip()
                else:
                    # Proposal is a NEW payload; keep the seed's benign cover and
                    # re-sync carrier_payload so the control and signals track it.
                    new_payload = value.strip()
                    patch[path] = f"{cover}\n\n{new_payload}".strip()
                    variant.carrier_payload = new_payload
        elif payload and transform is None:
            # role == "full": the proposal is the whole document. Guarantee the
            # payload survived a wholesale rewrite; re-append it if the model
            # silently produced a payload-free document.
            for path, value in list(patch.items()):
                if isinstance(value, str) and payload not in value:
                    patch[path] = f"{value.rstrip()}\n\n{payload}".strip()
        for path, value in patch.items():
            _set_guide_path(variant, path, value)
        # Invariant gate: reject-and-don't-record a mutation that dropped the
        # attack's success-bearing anchors (drift). Done BEFORE staging so a
        # drifted variant never costs a live browser run and never pollutes ASR.
        missing = carrier_invariant_violations(self.guide, variant)
        if missing:
            _log.warning(
                "mutation.rejected_invariant",
                node=str(node["id"]),
                operator=operator_name,
                missing=missing,
            )
            node["materialization"] = {
                "verified": False,
                "rejected": "invariant",
                "missing": missing,
            }
            return None
        # Palette gate: reject a cover mutation that drifted off its scenario palette
        # (the "sustainable footwear" drift) before staging.
        offpalette = cover_offpalette_fields(operator, patch)
        if offpalette:
            _log.warning(
                "mutation.rejected_offpalette",
                node=str(node["id"]),
                operator=operator_name,
                fields=offpalette,
            )
            node["materialization"] = {
                "verified": False,
                "rejected": "offpalette",
                "fields": offpalette,
            }
            return None
        result = await stage_guide(
            variant,
            variables=self.variables,
            operations=self.operations,
            account_pool=self.account_pool,
            session_pool=self.session_pool,
        )
        verify_setup_result(variant, result)
        bindings = bind_setup_target_options(variant, result)
        binder = getattr(self.target, "bind_carrier", None)
        if not callable(binder):
            raise RuntimeError("target cannot bind a per-branch staged carrier")
        bound = binder(**bindings)
        if inspect.isawaitable(bound):
            await bound
        record = MaterializedMutation(
            node_id=str(node["id"]),
            patch=dict(patch),
            bindings=bindings,
            setup_result=result,
            verified=True,
        )
        self.records.append(record)
        node["materialization"] = {
            "bindings": bindings,
            "verified": True,
            "cleanup_status": record.cleanup_status,
        }
        return record

    async def apply_control(self, node: dict[str, Any]) -> MaterializedMutation | None:
        """Stage a paired carrier with mutable attack content neutralized."""
        fields = {
            field
            for operator in self.guide.mutation_operators
            if operator.dimension == "carrier_placement"
            for field in operator.mutable_fields
        }
        if not fields:
            return None
        variant = self.guide.model_copy(deep=True)
        variant.setup_steps = [
            step for step in variant.setup_steps if step.operation not in {"type_prompt"}
        ]
        # A causal control must be the SAME document with the attack removed, so
        # the target behaves as it would on the genuine doc. The only payload-free
        # text we can use without fabricating one is the guide's own benign cover:
        # when carrier_payload is recorded verbatim and is a clean substring of the
        # field, strip just the payload and keep what remains. If we cannot derive
        # a payload-free version from the guide's own content, we do NOT invent a
        # document and we do NOT reuse the poisoned one — we skip the control
        # (return None) so a run never silently pairs against the real payload.
        payload = (self.guide.carrier_payload or "").strip()
        patch: dict[str, str] = {}
        for field_path in fields:
            try:
                current = _get_guide_path(self.guide, field_path)
            except Exception:
                current = None
            if not (isinstance(current, str) and payload and payload in current):
                return None
            neutral = current.replace(payload, "").strip()
            if not neutral:
                return None
            patch[field_path] = neutral
        for path, value in patch.items():
            _set_guide_path(variant, path, value)
        result = await stage_guide(
            variant,
            variables=self.variables,
            operations=self.operations,
            account_pool=self.account_pool,
            session_pool=self.session_pool,
        )
        verify_setup_result(variant, result)
        bindings = bind_setup_target_options(variant, result)
        binder = getattr(self.target, "bind_carrier", None)
        if not callable(binder):
            raise RuntimeError("target cannot bind a per-branch staged carrier")
        bound = binder(**bindings)
        if inspect.isawaitable(bound):
            await bound
        record = MaterializedMutation(
            node_id=str(node["id"]),
            patch=patch,
            bindings=bindings,
            setup_result=result,
            verified=True,
        )
        self.records.append(record)
        node["materialization"] = {
            "bindings": bindings,
            "verified": True,
            "control": "no_carrier",
        }
        return record

    def _tip_patch(self, operator: Any, transform: str) -> dict[str, Any]:
        """Build a deterministic Task-in-Prompt patch for ``operator``.

        Reads each of the operator's mutable fields from the ORIGINAL guide and
        rewrites it as ``x_task + E(u)`` (see ``attacks.tip_encoding``). So the
        planted carrier for this branch is the seed's carrier content, encoded
        with the named scheme and wrapped in a decode-and-act task. ``transform``
        is ``"tip:<scheme>"`` with an optional ``":dp"`` suffix for the
        depersonalisation framing."""
        from advtest_ext.attacks.tip_encoding import supported_schemes, tip_transform

        parts = transform.split(":")
        scheme = parts[1] if len(parts) > 1 else ""
        depersonalise = len(parts) > 2 and parts[2] == "dp"
        if scheme not in supported_schemes():
            raise RuntimeError(
                f"unknown TIP scheme in transform {transform!r}; supported: {supported_schemes()}"
            )
        if not operator.mutable_fields:
            raise RuntimeError(f"TIP operator {operator.name!r} declares no mutable_fields")
        patch: dict[str, Any] = {}
        for field_path in operator.mutable_fields:
            current = _get_guide_path(self.guide, field_path)
            if not isinstance(current, str) or not current:
                raise RuntimeError(
                    f"TIP transform needs a non-empty string at {field_path!r}; "
                    f"got {type(current).__name__}"
                )
            patch[field_path] = tip_transform(current, scheme, depersonalise=depersonalise)
        return patch

    async def cleanup(self, record: MaterializedMutation | None) -> None:
        if record is None:
            return
        if self.cleanup_callback is None:
            cleaned = await self.target.cleanup_bound_carrier(**record.bindings)
            record.cleanup_status = "cleaned" if cleaned else "retained-no-cleanup-handler"
            return
        result = self.cleanup_callback(record)
        if inspect.isawaitable(result):
            await result
        record.cleanup_status = "cleaned"


def _get_guide_path(guide: Guide, path: str) -> Any:
    """Read an explicitly allow-listed dotted Guide field path. Mirrors the
    traversal in :func:`_set_guide_path` (list items keyed by ``id``, dict keys,
    attributes) so a transform can read the value it is about to rewrite."""
    tokens = [token for token in path.split(".") if token]
    if not tokens:
        raise ValueError("empty mutable field path")
    current: Any = guide
    for token in tokens:
        if isinstance(current, list):
            current = next((item for item in current if getattr(item, "id", None) == token), None)
            if current is None:
                raise ValueError(f"mutable field path references unknown step {token!r}")
        elif isinstance(current, dict):
            if token not in current:
                raise ValueError(f"mutable field path references unknown key {token!r}")
            current = current[token]
        else:
            if not hasattr(current, token):
                raise ValueError(f"mutable field path references unknown field {token!r}")
            current = getattr(current, token)
    return current


def _set_guide_path(guide: Guide, path: str, value: Any) -> None:
    """Set an explicitly allow-listed dotted Guide field path."""
    tokens = [token for token in path.split(".") if token]
    if not tokens:
        raise ValueError("empty mutable field path")
    current: Any = guide
    for token in tokens[:-1]:
        if isinstance(current, list):
            current = next((item for item in current if getattr(item, "id", None) == token), None)
            if current is None:
                raise ValueError(f"mutable field path references unknown step {token!r}")
        elif isinstance(current, dict):
            if token not in current:
                raise ValueError(f"mutable field path references unknown key {token!r}")
            current = current[token]
        else:
            if not hasattr(current, token):
                raise ValueError(f"mutable field path references unknown field {token!r}")
            current = getattr(current, token)
    leaf = tokens[-1]
    if isinstance(current, dict):
        if leaf not in current:
            raise ValueError(f"mutable field path references unknown key {leaf!r}")
        current[leaf] = value
    else:
        if not hasattr(current, leaf):
            raise ValueError(f"mutable field path references unknown field {leaf!r}")
        setattr(current, leaf, value)


__all__ = ["GuideMutationMaterializer", "MaterializedMutation"]
