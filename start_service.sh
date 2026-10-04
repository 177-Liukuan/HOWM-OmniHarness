#!/usr/bin/env bash
set -euo pipefail
METHOD=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if docker inspect howm-omniharness-qwen9b >/dev/null 2>&1; then
  docker inspect howm-omniharness-qwen9b --format '{{.Name}} {{.State.Status}}'
  echo 'Existing service retained. Inspect it instead of silently replacing it.'
  exit 0
fi
GPU_DEVICES=${1:?usage: start_service.sh 1,2 -- select two GPUs already allocated to this experiment}
[[ "$GPU_DEVICES" =~ ^[0-9]+,[0-9]+$ ]] || { echo 'expected two GPU indices'; exit 2; }
exec docker run -d --name howm-omniharness-qwen9b --gpus "\"device=$GPU_DEVICES\"" --ipc host \
  --entrypoint bash -v /storage/jiazhan/models/Qwen3.5-9B:/models/Qwen3.5-9B:ro \
  -v "$METHOD/config/serve.sh:/serve.sh:ro" \
  howm-codex-qwen9b:20260930-visual-causal-v8 /serve.sh
