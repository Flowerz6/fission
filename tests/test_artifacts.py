"""ArtifactStore: local backend + env-driven backend selection.

The GCS backend isn't exercised against real object storage here (that needs the
optional ``gcs`` extra + credentials); we test that the env factory builds it
with the right bucket/prefix, which is the part with logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from advtest_ext.storage.artifacts import (
    GcsArtifactStore,
    LocalArtifactStore,
    artifact_store_from_env,
)


def test_local_store_writes_and_returns_locator(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    loc = store.put_bytes("replays/f1.zip", b"payload-bytes")
    assert Path(loc) == tmp_path / "replays" / "f1.zip"
    assert Path(loc).read_bytes() == b"payload-bytes"


def test_local_store_creates_nested_dirs(tmp_path: Path) -> None:
    loc = LocalArtifactStore(tmp_path).put_bytes("a/b/c/x.bin", b"x")
    assert Path(loc).exists()


def test_env_defaults_to_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ADVTEST_ARTIFACT_BACKEND", raising=False)
    assert isinstance(artifact_store_from_env(tmp_path), LocalArtifactStore)


def test_env_gcs_requires_bucket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADVTEST_ARTIFACT_BACKEND", "gcs")
    monkeypatch.delenv("ADVTEST_ARTIFACT_BUCKET", raising=False)
    with pytest.raises(ValueError, match="ADVTEST_ARTIFACT_BUCKET"):
        artifact_store_from_env(tmp_path)


def test_env_gcs_builds_store_with_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADVTEST_ARTIFACT_BACKEND", "gcs")
    monkeypatch.setenv("ADVTEST_ARTIFACT_BUCKET", "my-bucket")
    monkeypatch.setenv("ADVTEST_ARTIFACT_PREFIX", "runs/")
    store = artifact_store_from_env(tmp_path)
    assert isinstance(store, GcsArtifactStore)
    # Object-name assembly (no client / no upload needed).
    assert store._name("replays/f.zip") == "runs/replays/f.zip"


def test_env_unknown_backend_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADVTEST_ARTIFACT_BACKEND", "s3-nope")
    with pytest.raises(ValueError, match="unknown ADVTEST_ARTIFACT_BACKEND"):
        artifact_store_from_env(tmp_path)
