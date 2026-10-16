#!/bin/bash
# SGLang engine job — run. One inference server per Job Group engine job,
# reachable by the trainer at <job>-0.<jobgroup>:30000 (stable hostname).
# Env (all optional; defaults match this example):
#   MODEL_DIR             local checkpoint dir under /root (default Qwen3-14B)
#   ENGINE_TP             tensor-parallel size per engine  (default 1 = 1 H100)
#   SGLANG_MEM_FRACTION   static KV/mem fraction           (default 0.7)
set -ex
MODEL_DIR="${MODEL_DIR:-Qwen3-14B}"
ENGINE_TP="${ENGINE_TP:-1}"

python -m sglang.launch_server --model-path /root/${MODEL_DIR} --tp ${ENGINE_TP} \
  --host 0.0.0.0 --port 30000 --mem-fraction-static ${SGLANG_MEM_FRACTION:-0.7}
