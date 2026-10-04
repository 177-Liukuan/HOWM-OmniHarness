#!/usr/bin/env bash
set -euo pipefail
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTHONHASHSEED=20260929
export VLLM_NO_USAGE_STATS=1
export VLLM_USE_FLASHINFER_SAMPLER=0
export DO_NOT_TRACK=1
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export CUBLAS_WORKSPACE_CONFIG=:4096:8
exec /opt/vllm/bin/python -m vllm.entrypoints.cli.main serve /models/Qwen3.5-9B \
  --host 127.0.0.1 --port 8000 --served-model-name howm-qwen35-9b \
  --dtype bfloat16 --enforce-eager --tensor-parallel-size 2 --max-model-len 131072 \
  --max-num-seqs 8 --max-num-batched-tokens 8192 --gpu-memory-utilization 0.85 \
  --seed 20260929 --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --default-chat-template-kwargs '{"enable_thinking":false}' \
  --generation-config vllm \
  --override-generation-config '{"temperature":0.0,"top_p":1.0,"top_k":-1,"max_new_tokens":8192}' \
  --limit-mm-per-prompt '{"image":64,"video":0}' --skip-mm-profiling
