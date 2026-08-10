#!/bin/bash
# Trainer job — run. The rollout-side orchestration around the slime launch:
#   1. wait for EVERY engine job to serve (all-or-nothing gang start),
#   2. build the engine-address list slime discovers,
#   3. warm one sandbox pool per repo image (unless SANDBOX_NO_POOL=1),
#   4. hand off to run-slime.sh (which builds + submits the slime Ray job).
#
# Scaling the inference fleet is entirely declarative: add an SGLang job to
# the YAML and add its name to SGLANG_MEMBERS below — this script is unchanged
# for 1, 2, or N engines.
#   SGLANG_MEMBERS   space-separated Job Group engine job names (default "sglang").
#                    Each is reached at <job>-0.<jobgroup>:30000.
set -ex

# Self-clean the run's sandbox pool on exit (success OR failure).
trap 'python ~/sky_workdir/code/pool_cleanup.py ~/sky_workdir/swesmith-pools.json 2>&1 || true' EXIT

# 1 + 2: health-gate every engine, then assemble the discovery address list.
SGLANG_MEMBERS="${SGLANG_MEMBERS:-sglang}"
ENGINE_ADDRS=""
for m in ${SGLANG_MEMBERS}; do
  URL="http://${m}-0.${SKYPILOT_JOBGROUP_NAME}:30000"
  healthy=0
  for i in $(seq 1 180); do curl -sf "${URL}/health" >/dev/null 2>&1 && { echo "engine ${m} healthy"; healthy=1; break; }; sleep 10; done
  [ "$healthy" = 1 ] || { echo "ERROR: engine ${m} never became healthy after 30m" >&2; exit 1; }
  ENGINE_ADDRS="${ENGINE_ADDRS}${ENGINE_ADDRS:+ }${m}-0.${SKYPILOT_JOBGROUP_NAME}:30000"
done
echo "engine addrs: ${ENGINE_ADDRS}"

# 3: one warm sandbox pool per repo image — skipped in no-pool mode, where each
# rollout instead creates an on-demand ad-hoc sandbox from its own image.
if [ "${SANDBOX_NO_POOL:-0}" != "1" ]; then
PYTHONPATH=~/sky_workdir/code python - <<'PYEOF'
import json, os
import sandbox_env
with open(os.path.expanduser('~/sky_workdir/swesmith-pools.json')) as f:
    pools = json.load(f)
reps = int(os.environ.get('SANDBOX_POOL_REPLICAS', '184'))
for p in pools:
    sandbox_env.ensure_pool(p['pool'], image=p['image'], replicas=reps)
PYEOF
else echo "SANDBOX_NO_POOL=1 -> no warm pools; using on-demand ad-hoc sandboxes"; fi

# 4: hand off to slime.
cd /root/slime
set -o pipefail
EXTERNAL_ENGINE_ADDR="${ENGINE_ADDRS}" \
PROMPT_DATA="$HOME/sky_workdir/swesmith.jsonl" \
  bash ~/sky_workdir/run-slime.sh
