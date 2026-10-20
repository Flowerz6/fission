"""Sub-step 0b — a narrow, READ-ONLY Drive share-grant accelerator.

Delivery stays UI-only (unchanged). This is an OBSERVATION accelerator: it
enumerates share/permission state ONLY for the doc ids named in operator ground
truth — not a general Drive surface, not a delivery path. When unconfigured,
``files_shared`` stays ``unavailable`` (Part B behaviour unchanged); when
configured, it flips ``files_shared`` to ``observed`` with the granted scope.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from advtest_ext.observe.model import FileShare

DriveServiceFactory = Callable[[str, str, Sequence[str], Path | None], Any]


class SharesApiAccelerator:
    """Read share grants for an allow-listed set of doc ids via the Drive API.

    Only doc ids in ``allowed_doc_ids`` (the ground-truth watched docs) are ever
    queried; anything else is ignored, so this can't become a general Drive read.
    """

    def __init__(
        self,
        *,
        allowed_doc_ids: Sequence[str],
        service: Any | None = None,
        token_cache: Path | None = None,
        service_factory: DriveServiceFactory | None = None,
    ) -> None:
        self._allowed = {str(d) for d in allowed_doc_ids}
        self._service = service
        self._token_cache = token_cache
        self._service_factory = service_factory

    def _drive(self) -> Any:
        if self._service is None:
            from advtest_ext.adapters._auth import DOCS_SCOPES, get_service

            factory = self._service_factory or (
                lambda api, ver, scopes, tc: get_service(api, ver, scopes, token_cache=tc)
            )
            self._service = factory("drive", "v3", DOCS_SCOPES, self._token_cache)
        return self._service

    def shares(self, doc_ids: Sequence[str]) -> list[FileShare]:
        """Enumerate current share grants for the requested docs ∩ the allow-list."""
        drive = self._drive()
        grants: list[FileShare] = []
        for doc_id in doc_ids:
            if str(doc_id) not in self._allowed:
                continue  # narrow: only ground-truth docs are ever read
            perms = (
                drive.permissions()
                .list(fileId=doc_id, fields="permissions(id,type,role,emailAddress,domain)")
                .execute()
            )
            for perm in perms.get("permissions", []) or []:
                principal = str(
                    perm.get("emailAddress") or perm.get("domain") or perm.get("type", "")
                )
                grants.append(
                    FileShare(
                        file_id=str(doc_id), principal=principal, scope=str(perm.get("role", ""))
                    )
                )
        return grants


__all__ = ["SharesApiAccelerator"]
