#!/bin/bash
# Trainer job — setup. Installs the rollout deps (agent harness, sandbox SDK,
# SWE-smith grading), pre-converts the HF checkpoint to Megatron torch_dist, and
# generates the SWE-smith dataset + warm-pool manifest. Idempotent — each guarded
# step is skipped if its output already exists.
# Env (all optional; defaults match this example's Qwen3-14B):
#   MODEL_DIR / MODEL_HF_REPO / MODEL_SCRIPT   model axis (see run-slime.sh)
#   SWESMITH_REPOS / SWESMITH_PER_REPO / SWESMITH_POOL_SUFFIX   dataset subset
set -ex
MODEL_DIR="${MODEL_DIR:-Qwen3-14B}"
MODEL_HF_REPO="${MODEL_HF_REPO:-Qwen/Qwen3-14B}"
MODEL_SCRIPT="${MODEL_SCRIPT:-qwen3-14B.sh}"

pip install -q -U "huggingface_hub[cli]"
# SkyPilot Sandboxes SDK (platform feature; provides sky.sandbox used by sandbox_env.py).
# It's a standalone wheel the sandbox-enabled API server ships to the client at
# ~/.sky/bin/wheels, mounted into this pod at /wheels by the trainer's file_mounts.
# --force-reinstall --no-deps: the dev0 version can otherwise silently no-op; its
# deps are light and satisfied by the base image + the installs below.
pip install --force-reinstall --no-deps /wheels/skypilot_sandbox_sdk-*.whl
pip install -q mini-swe-agent==2.4.5 litellm pillow datasets
# Host-side grading: SWE-smith's test command + log parser run on the TRAINER
# (not in the sandbox image), so swesmith/swebench are installed here.
pip install -q swebench
pip install -q "swesmith @ git+https://github.com/SWE-bench/SWE-smith.git" || pip install -q swesmith

[ -d /root/${MODEL_DIR} ] || hf download ${MODEL_HF_REPO} --local-dir /root/${MODEL_DIR}
if [ ! -d /root/${MODEL_DIR}_torch_dist ]; then
  cd /root/slime
  source scripts/models/${MODEL_SCRIPT}
  PYTHONPATH=/root/Megatron-LM python tools/convert_hf_to_torch_dist.py \
    ${MODEL_ARGS[@]} --hf-checkpoint /root/${MODEL_DIR} --save /root/${MODEL_DIR}_torch_dist
fi

cd ~/sky_workdir
[ -f swesmith.jsonl ] || python code/dataset_swesmith.py \
  --out swesmith.jsonl --pools-out swesmith-pools.json \
  ${SWESMITH_REPOS:+--repos ${SWESMITH_REPOS}} --per-repo ${SWESMITH_PER_REPO} \
  ${SWESMITH_POOL_SUFFIX:+--pool-suffix ${SWESMITH_POOL_SUFFIX}}
