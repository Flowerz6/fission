"""Unit tests for the Workspace Gemini traffic interceptor.

No real Playwright in these tests — we drive ``TrafficInterceptor``
with hand-rolled Fake Page / Request / Response doubles so the
test suite runs without Chromium and without network.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from advtest_ext.adapters._ui_mode_common.interception import (
    WORKSPACE_GEMINI_URL_PATTERNS,
    TrafficInterceptor,
)

# ----------------------------------------------------------------------
# Test doubles — minimal stand-ins for Playwright Page / Request /
# Response. Each exposes only the surface the interceptor touches.
# ----------------------------------------------------------------------


class FakeRequest:
    """Minimal stand-in for ``playwright.async_api.Request``."""

    def __init__(
        self,
        method: str = "POST",
        post_data: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.method = method
        self.post_data = post_data
        self._headers = headers or {}

    async def all_headers(self) -> dict[str, str]:
        return dict(self._headers)


class FakeResponse:
    """Minimal stand-in for ``playwright.async_api.Response``."""

    def __init__(
        self,
        url: str,
        status: int,
        request: FakeRequest,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.status = status
        self.request = request
        self._body = body
        self._headers = headers or {"content-type": "application/json"}

    async def text(self) -> str:
        return self._body

    async def all_headers(self) -> dict[str, str]:
        return dict(self._headers)


class FakePage:
    """Playwright-ish Page that records listeners and can fire events."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        if event in self._handlers and handler in self._handlers[event]:
            self._handlers[event].remove(handler)

    def listener_count(self, event: str) -> int:
        return len(self._handlers.get(event, []))

    async def fire_response(self, response: FakeResponse) -> None:
        """Synchronously invoke every response listener, then yield so
        the interceptor's scheduled capture task can run."""
        for handler in list(self._handlers.get("response", [])):
            handler(response)
        # Let the scheduled asyncio.create_task drain.
        await asyncio.sleep(0)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _gemini_url(suffix: str = "streamGenerate") -> str:
    return f"https://appsgenaiserver-pa.clients6.google.com/v1/genai/{suffix}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_captures_matching_url(tmp_path: Path) -> None:
    page = FakePage()
    out = tmp_path / "capture.jsonl"
    req = FakeRequest(method="POST", post_data='{"prompt": "hi"}')
    resp = FakeResponse(_gemini_url(), 200, req, body='{"response": "hello"}')

    async with TrafficInterceptor(page, out):
        await page.fire_response(resp)

    entries = _read_jsonl(out)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["url"] == resp.url
    assert entry["status"] == 200
    assert entry["method"] == "POST"
    assert entry["request_body"] == '{"prompt": "hi"}'
    assert entry["response_body"] == '{"response": "hello"}'
    assert entry["seq"] == 1


@pytest.mark.asyncio
async def test_skips_non_matching_url(tmp_path: Path) -> None:
    page = FakePage()
    out = tmp_path / "capture.jsonl"
    req = FakeRequest(method="GET")
    resp = FakeResponse("https://other.example.com/foo", 200, req, body="not interesting")

    async with TrafficInterceptor(page, out):
        await page.fire_response(resp)

    entries = _read_jsonl(out)
    assert entries == []


@pytest.mark.asyncio
async def test_redacts_auth_headers(tmp_path: Path) -> None:
    page = FakePage()
    out = tmp_path / "capture.jsonl"
    req = FakeRequest(
        method="POST",
        post_data="{}",
        headers={
            "content-type": "application/json",
            "authorization": "Bearer SECRET-TOKEN",
            "cookie": "SAPISID=abc; SID=xyz",
        },
    )
    resp = FakeResponse(
        _gemini_url(),
        200,
        req,
        body="{}",
        headers={
            "content-type": "application/json",
            "set-cookie": "SAPISID=new; Secure",
        },
    )

    async with TrafficInterceptor(page, out):
        await page.fire_response(resp)

    entries = _read_jsonl(out)
    assert len(entries) == 1
    request_header_keys = {k.lower() for k in entries[0]["request_headers"]}
    response_header_keys = {k.lower() for k in entries[0]["response_headers"]}
    assert "authorization" not in request_header_keys
    assert "cookie" not in request_header_keys
    assert "set-cookie" not in response_header_keys
    # Safe headers should still come through.
    assert "content-type" in request_header_keys
    assert "content-type" in response_header_keys


@pytest.mark.asyncio
async def test_sequence_increments(tmp_path: Path) -> None:
    page = FakePage()
    out = tmp_path / "capture.jsonl"

    async with TrafficInterceptor(page, out):
        for i in range(3):
            req = FakeRequest()
            resp = FakeResponse(_gemini_url(), 200, req, body=f'{{"i": {i}}}')
            await page.fire_response(resp)

    entries = _read_jsonl(out)
    assert [e["seq"] for e in entries] == [1, 2, 3]


@pytest.mark.asyncio
async def test_handles_response_body_error(tmp_path: Path) -> None:
    """A response whose body read raises should drop silently — the
    interceptor must not propagate Playwright's transient errors."""
    page = FakePage()
    out = tmp_path / "capture.jsonl"

    class ErrorResponse(FakeResponse):
        async def text(self) -> str:
            raise RuntimeError("page closed mid-read")

    req = FakeRequest()
    resp = ErrorResponse(_gemini_url(), 200, req)

    async with TrafficInterceptor(page, out):
        await page.fire_response(resp)

    # Failed read → empty body, but the entry should still land
    # so we have a record that a matching response fired.
    entries = _read_jsonl(out)
    assert len(entries) == 1
    assert entries[0]["response_body"] == ""


@pytest.mark.asyncio
async def test_detach_removes_listener(tmp_path: Path) -> None:
    """After exit, the listener is removed — no accumulation across
    re-entries on the same Page."""
    page = FakePage()
    out = tmp_path / "capture.jsonl"

    assert page.listener_count("response") == 0
    async with TrafficInterceptor(page, out):
        assert page.listener_count("response") == 1
    assert page.listener_count("response") == 0

    # Re-entry should not double up.
    async with TrafficInterceptor(page, out):
        assert page.listener_count("response") == 1
    assert page.listener_count("response") == 0


@pytest.mark.asyncio
async def test_empty_capture_still_flushes_file(tmp_path: Path) -> None:
    """Zero matching responses → file exists (empty) so callers can
    rely on the path being a real file."""
    page = FakePage()
    out = tmp_path / "capture.jsonl"

    async with TrafficInterceptor(page, out):
        pass

    assert out.exists()
    assert out.read_text() == ""


@pytest.mark.asyncio
async def test_creates_output_directory(tmp_path: Path) -> None:
    page = FakePage()
    out = tmp_path / "deeply" / "nested" / "capture.jsonl"

    async with TrafficInterceptor(page, out):
        pass

    assert out.exists()


def test_default_patterns_cover_known_endpoints() -> None:
    """The shipped default patterns should match the two endpoints
    documented in the Google Red Team Report."""
    interceptor = TrafficInterceptor(page=None, output_path=Path("/tmp/x"))
    assert interceptor._url_matches(_gemini_url("streamGenerate"))
    assert interceptor._url_matches(_gemini_url("quotaSummary"))
    assert not interceptor._url_matches("https://docs.google.com/something")


def test_default_patterns_are_the_documented_ones() -> None:
    """Guard against accidentally widening or narrowing the default
    URL match set without a test update."""
    assert WORKSPACE_GEMINI_URL_PATTERNS == ("appsgenaiserver-pa.clients6.google.com/v1/genai/",)
