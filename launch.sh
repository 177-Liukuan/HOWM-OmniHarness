#!/usr/bin/env bash
set -euo pipefail
# A fresh source snapshot is required per run. Existing run resumes its snapshot.
PROJECT=/storage1/HOWM-LAB-Project
METHOD=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RUN=${1:?usage: launch.sh RUN_ID [comma-separated IDs|--all]}
IDS=${2:-001}
[[ "$RUN" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]+$ ]] || { echo 'invalid run id'; exit 2; }
OUT="$PROJECT/outputs/$RUN"
mkdir -p "$OUT"
if [[ ! -d "$OUT/source" ]]; then
  mkdir "$OUT/source"
  cp -a "$METHOD/." "$OUT/source/"
fi
ARGS=(--ids "$IDS")
if [[ "$IDS" == --all ]]; then ARGS=(--all); fi
exec docker run --rm --init --user "$(id -u):$(id -g)" -e HOME=/tmp --network container:howm-omniharness-qwen9b \
  --entrypoint /opt/vllm/bin/python \
  -v "$OUT/source:/method:ro" -v "$OUT:/work:rw" \
  -v "$PROJECT/datasets/HomeHWM/extracted/HomeHWM_preliminary_questions_5000_schema_3_1:/data:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONHASHSEED=20260929 \
  howm-codex-qwen9b:20260930-visual-causal-v8 /method/run.py "${ARGS[@]}"
