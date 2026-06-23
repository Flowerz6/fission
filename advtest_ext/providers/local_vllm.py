"""LocalVllmProvider: OpenAI-compatible client for a vLLM server.

Targets vLLM's OpenAI-compatible endpoint (default
``http://localhost:8000/v1``). Same shape works for Ollama, LM Studio,
or any other OpenAI-API-compatible local backend — only the endpoint
URL and ``model_id`` differ.

The frontier subclass is identical except for which config slot it
binds to in YAML (``local_vllm`` vs ``local_vllm_frontier``); behaviour
is the same.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from typing import Any

import httpx
from structlog import get_logger

from advtest_ext.providers.base import (
    ChatMessage,
    ChatResponse,
    Provider,
    ProviderConfig,
    Usage,
)

logger = get_logger(__name__)


DEFAULT_ENDPOINT = "http://localhost:8000/v1"

# Rough estimate of GPU-seconds per output token on Qwen3-32B FP8 on a
# single H200. Used for cost-ledger reporting when neither a server
# header nor a real measurement is available. Calibrate from real runs
# once you have them — this is a placeholder, not a measurement.
DEFAULT_GPU_SECONDS_PER_OUTPUT_TOKEN = 0.012


class LocalVllmProvider(Provider):
    """OpenAI-compatible chat-completions client for a local vLLM server."""

    expected_provider_type = "local_vllm"

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        endpoint = (config.endpoint or DEFAULT_ENDPOINT).rstrip("/")
        self._endpoint = endpoint
        self._model = config.model_id

        # API key is usually irrelevant for local vLLM, but the server
        # requires the header to be present. ``EMPTY`` is the conventional
        # placeholder.
        api_key = ""
        if config.api_key_env:
            api_key = os.environ.get(config.api_key_env, "") or ""
        self._headers = {
            "Authorization": f"Bearer {api_key or 'EMPTY'}",
            "Content-Type": "application/json",
        }

        # Long read timeout because long-context calls (MSJ, big
        # documents) can take a while on local hardware.
        timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
        self._client = httpx.AsyncClient(timeout=timeout)
        self._gpu_seconds_per_output_token = float(
            config.extra_kwargs.get(
                "gpu_seconds_per_output_token",
                DEFAULT_GPU_SECONDS_PER_OUTPUT_TOKEN,
            )
        )

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> ChatResponse:
        """Send a chat completion request and return a ``ChatResponse``.

        ``overrides`` may include any vLLM-supported field
        (``max_tokens``, ``temperature``, ``top_p``, ``stop``, ``seed``,
        ``response_format``, …); they're forwarded to the server as-is.
        ``tools`` is the OpenAI-format tool list (vLLM and Ollama both
        speak it natively).
        """
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": overrides.pop("max_tokens", self.config.max_tokens),
            "temperature": overrides.pop("temperature", self.config.temperature),
        }
        if tools is not None:
            body["tools"] = tools
        body.update(overrides)

        url = f"{self._endpoint}/chat/completions"
        start = time.perf_counter()

        try:
            response = await self._client.post(url, headers=self._headers, json=body)
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Could not connect to vLLM at {self._endpoint}. "
                "Is the server running? See scripts/start_vllm.sh."
            ) from e
        except httpx.TimeoutException as e:
            raise RuntimeError(
                f"vLLM at {self._endpoint} timed out after {self._client.timeout.read}s."
            ) from e

        elapsed = time.perf_counter() - start

        if response.status_code != 200:
            raise RuntimeError(f"vLLM returned {response.status_code}: {response.text[:500]}")

        data = response.json()
        try:
            message = data["choices"][0]["message"]
            usage_data = data.get("usage", {})
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected vLLM response shape: {data!r}") from e

        # When the model emits only tool_calls, content is null/missing.
        # Normalise to empty string so ChatResponse stays str-typed; the
        # full tool_calls list is preserved in `raw`.
        content = message.get("content") or ""

        prompt_tokens = int(usage_data.get("prompt_tokens", 0))
        completion_tokens = int(usage_data.get("completion_tokens", 0))
        total_tokens = int(usage_data.get("total_tokens", prompt_tokens + completion_tokens))

        # Prefer a server-reported inference time if vLLM was built with
        # the X-Inference-Time header; otherwise estimate from completion
        # tokens, falling back to wall-clock when even that is unavailable.
        server_inference_time = response.headers.get("x-inference-time")
        if server_inference_time:
            try:
                gpu_seconds = float(server_inference_time)
            except ValueError:
                gpu_seconds = elapsed
        elif completion_tokens:
            gpu_seconds = completion_tokens * self._gpu_seconds_per_output_token
        else:
            gpu_seconds = elapsed

        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=0.0,
            gpu_seconds=gpu_seconds,
        )

        logger.info(
            "local_vllm.call",
            endpoint=self._endpoint,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            wall_seconds=round(elapsed, 3),
            gpu_seconds=round(gpu_seconds, 3),
        )

        return ChatResponse(
            text=content,
            raw=data,
            usage=usage,
            provider_type=self.provider_type,
            model_id=self.model_id,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class LocalVllmFrontierProvider(LocalVllmProvider):
    """Same wire protocol as :class:`LocalVllmProvider`, separate config slot.

    Used for headline demo runs (DeepSeek V3.2 on multi-GPU). Kept as a
    distinct subclass so YAML configs and cost reports cleanly
    distinguish dev vs. demo local backends.
    """

    expected_provider_type = "local_vllm_frontier"
