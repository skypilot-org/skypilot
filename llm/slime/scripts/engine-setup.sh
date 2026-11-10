#!/bin/bash
# SGLang engine job — setup. Runs on each inference job of the Job Group.
# Just the model checkpoint: pull it once into the engine pod's local disk.
# Env (all optional; defaults match this example's Qwen3-14B):
#   MODEL_DIR       local dir under /root (default Qwen3-14B)
#   MODEL_HF_REPO   HF repo to download    (default Qwen/Qwen3-14B)
set -ex
MODEL_DIR="${MODEL_DIR:-Qwen3-14B}"
MODEL_HF_REPO="${MODEL_HF_REPO:-Qwen/Qwen3-14B}"

pip install -q -U "huggingface_hub[cli]"
[ -d /root/${MODEL_DIR} ] || hf download ${MODEL_HF_REPO} --local-dir /root/${MODEL_DIR}
