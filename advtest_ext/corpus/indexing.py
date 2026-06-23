"""Wait for planted corpus to become retrievable.

Content written through the Gmail / Drive APIs is not instantly
visible to Gemini's grounding — there is an indexing lag between
"the API call returned 200" and "a retrieval query can find it".
Fire the retrieval trigger too early and you get a false negative
that looks like "the injection didn't fire" when really "the poison
wasn't indexed yet".

:func:`wait_until_retrievable` polls a caller-supplied predicate
with linear backoff until it returns ``True`` or the attempt budget
is exhausted. The predicate is injected (and so is the sleeper) so
this is pure, fast-to-test logic with no API or clock dependency.

Caveat to record in any run: the best available *programmatic*
proxy for "Gemini can ground on it" is "Gmail/Drive search can find
it" (:func:`gmail_search_probe` / :func:`drive_search_probe`).
Search-index readiness and grounding-index readiness are correlated
but not identical — treat a satisfied probe as necessary, not
sufficient, and let the actual retrieval trigger be the ground truth.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

# An async predicate: "is the corpus retrievable yet?"
Probe = Callable[[], Awaitable[bool]]
Sleeper = Callable[[float], Awaitable[None]]


async def wait_until_retrievable(
    probe: Probe,
    *,
    max_attempts: int = 10,
    base_delay_s: float = 2.0,
    max_delay_s: float = 30.0,
    sleeper: Sleeper = asyncio.sleep,
) -> bool:
    """Poll ``probe`` until it returns ``True`` or attempts run out.

    Returns ``True`` as soon as the probe is satisfied, ``False`` if
    every attempt failed. Backoff is linear (``base_delay_s * n``)
    capped at ``max_delay_s``; no sleep is taken after the final
    attempt. ``sleeper`` is injectable so tests run instantly.

    The probe is called up to ``max_attempts`` times. A probe that
    raises is treated as "not ready yet" (swallowed) — a transient
    404/permission blip during indexing shouldn't abort the wait.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    for attempt in range(1, max_attempts + 1):
        try:
            if await probe():
                return True
        except Exception:
            # Indexing-window transients (not-found, eventual-
            # consistency errors) count as "not ready".
            pass
        if attempt < max_attempts:
            await sleeper(min(base_delay_s * attempt, max_delay_s))
    return False


def gmail_search_probe(service: Any, query: str) -> Probe:
    """Build a probe that succeeds once a Gmail search for ``query``
    returns at least one message.

    ``service`` is a Gmail ``Resource``; the blocking
    ``messages.list`` call is pushed to a thread so the poll loop
    stays async. Use the artifact's ``unique_entity`` as ``query``.
    """

    async def _probe() -> bool:
        def _list() -> Any:
            return service.users().messages().list(userId="me", q=query, maxResults=1).execute()

        result = await asyncio.to_thread(_list)
        return bool(result.get("messages"))

    return _probe


def drive_search_probe(service: Any, query: str) -> Probe:
    """Build a probe that succeeds once a Drive ``fullText`` search
    for ``query`` returns at least one file.

    ``service`` is a Drive v3 ``Resource``. Note ``drive.file`` scope
    only sees app-created files — which is exactly the planted doc,
    so this works under the seeder's scope.
    """

    async def _probe() -> bool:
        def _list() -> Any:
            return (
                service.files()
                .list(
                    q=f"fullText contains '{query}'",
                    fields="files(id)",
                    pageSize=1,
                )
                .execute()
            )

        result = await asyncio.to_thread(_list)
        return bool(result.get("files"))

    return _probe


__all__ = [
    "Probe",
    "Sleeper",
    "drive_search_probe",
    "gmail_search_probe",
    "wait_until_retrievable",
]
