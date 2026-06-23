"""Tests for LocalVllmProvider — no real network calls; httpx mocked."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from advtest_ext.providers.base import ChatMessage, ProviderConfig
from advtest_ext.providers.local_vllm import (
    LocalVllmFrontierProvider,
    LocalVllmProvider,
)


def _config(**overrides: Any) -> ProviderConfig:
    base: dict[str, Any] = {
        "provider_type": "local_vllm",
        "endpoint": "http://localhost:8000/v1",
        "model_id": "Qwen/Qwen3-32B",
        "api_key_env": None,
        "max_tokens": 512,
        "temperature": 0.7,
        "extra_kwargs": {},
    }
    base.update(overrides)
    return ProviderConfig(**base)


def _vllm_response(
    content: str = "ok",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _mock_response(
    status: int = 200,
    json_payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = text
    if json_payload is not None:
        resp.json.return_value = json_payload
    return resp


async def test_complete_returns_text_and_usage() -> None:
    provider = LocalVllmProvider(_config())
    mock = _mock_response(json_payload=_vllm_response("hello", 12, 3))

    with patch.object(provider._client, "post", new=AsyncMock(return_value=mock)) as post:
        out = await provider.complete([ChatMessage(role="user", content="hi")])

    assert out.text == "hello"
    assert out.provider_type == "local_vllm"
    assert out.model_id == "Qwen/Qwen3-32B"
    assert out.usage.prompt_tokens == 12
    assert out.usage.completion_tokens == 3
    assert out.usage.total_tokens == 15
    assert out.usage.cost_usd == 0.0
    assert out.usage.gpu_seconds > 0  # estimated from completion tokens

    post.assert_called_once()
    sent_body = post.call_args.kwargs["json"]
    assert sent_body["model"] == "Qwen/Qwen3-32B"
    assert sent_body["messages"] == [{"role": "user", "content": "hi"}]
    # Defaults from ProviderConfig flow through.
    assert sent_body["max_tokens"] == 512
    assert sent_body["temperature"] == 0.7


async def test_overrides_pass_through_to_body() -> None:
    provider = LocalVllmProvider(_config(temperature=0.7, max_tokens=512))
    mock = _mock_response(json_payload=_vllm_response())

    with patch.object(provider._client, "post", new=AsyncMock(return_value=mock)) as post:
        await provider.complete(
            [ChatMessage(role="user", content="x")],
            temperature=0.1,
            max_tokens=64,
            seed=42,
            top_p=0.9,
        )

    body = post.call_args.kwargs["json"]
    assert body["temperature"] == 0.1
    assert body["max_tokens"] == 64
    assert body["seed"] == 42
    assert body["top_p"] == 0.9


async def test_connection_error_message_is_actionable() -> None:
    provider = LocalVllmProvider(_config())
    with patch.object(
        provider._client,
        "post",
        new=AsyncMock(side_effect=httpx.ConnectError("refused")),
    ):
        with pytest.raises(RuntimeError, match=r"start_vllm\.sh"):
            await provider.complete([ChatMessage(role="user", content="hi")])


async def test_timeout_is_surfaced_as_runtime_error() -> None:
    provider = LocalVllmProvider(_config())
    with patch.object(
        provider._client,
        "post",
        new=AsyncMock(side_effect=httpx.ReadTimeout("slow")),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            await provider.complete([ChatMessage(role="user", content="hi")])


async def test_non_200_surfaces_status_and_body() -> None:
    provider = LocalVllmProvider(_config())
    mock = _mock_response(status=500, text="internal error")

    with patch.object(provider._client, "post", new=AsyncMock(return_value=mock)):
        with pytest.raises(RuntimeError, match="500"):
            await provider.complete([ChatMessage(role="user", content="hi")])


async def test_unexpected_response_shape_surfaces_clearly() -> None:
    provider = LocalVllmProvider(_config())
    mock = _mock_response(json_payload={"unexpected": "shape"})

    with patch.object(provider._client, "post", new=AsyncMock(return_value=mock)):
        with pytest.raises(RuntimeError, match="Unexpected vLLM response"):
            await provider.complete([ChatMessage(role="user", content="hi")])


async def test_server_inference_time_header_preferred_over_estimate() -> None:
    provider = LocalVllmProvider(_config())
    mock = _mock_response(
        json_payload=_vllm_response("hi", 10, 100),
        headers={"x-inference-time": "2.5"},
    )

    with patch.object(provider._client, "post", new=AsyncMock(return_value=mock)):
        out = await provider.complete([ChatMessage(role="user", content="hi")])

    assert out.usage.gpu_seconds == 2.5


async def test_gpu_seconds_estimated_from_completion_tokens() -> None:
    """Without a server header, gpu_seconds = completion_tokens *
    DEFAULT_GPU_SECONDS_PER_OUTPUT_TOKEN. Lock the relationship so a
    careless edit to the constant gets flagged."""
    from advtest_ext.providers.local_vllm import DEFAULT_GPU_SECONDS_PER_OUTPUT_TOKEN

    provider = LocalVllmProvider(_config())
    mock = _mock_response(json_payload=_vllm_response("hi", 10, 100))

    with patch.object(provider._client, "post", new=AsyncMock(return_value=mock)):
        out = await provider.complete([ChatMessage(role="user", content="hi")])

    assert out.usage.gpu_seconds == pytest.approx(100 * DEFAULT_GPU_SECONDS_PER_OUTPUT_TOKEN)


async def test_extra_kwarg_overrides_gpu_seconds_per_token() -> None:
    """extra_kwargs.gpu_seconds_per_output_token lets a user calibrate
    the estimate per-pod."""
    provider = LocalVllmProvider(_config(extra_kwargs={"gpu_seconds_per_output_token": 0.05}))
    mock = _mock_response(json_payload=_vllm_response("hi", 10, 100))

    with patch.object(provider._client, "post", new=AsyncMock(return_value=mock)):
        out = await provider.complete([ChatMessage(role="user", content="hi")])

    assert out.usage.gpu_seconds == pytest.approx(5.0)


async def test_endpoint_defaults_when_config_omits_it() -> None:
    """ProviderConfig.endpoint is Optional; the local provider falls
    back to the canonical localhost URL rather than blowing up."""
    cfg = ProviderConfig(provider_type="local_vllm", model_id="Qwen/Qwen3-32B")
    provider = LocalVllmProvider(cfg)
    assert provider._endpoint == "http://localhost:8000/v1"


async def test_endpoint_trailing_slash_is_stripped() -> None:
    cfg = _config(endpoint="http://localhost:8000/v1/")
    provider = LocalVllmProvider(cfg)
    assert provider._endpoint == "http://localhost:8000/v1"


def test_frontier_subclass_inherits_behavior() -> None:
    cfg = _config(
        provider_type="local_vllm_frontier",
        model_id="deepseek-ai/DeepSeek-V3.2",
        endpoint="http://h200-pod:8000/v1",
    )
    provider = LocalVllmFrontierProvider(cfg)
    assert provider._model == "deepseek-ai/DeepSeek-V3.2"
    assert provider._endpoint == "http://h200-pod:8000/v1"
    assert provider.provider_type == "local_vllm_frontier"
