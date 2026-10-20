"""Capture the LIVE Gemini-in-Docs side-panel DOM for selector repair.

Why: the side-panel response selector is keyed on Google's obfuscated
``appsElementsSidekick…`` classes, which drift. When they rename, the
scraper times out even though the reply rendered (see TODO_BEFORE_PITCH.md
item 4). This script plants a throwaway benign doc, opens the side panel,
asks a benign question, and — whether or not the scraper selector still
matches — writes the side-panel container's **outerHTML** to a failure
artefact so the current class/attribute hooks can be read off and the
selector updated.

It is a read-only diagnostic: the only mutation is a benign scratch doc,
which is torn down at the end.

Run it against a signed-in profile (same prerequisites as any ui_mode run)::

    poetry run python scripts/dump_side_panel_dom.py

Then share the printed ``…/failures/<trace>.txt`` path — the
``--- side panel container outerHTML ---`` section is what's needed.

Prereqs:
  1. poetry run python scripts/setup_playwright_profile.py   (sign in once)
  2. OAuth token for Docs/Drive planting (scripts/authorize_apis.py)
  3. ADVTEST_AUTH_MODE / ADVTEST_PLAYWRIGHT_PROFILE_DIR as for a live run
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path


async def _run(output_dir: Path, prompt: str) -> int:
    from advtest_ext.adapters._ui_mode_common.errors import SidePanelUnavailable
    from advtest_ext.adapters.google_docs.ui_mode.invoker import ui_mode_invoke
    from advtest_ext.corpus.docs_seeder import DocsCorpusSeeder

    seeder = DocsCorpusSeeder()
    artifact = seeder.plant(
        visible_body=(
            "Quarterly status note. Revenue grew in Q2 and customer sentiment "
            "held steady across all teams. See the appendix for the detail."
        ),
        injection="",  # benign: no hidden instruction, we only want the panel DOM
        title="Side-panel DOM probe",
    )
    trace_id = f"dom-probe-{uuid.uuid4().hex[:8]}"
    print(f"planted doc {artifact.artifact_id} ({artifact.url})")
    try:
        resp = await ui_mode_invoke(
            document_id=str(artifact.artifact_id),
            user_prompt=prompt,
            trace_id=trace_id,
            output_dir=output_dir,
        )
        # The selector still matches — great, but we still want the DOM for
        # reference. Point the operator at the screenshot; the reply is below.
        print("side panel responded; current selector still works.")
        print(f"reply (first 200 chars): {resp.text[:200]!r}")
        print("(no failure artefact written — selector is healthy)")
        return 0
    except SidePanelUnavailable as exc:
        dump = output_dir / "failures" / f"{trace_id}.txt"
        print(f"\nselector drift reproduced: {exc}")
        if dump.is_file():
            has_html = "side panel container outerHTML" in dump.read_text(encoding="utf-8")
            print(f"DOM dump written to: {dump}")
            print(f"contains container outerHTML: {has_html}")
        else:
            print(f"expected dump not found at {dump}")
        return 2
    finally:
        try:
            seeder.teardown(artifact)
            print("scratch doc torn down.")
        except Exception as exc:  # pragma: no cover - cleanup best-effort
            print(f"(teardown failed, delete {artifact.artifact_id} manually: {exc})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--output-dir",
        default="out/side_panel_dom",
        help="Where screenshots + the failure artefact land (default out/side_panel_dom).",
    )
    ap.add_argument(
        "--prompt",
        default="Summarise this document for me.",
        help="The benign user prompt to type into the side panel.",
    )
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_run(Path(args.output_dir), args.prompt)))


if __name__ == "__main__":
    main()
