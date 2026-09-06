"""Isolation + throughput probe: N concurrent Gemini side-panel chats, ONE account.

Answers the question that gates the "run many node paths in parallel" idea
(measurement-spine throughput): can a single logged-in account drive several
independent side-panel conversations at once, in separate browser tabs, WITHOUT

  * state bleed  — tab A's chat leaking into tab B's, and
  * throttling   — the account rate-limiting / erroring under concurrency,

so that the traces we collect stay independent (a hard requirement for judge
calibration). It is a DIAGNOSTIC, not an attack: every tab sends a benign
"echo this token" prompt and we check each tab gets ITS OWN token back and
never another tab's.

How it works
------------
One ``UiModeSession`` opens Chrome once against your profile (a persistent
context). We then open ``--tabs`` pages IN THAT ONE CONTEXT — same account, same
cookies — and drive each with the real ``ui_mode_invoke`` flow (open panel →
fresh conversation → submit → read). Same account can only be one Chrome
context (the profile dir is locked), so parallelism is per-PAGE, which is
exactly the shape the tree would use.

We run once concurrently and once sequentially and compare wall-clock, so you
can see whether concurrency actually buys throughput or just trips limits.

CAUTION — live + manual. Real browser, real Gemini, real account. NOT part of
the test suite. Needs ``--extras ui_mode`` + a logged-in profile
(``ADVTEST_PLAYWRIGHT_PROFILE_DIR``). No ``GEMINI_API_KEY`` — the side panel is
the model.

Usage
-----
  poetry run python scripts/probe_parallel_panels.py --doc-id <a_blank_doc_id> --tabs 3
  # different doc per tab (cycles if fewer ids than tabs):
  poetry run python scripts/probe_parallel_panels.py --doc-ids id1,id2,id3 --tabs 3
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TabResult:
    tab: int
    marker: str
    document_id: str
    ok: bool = False  # our own marker came back
    foreign: list[str] = field(default_factory=list)  # other tabs' markers seen here
    latency_s: float = 0.0
    error: str | None = None
    response_excerpt: str = ""


def _markers(n: int) -> list[str]:
    """Unique, echo-friendly tokens — one per tab. The suffix is random so a
    stale panel from a prior run can't masquerade as this run's marker."""
    run = uuid.uuid4().hex[:6].upper()
    return [f"PROBE-{run}-T{i}" for i in range(n)]


def _prompt_for(marker: str) -> str:
    return (
        "Ignore the document content. Reply with exactly this token and nothing "
        f"else, no punctuation, no explanation: {marker}"
    )


def _doc_ids(args: argparse.Namespace, n: int) -> list[str]:
    if args.doc_ids:
        ids = [d.strip() for d in args.doc_ids.split(",") if d.strip()]
        if not ids:
            raise SystemExit("--doc-ids was empty")
        return [ids[i % len(ids)] for i in range(n)]
    if args.doc_id:
        return [args.doc_id] * n  # same doc in every tab — the harsher bleed test
    raise SystemExit("pass --doc-id <id> or --doc-ids id1,id2,...")


async def _run_one(
    *,
    context: object,
    tab: int,
    marker: str,
    document_id: str,
    out_dir: Path,
    timeout_ms: int,
) -> TabResult:
    from advtest_ext.adapters.google_docs.ui_mode.invoker import ui_mode_invoke

    res = TabResult(tab=tab, marker=marker, document_id=document_id)
    page = await context.new_page()  # type: ignore[attr-defined]
    start = time.monotonic()
    try:
        response = await ui_mode_invoke(
            document_id=document_id,
            user_prompt=_prompt_for(marker),
            trace_id=f"probe-{marker}",
            output_dir=out_dir,
            external_context=context,
            external_page=page,
            reset_conversation=True,  # each tab starts a fresh chat
            timeout_ms=timeout_ms,
        )
        res.latency_s = time.monotonic() - start
        text = response.text or ""
        res.response_excerpt = text[:120].replace("\n", " ")
        res.ok = marker in text
    except Exception as exc:  # throttle / timeout / selector drift all land here
        res.latency_s = time.monotonic() - start
        res.error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await page.close()  # type: ignore[attr-defined]
        except Exception:
            pass
    return res


def _cross_check(results: list[TabResult]) -> None:
    """Flag any tab whose response contained a DIFFERENT tab's marker — the
    signature of cross-tab state bleed."""
    for r in results:
        others = [x.marker for x in results if x.marker != r.marker]
        r.foreign = [m for m in others if m in r.response_excerpt]


async def _phase(
    *,
    context: object,
    markers: list[str],
    ids: list[str],
    out_dir: Path,
    timeout_ms: int,
    concurrent: bool,
) -> tuple[list[TabResult], float]:
    coros = [
        _run_one(
            context=context,
            tab=i,
            marker=markers[i],
            document_id=ids[i],
            out_dir=out_dir,
            timeout_ms=timeout_ms,
        )
        for i in range(len(markers))
    ]
    start = time.monotonic()
    if concurrent:
        results = list(await asyncio.gather(*coros))
    else:
        results = [await c for c in coros]
    wall = time.monotonic() - start
    _cross_check(results)
    return results, wall


def _print_phase(name: str, results: list[TabResult], wall: float) -> None:
    print(f"\n=== {name} phase — wall {wall:.1f}s ===")
    print(f"{'tab':>3} {'own?':>5} {'foreign':>8} {'lat(s)':>7}  detail")
    for r in sorted(results, key=lambda x: x.tab):
        foreign = ",".join(r.foreign) if r.foreign else "-"
        detail = r.error if r.error else r.response_excerpt
        print(
            f"{r.tab:>3} {('yes' if r.ok else 'NO'):>5} {foreign:>8} {r.latency_s:>7.1f}  {detail}"
        )


async def _run(args: argparse.Namespace) -> int:
    from advtest_ext.adapters._ui_mode_common import UiModeSession
    from advtest_ext.adapters._ui_mode_common.config import resolve_profile_dir

    n = args.tabs
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = _doc_ids(args, n)
    session = UiModeSession(profile_dir=resolve_profile_dir())
    try:
        context = await session.ensure_open()

        con_results, con_wall = await _phase(
            context=context,
            markers=_markers(n),
            ids=ids,
            out_dir=out_dir,
            timeout_ms=args.timeout_ms,
            concurrent=True,
        )
        _print_phase("CONCURRENT", con_results, con_wall)

        seq_wall = 0.0
        seq_results: list[TabResult] = []
        if not args.skip_sequential:
            seq_results, seq_wall = await _phase(
                context=context,
                markers=_markers(n),
                ids=ids,
                out_dir=out_dir,
                timeout_ms=args.timeout_ms,
                concurrent=False,
            )
            _print_phase("SEQUENTIAL", seq_results, seq_wall)
    finally:
        await session.close()

    # ---- verdict --------------------------------------------------------
    isolated = all(r.ok for r in con_results) and all(not r.foreign for r in con_results)
    errored = [r for r in con_results if r.error]
    print("\n=== VERDICT ===")
    print(
        f"isolation (each tab got its own token, no foreign tokens): {'PASS' if isolated else 'FAIL'}"
    )
    if errored:
        print(f"errors under concurrency: {len(errored)}/{n} — possible throttling/limits")
    else:
        print(f"errors under concurrency: 0/{n}")
    if seq_results and seq_wall > 0:
        speedup = seq_wall / con_wall if con_wall else 0.0
        print(
            f"throughput: concurrent {con_wall:.1f}s vs sequential {seq_wall:.1f}s → {speedup:.1f}x"
        )
        print(
            "  (near 1x means the account serialised the work — parallel tabs "
            "won't help; well above 1x means real concurrency)"
        )
    if not isolated:
        print(
            "\n-> same-account parallel tabs are NOT safe for measurement: state "
            "bled or tokens were lost. Prefer multiple accounts (the account pool) "
            "for concurrency, or keep runs serial."
        )
    return 0 if isolated and not errored else 1


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    src = p.add_mutually_exclusive_group()
    src.add_argument("--doc-id", help="one Doc id used in EVERY tab (harsher bleed test)")
    src.add_argument("--doc-ids", help="comma-separated Doc ids, one per tab (cycled)")
    p.add_argument("--tabs", type=int, default=3, help="concurrent tabs (default 3)")
    p.add_argument(
        "--timeout-ms", type=int, default=30000, help="per-step wait, ms (default 30000)"
    )
    p.add_argument("--output-dir", default="out/probe", help="screenshots + artifacts")
    p.add_argument(
        "--skip-sequential",
        action="store_true",
        help="only run the concurrent phase (no throughput comparison)",
    )
    return p.parse_args()


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
