#!/usr/bin/env bash
# Launch a vLLM OpenAI-compatible server for advtest. Profiles:
#
#   dev   — Qwen/Qwen3-32B  on a single H200 (FP8), port 8000 (configs/dev.yaml).
#   demo  — deepseek-ai/DeepSeek-V3.2 on multi-GPU,   port 8001 (configs/demo.yaml).
#
# Reference: vLLM official OpenAI server docs
#   https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html
#
# This is a RunPod-friendly template; tune --gpu-memory-utilization,
# --max-model-len, and --tensor-parallel-size to your pod.

set -euo pipefail

PROFILE="${1:-dev}"

case "$PROFILE" in
  dev)
    MODEL="Qwen/Qwen3-32B"
    PORT=8000
    EXTRA_ARGS=(
      --quantization fp8
      --tensor-parallel-size 1
      --gpu-memory-utilization 0.92
      --max-model-len 32768
    )
    ;;
  demo)
    MODEL="deepseek-ai/DeepSeek-V3.2"
    PORT=8001
    # Multi-GPU defaults; override with TP=8 ./start_vllm.sh demo
    TP="${TP:-4}"
    EXTRA_ARGS=(
      --tensor-parallel-size "${TP}"
      --gpu-memory-utilization 0.92
      --max-model-len 65536
      --trust-remote-code
    )
    ;;
  *)
    echo "usage: $0 {dev|demo}" >&2
    exit 1
    ;;
esac

echo "Starting vLLM ($PROFILE) — model=$MODEL port=$PORT"

exec python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --served-model-name "$MODEL" \
  "${EXTRA_ARGS[@]}"
