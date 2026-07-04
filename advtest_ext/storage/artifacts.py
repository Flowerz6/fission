"""Pluggable artifact sink.

Replay bundles / screenshots / captures are built in memory and handed to an
``ArtifactStore``, which writes them somewhere durable and returns a *locator*
(a local path or a ``gs://`` URI). Ephemeral browser-farm workers don't share a
local disk, so in a deployed setup they write straight to object storage.

Default backend is **local** (a base directory), which preserves the on-disk
layout the CLI + dashboard already expect — so nothing changes for dev/CI.
Set ``ADVTEST_ARTIFACT_BACKEND=gcs`` (+ ``ADVTEST_ARTIFACT_BUCKET``) for a farm.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ArtifactStore(ABC):
    """Write-side interface for run artifacts. Read-back of *local* bundles
    stays :func:`advtest_ext.storage.replay.read_replay_bundle`; remote read is
    a later concern (fetch via the backend, then read)."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key`` (a ``/``-separated relative path) and
        return a locator string — a local filesystem path for the local
        backend, a ``gs://bucket/key`` URI for GCS."""


class LocalArtifactStore(ArtifactStore):
    """Writes under ``root/<key>`` — the pre-existing on-disk layout."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def put_bytes(self, key: str, data: bytes) -> str:
        dest = self._root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return str(dest)


class GcsArtifactStore(ArtifactStore):
    """Uploads to ``gs://<bucket>/<prefix>/<key>``. Requires the optional
    ``gcs`` extra (``google-cloud-storage``); the client is created lazily so
    importing this module never needs the dependency."""

    def __init__(self, bucket: str, prefix: str = "") -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client: Any = None  # google.cloud.storage.Client (untyped, lazy)

    def _name(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def put_bytes(self, key: str, data: bytes) -> str:
        from google.cloud import storage  # optional dep (extras: gcs)

        if self._client is None:
            self._client = storage.Client()
        name = self._name(key)
        blob = self._client.bucket(self._bucket).blob(name)
        blob.upload_from_string(data, content_type="application/octet-stream")
        return f"gs://{self._bucket}/{name}"


def artifact_store_from_env(default_root: Path | str) -> ArtifactStore:
    """Resolve the store from ``ADVTEST_ARTIFACT_BACKEND`` (``local`` | ``gcs``).

    ``local`` (default) roots at ``default_root`` — same behaviour as before.
    ``gcs`` needs ``ADVTEST_ARTIFACT_BUCKET`` (+ optional ``ADVTEST_ARTIFACT_PREFIX``).
    """
    backend = os.environ.get("ADVTEST_ARTIFACT_BACKEND", "local").lower()
    if backend == "local":
        return LocalArtifactStore(default_root)
    if backend == "gcs":
        bucket = os.environ.get("ADVTEST_ARTIFACT_BUCKET")
        if not bucket:
            raise ValueError("ADVTEST_ARTIFACT_BACKEND=gcs requires ADVTEST_ARTIFACT_BUCKET")
        return GcsArtifactStore(bucket, os.environ.get("ADVTEST_ARTIFACT_PREFIX", ""))
    raise ValueError(f"unknown ADVTEST_ARTIFACT_BACKEND {backend!r} (expected 'local' or 'gcs')")


__all__ = [
    "ArtifactStore",
    "GcsArtifactStore",
    "LocalArtifactStore",
    "artifact_store_from_env",
]
