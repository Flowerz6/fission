"""Replay-bundle ZIP packaging.

Every Finding ships with a self-contained ZIP under
``<output_dir>/replays/<finding_id>.zip`` whose contents are
sufficient to reproduce the attack against a fresh target install:

* ``manifest.json`` — finding id, run id, behaviour id, severity.
* ``abstract_document.json`` — the ``AbstractDocument`` payload.
* ``prompt.txt`` — the user prompt sent.
* ``response.txt`` — the captured AI response (text-only).
* ``response_raw.json`` — structured side data
  (tool_calls / agent rounds / hidden_techniques) when non-empty.
* ``judge_scores.json`` — list[JudgeScore] per persisted-judge.
* ``trace.json`` — the full ``AttackTrace`` dump (back-stop in case
  the schema evolves and the structured files lose a field).
* ``replay.py`` — a small self-contained replay script template.

This is the reproducibility-infrastructure deliverable from
FOUNDATIONS.md "open problems" #4.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.core.models import AttackTrace
    from advtest_ext.document import AbstractDocument


_REPLAY_SCRIPT = '''\
"""Self-contained replay for finding {finding_id}.

Reproduces the attack that produced this finding. Designed to be
run from a fresh checkout with credentials provisioned via env
vars (e.g. ANTHROPIC_API_KEY, GOOGLE_API_KEY) — the same env vars
the originating run used.

Usage:
    pip install -e ../path/to/advtest_ext  # or `poetry install`
    python replay.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from advtest_ext.core.models import AttackTrace


_HERE = Path(__file__).resolve().parent
_TRACE = AttackTrace.model_validate_json(
    (_HERE / "trace.json").read_text(encoding="utf-8")
)


async def main() -> None:
    """Re-issue the captured request against the target.

    NOTE: this template assumes you wire up the right
    ``DocumentTarget`` for ``trace.target_name``. The finding ZIP
    intentionally does not bake target credentials in — pass them
    via env vars / a YAML config (the same one the originating run
    used).
    """
    print(json.dumps({{"finding_id": {finding_id!r}, "trace_id": _TRACE.id}}))
    print("user_prompt:", _TRACE.raw_request[:120])
    print("expected_response_excerpt:", _TRACE.raw_response[:120])
    print()
    print(
        "TODO: instantiate the appropriate DocumentTarget for "
        f"{{_TRACE.target_name!r}} and call ``target.invoke(ctx, _TRACE.raw_request)``."
    )


if __name__ == "__main__":
    asyncio.run(main())
'''


def build_replay_bundle(
    *,
    finding_id: str,
    run_id: str,
    trace: AttackTrace,
    document: AbstractDocument | None,
    output_dir: Path,
    severity: str = "med",
) -> Path:
    """Materialise the replay ZIP under
    ``<output_dir>/replays/<finding_id>.zip`` and return the path.

    Idempotent: re-running with the same ``finding_id`` overwrites
    the existing ZIP rather than failing — matches the
    promote-finding "upsert" semantics on the SQL side.
    """
    replays_dir = output_dir / "replays"
    replays_dir.mkdir(parents=True, exist_ok=True)
    target = replays_dir / f"{finding_id}.zip"

    manifest: dict[str, Any] = {
        "finding_id": finding_id,
        "run_id": run_id,
        "trace_id": trace.id,
        "behavior_id": trace.behavior_id,
        "severity": severity,
        "target_name": trace.target_name,
        "verdict": trace.verdict,
        "depth": trace.depth,
        "parent_id": trace.parent_id,
    }
    judge_scores_payload = [s.model_dump(mode="json") for s in trace.judge_scores]

    # Pull a ui_mode side-panel screenshot into the bundle when one
    # exists. The ui_mode invoker stamps the absolute path onto
    # ``raw_response_data["screenshot_path"]``; copy that PNG into
    # the ZIP under a stable filename and record its presence in
    # the manifest so consumers can find it without scanning.
    screenshot_path_str = (
        trace.raw_response_data.get("screenshot_path") if trace.raw_response_data else None
    )
    screenshot_bytes: bytes | None = None
    if isinstance(screenshot_path_str, str) and screenshot_path_str:
        screenshot_src = Path(screenshot_path_str)
        if screenshot_src.exists():
            screenshot_bytes = screenshot_src.read_bytes()
            manifest["ui_mode_screenshot"] = "screenshot.png"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr(
            "abstract_document.json",
            (document.model_dump_json(indent=2) if document is not None else "{}"),
        )
        zf.writestr("prompt.txt", trace.raw_request)
        zf.writestr("response.txt", trace.raw_response)
        if trace.raw_response_data:
            zf.writestr(
                "response_raw.json",
                json.dumps(trace.raw_response_data, indent=2, default=str),
            )
        zf.writestr("judge_scores.json", json.dumps(judge_scores_payload, indent=2))
        zf.writestr("trace.json", trace.model_dump_json(indent=2))
        zf.writestr(
            "replay.py",
            _REPLAY_SCRIPT.format(finding_id=finding_id),
        )
        if screenshot_bytes is not None:
            zf.writestr("screenshot.png", screenshot_bytes)

    target.write_bytes(buffer.getvalue())
    return target


def read_replay_bundle(zip_path: Path) -> dict[str, str | bytes]:
    """Read a replay ZIP back into ``{filename: content}`` so tests
    (and a future "import a finding from somewhere else" CLI) can
    introspect without assuming the bundle is unpacked. Binary
    content stays bytes; text files are decoded as utf-8."""
    out: dict[str, str | bytes] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            data = zf.read(info)
            try:
                out[info.filename] = data.decode("utf-8")
            except UnicodeDecodeError:  # pragma: no cover - text-only bundles today
                out[info.filename] = data
    return out


__all__ = ["build_replay_bundle", "read_replay_bundle"]
