"""Materialize accepted carrier mutations before a TAP branch runs live."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from advtest_ext.guides.stage import (
    bind_setup_target_options,
    stage_guide,
    verify_setup_result,
)

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
        patch = mutation.get("artifact_patch") or {}
        if not isinstance(patch, dict) or not patch:
            return None
        allowed = {
            field
            for operator in self.guide.mutation_operators
            if operator.name == mutation.get("operator_name")
            for field in operator.mutable_fields
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
        patch = {field: "[neutral control content]" for field in fields}
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
