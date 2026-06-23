"""Workspace Gemini API traffic interception.

Wraps Playwright's response-observation API to capture
``appsgenaiserver-pa.clients6.google.com/v1/genai/...`` traffic
alongside UI driving. The captured stream is the same data the
Google Red Team Report's intern collected with Burp Suite — a
first-class signal channel for the V3-style reasoning leaks
that surface in the raw stream but not in the rendered
side-panel response.

Observe-only by design. The interceptor uses
``page.on("response", ...)`` rather than ``page.route()``,
so it never modifies requests, never blocks responses, and
cannot break the page it's attached to. Worst case is a
dropped capture; the page keeps working.

Usage::

    async with TrafficInterceptor(page, output_path) as interceptor:
        await page.goto(...)
        # ... drive UI ...
    # listener is detached and the JSONL file is flushed on exit

Each captured response becomes one JSONL line at ``output_path``,
fields::

    {
      "seq": int,
      "ts": ISO-8601 UTC,
      "url": str,
      "method": str,
      "status": int,
      "request_headers": {safe-allowlist},
      "request_body": str,
      "response_headers": {safe-allowlist},
      "response_body": str
    }

Headers are filtered through a safe-allowlist so the captured
file is shareable without leaking the operator's session
(Authorization tokens, cookies, etc. are stripped).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

# URL substring patterns. A response URL matches if any pattern
# is a substring of the URL. Workspace Gemini's production
# endpoints all live under a single path prefix, so one pattern
# covers ``streamGenerate``, ``quotaSummary``, and any
# sibling endpoints. Extend this list as new endpoints surface.
WORKSPACE_GEMINI_URL_PATTERNS: tuple[str, ...] = (
    "appsgenaiserver-pa.clients6.google.com/v1/genai/",
)


# Headers safe to retain in the captured file. Lowercase for
# case-insensitive matching. Anything not in this set is stripped
# at capture time — auth tokens, session cookies, fingerprinting
# headers all drop out so the file is safe to share.
_SAFE_HEADERS: frozenset[str] = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "user-agent",
        "accept",
        "accept-language",
        "accept-encoding",
        "x-client-data",
    }
)


class TrafficInterceptor:
    """Observe-only network capture for Workspace Gemini traffic.

    Attaches to a Playwright ``Page`` via the response-observation
    API, captures responses whose URLs match one of
    :data:`WORKSPACE_GEMINI_URL_PATTERNS`, and appends one JSONL
    entry per response to ``output_path``.

    Designed as an async context manager so the listener is
    always detached and the file is always flushed, even on
    exceptions inside the wrapped block. Reusable across many
    runs on the same page: the listener reference is held
    internally so detach removes exactly the handler that was
    added (never accumulates handlers across re-entries).
    """

    def __init__(
        self,
        page: Any,
        output_path: Path,
        *,
        url_patterns: Iterable[str] = WORKSPACE_GEMINI_URL_PATTERNS,
        safe_headers: Iterable[str] = _SAFE_HEADERS,
    ) -> None:
        self._page = page
        self._output_path = Path(output_path)
        self._url_patterns: tuple[str, ...] = tuple(url_patterns)
        self._safe_headers: frozenset[str] = frozenset(h.lower() for h in safe_headers)
        self._sequence = 0
        self._pending: set[asyncio.Task[None]] = set()
        self._fh: Any = None
        self._listener: Any = None

    # ------------------------------------------------------------------
    # Async context manager surface
    # ------------------------------------------------------------------

    async def __aenter__(self) -> TrafficInterceptor:
        await self._attach()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._detach()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _attach(self) -> None:
        """Open the output file and register the response listener.

        The listener is a sync wrapper around ``_capture_response``
        — Playwright's ``page.on`` expects a sync callable. The
        wrapper schedules the actual capture as a background
        ``asyncio.Task`` so we don't block the page's event loop
        while awaiting response bodies.
        """
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._output_path.open("a", encoding="utf-8")

        def _listener(response: Any) -> None:
            if not self._url_matches(response.url):
                return
            task = asyncio.create_task(self._capture_response(response))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

        self._listener = _listener
        self._page.on("response", self._listener)

    async def _detach(self) -> None:
        """Remove the listener, drain in-flight captures, close the file.

        Drained tasks may raise (page closed mid-capture is
        routine); we use ``return_exceptions=True`` so one
        broken capture can't keep the file open. The file is
        always closed before this method returns.
        """
        if self._listener is not None:
            try:
                self._page.remove_listener("response", self._listener)
            except Exception:
                pass
            self._listener = None

        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
            self._pending.clear()

        if self._fh is not None:
            try:
                self._fh.flush()
            finally:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def _url_matches(self, url: str) -> bool:
        """True if any configured pattern is a substring of ``url``."""
        return any(p in url for p in self._url_patterns)

    async def _capture_response(self, response: Any) -> None:
        """Build one JSONL entry from a response and append it.

        Best-effort: any exception is swallowed. A response that
        was aborted mid-flight or a page that closed during
        navigation are routine and must not propagate to the
        page event loop.
        """
        try:
            entry = await self._build_entry(response)
        except Exception:
            return
        self._write_entry(entry)

    async def _build_entry(self, response: Any) -> dict[str, Any]:
        """Materialise a JSON-serialisable entry from a response."""
        request = response.request

        response_body = await _safe_response_text(response)
        request_body = _safe_request_body(request)
        response_headers = await _safe_response_headers(response)
        request_headers = await _safe_request_headers(request)

        self._sequence += 1
        return {
            "seq": self._sequence,
            "ts": _now_iso(),
            "url": response.url,
            "method": request.method,
            "status": response.status,
            "request_headers": self._redact_headers(request_headers),
            "request_body": request_body,
            "response_headers": self._redact_headers(response_headers),
            "response_body": response_body,
        }

    def _redact_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Return only headers in the safe allowlist (case-insensitive)."""
        return {k: v for k, v in headers.items() if k.lower() in self._safe_headers}

    def _write_entry(self, entry: dict[str, Any]) -> None:
        """Append one JSONL line. No per-write flush — the OS buffer
        is fine and the file is explicitly flushed in ``_detach``."""
        if self._fh is None:
            return
        try:
            line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
            self._fh.write(line + "\n")
        except Exception:
            pass


# ----------------------------------------------------------------------
# Defensive helpers — each isolates one Playwright-call failure mode so
# the capture path is exception-free in normal use.
# ----------------------------------------------------------------------


async def _safe_response_text(response: Any) -> str:
    """Read response body as text. Returns empty string on any error."""
    try:
        text: str = await response.text()
        return text
    except Exception:
        return ""


def _safe_request_body(request: Any) -> str:
    """Pull request body without raising. ``post_data`` is None for GETs."""
    try:
        return request.post_data or ""
    except Exception:
        return ""


async def _safe_response_headers(response: Any) -> dict[str, str]:
    """Pull headers off a Response without raising."""
    try:
        return dict(await response.all_headers())
    except Exception:
        try:
            return dict(response.headers)
        except Exception:
            return {}


async def _safe_request_headers(request: Any) -> dict[str, str]:
    """Pull headers off a Request without raising."""
    try:
        return dict(await request.all_headers())
    except Exception:
        try:
            return dict(request.headers)
        except Exception:
            return {}


def _now_iso() -> str:
    """UTC now in ISO-8601 with timezone suffix."""
    return datetime.now(UTC).isoformat()
