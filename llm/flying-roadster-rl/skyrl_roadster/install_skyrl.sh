#!/usr/bin/env bash
# Install SkyRL at a pinned commit and drop our env in next to its examples.
set -euo pipefail
EXTRA=${SKYRL_EXTRA:-fsdp}   # fsdp, or megatron for large MoE models
python3 -m pip install -q uv
[ -d ~/SkyRL ] || git clone -q https://github.com/NovaSky-AI/SkyRL.git ~/SkyRL
cd ~/SkyRL
git fetch -q origin "$SKYRL_COMMIT" && git checkout -q "$SKYRL_COMMIT"
rm -rf examples/train/roadster && cp -r ~/sky_workdir/skyrl_roadster examples/train/roadster
uv sync -q --extra $EXTRA
uv run -q --isolated --extra $EXTRA python -c "import torch, vllm; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'vllm', vllm.__version__, 'gpu ok', torch.cuda.is_available())"
