#!/bin/bash
# slime launcher for the coding-agent RL loop. Builds the train_async.py / train.py
# argument set from env vars and submits it as a Ray job to the trainer's local Ray
# cluster. Everything is env-driven so the Job Group YAML is the single source of
# config; the notable knobs:
#
#   Rollout      --custom-generate-function-path generate.generate  (agentic loop;
#                code/ is on PYTHONPATH via the Ray runtime env below). Reward comes
#                from the sandbox eval inside generate() — no reward model.
#   Metrics      --custom-rollout-log-function-path rollout_metrics.log_rollout_metrics
#                (pass@k + sandbox timing; wandb is opt-in via WANDB_API_KEY).
#   Sizing       ROLLOUT_BATCH_SIZE x N_SAMPLES_PER_PROMPT = concurrent rollouts =
#                concurrent sandbox claims; keep SANDBOX_POOL_REPLICAS >= that product.
#   Topology     TRAIN_SCRIPT=train_async.py  -> async rollout/train overlap (default).
#                WEIGHT_TRANSPORT=disk|nccl   -> weight-sync carrier (default disk).
#                WEIGHT_MODE=delta|full       -> ship changed bytes only, or all.
#
# The rollout-actor env (sandbox SDK auth, AGENT_*/SANDBOX_* knobs) is passed through
# the Ray job runtime env — Ray actors don't reliably inherit the shell env.

set -ex

# will prevent ray from buffering stdout/stderr
export PYTHONUNBUFFERED=1

NVLINK_COUNT=$(nvidia-smi topo -m 2>/dev/null | grep -o 'NV[0-9][0-9]*' | wc -l)
if [ "$NVLINK_COUNT" -gt 0 ]; then
    HAS_NVLINK=1
else
    HAS_NVLINK=0
fi
echo "HAS_NVLINK: $HAS_NVLINK (detected $NVLINK_COUNT NVLink references)"

if command -v nvidia-smi >/dev/null 2>&1; then
    DETECTED_GPUS=$(nvidia-smi -L 2>/dev/null | wc -l | tr -d ' ')
else
    DETECTED_GPUS=0
fi
NUM_GPUS=${NUM_GPUS:-${DETECTED_GPUS}}
if [ -z "$NUM_GPUS" ] || [ "$NUM_GPUS" -le 0 ]; then
    NUM_GPUS=8
fi
echo "NUM_GPUS: $NUM_GPUS"

# Model axis. Defaults match this example (Qwen3-14B); override via the trainer
# YAML env to train a different model.
#   MODEL_SCRIPT: slime arch args (scripts/models/*.sh)
#   MODEL_DIR:    checkpoint dir under /root (hf download target in the YAML)
#   TP:           tensor-model-parallel size (= trainer GPU count; 14B -> 4)
MODEL_SCRIPT="${MODEL_SCRIPT:-qwen3-14B.sh}"
MODEL_DIR="${MODEL_DIR:-Qwen3-14B}"
TP="${TP:-4}"

# Topology: SLIME_MODE=async (default; overlap rollout with training) | sync
# (serialize). Maps to slime's train_async.py / train.py.
SLIME_MODE="${SLIME_MODE:-async}"
TRAIN_SCRIPT=$([ "$SLIME_MODE" = "sync" ] && echo train.py || echo train_async.py)

# Vendored script lives outside the slime tree; point at the in-image copy.
SLIME_DIR="${SLIME_DIR:-/root/slime}"
source "${SLIME_DIR}/scripts/models/${MODEL_SCRIPT}"

# Our rollout code (generate.py, sandbox_env.py) synced via workdir.
AGENT_CODE_DIR="${AGENT_CODE_DIR:-$HOME/sky_workdir/code}"
PROMPT_DATA="${PROMPT_DATA:-$HOME/sky_workdir/mbppplus.jsonl}"

CKPT_ARGS=(
   --hf-checkpoint /root/${MODEL_DIR}
   --ref-load /root/${MODEL_DIR}_torch_dist
   --load /root/${MODEL_DIR}_slime/
   --save /root/${MODEL_DIR}_slime/
   --save-interval ${SAVE_INTERVAL:-5}
)

# ROLLOUT_SHUFFLE=0 -> deterministic (same instances every step) for clean
# benchmark timing; default on (real training wants variety). Empty-array idiom
# so "off" contributes zero args (no empty token to argparse).
SHUFFLE_ARG=(); [ "${ROLLOUT_SHUFFLE:-1}" != "0" ] && SHUFFLE_ARG=(--rollout-shuffle)
ROLLOUT_ARGS=(
   --prompt-data ${PROMPT_DATA}
   --input-key text
   --label-key label
   --metadata-key metadata
   # NO --apply-chat-template: generate.py's harness renders its own messages
   "${SHUFFLE_ARG[@]}"
   --num-rollout ${NUM_ROLLOUT:-3}
   --rollout-batch-size ${ROLLOUT_BATCH_SIZE:-4}
   --n-samples-per-prompt ${N_SAMPLES_PER_PROMPT:-4}
   # Per-turn generation cap (feeds sampling_params -> adapter session
   # defaults); MBPP solutions are short, 2048 bounds a runaway turn.
   --rollout-max-response-len ${ROLLOUT_MAX_RESPONSE_LEN:-2048}
   # Whole-session context cap enforced by the adapter (finish_reason=length
   # past this).
   --rollout-max-context-len ${ROLLOUT_MAX_CONTEXT_LEN:-8192}
   --rollout-temperature ${ROLLOUT_TEMPERATURE:-1}

   # Global (training) batch = train on exactly what we generated this step, so
   # it's DERIVED from the two knobs above (not a separate config var).
   --global-batch-size $(( ${ROLLOUT_BATCH_SIZE:-4} * ${N_SAMPLES_PER_PROMPT:-4} ))
   --balance-data

   --custom-generate-function-path generate.generate
   # Adds agent/pass@1, agent/pass@k, agent/sandbox_*_sec/* to the metrics
   # slime already logs; returns falsy so slime's default logging still runs.
   --custom-rollout-log-function-path rollout_metrics.log_rollout_metrics
)

PERF_ARGS=(
   --tensor-model-parallel-size ${TP:-2}
   --sequence-parallel
   --pipeline-model-parallel-size 1
   --context-parallel-size 1
   --expert-model-parallel-size 1
   --expert-tensor-parallel-size 1

   --recompute-granularity full
   --recompute-method uniform
   --recompute-num-layers 1

   --use-dynamic-batch-size
   --max-tokens-per-gpu ${MAX_TOKENS_PER_GPU:-9216}
)

GRPO_ARGS=(
   --advantage-estimator grpo
   --use-kl-loss
   --kl-loss-coef 0.00
   --kl-loss-type low_var_kl
   --entropy-coef 0.00
   --eps-clip 0.2
   --eps-clip-high 0.28
)

OPTIMIZER_ARGS=(
   --optimizer adam
   --lr 1e-6
   --lr-decay-style constant
   --weight-decay 0.1
   --adam-beta1 0.9
   --adam-beta2 0.98
)

# wandb is OPT-IN via WANDB_API_KEY: unset => empty WANDB_ARGS => run stays
# fully disabled (never breaks a run for a missing key). slime derives the
# run name from --wandb-group (no --wandb-run-name arg exists; wandb_utils.py
# uses group as the name), so we encode the config in the group.
WANDB_ARGS=()
# SECURITY: this test EXPANDS WANDB_API_KEY, so `set -x` (on since the top of the
# script) would echo the key into the job log. Trace off around just the test;
# WANDB_ARGS itself holds only project/group/team, so re-enable right after.
set +x
if [ -n "${WANDB_API_KEY:-}" ]; then
   WANDB_ARGS=(
      --use-wandb
      --wandb-project "${WANDB_PROJECT:-skypilot-rl-workloads}"
      --wandb-group "${WANDB_GROUP:-coding-agent}"
      ${WANDB_TEAM:+--wandb-team "${WANDB_TEAM}"}
   )
fi
set -x

SGLANG_ARGS=(
   # per-engine GPU count (= each external sglang job's TP). Multi-engine
   # scale-out passes several addrs in EXTERNAL_ENGINE_ADDR; slime auto-detects
   # each engine's GPU count at discovery, but keep this consistent.
   --rollout-num-gpus-per-engine ${ROLLOUT_GPUS_PER_ENGINE:-2}
   --sglang-mem-fraction-static 0.7
)

MISC_ARGS=(
   # default dropout in megatron is 0.1
   --attention-dropout 0.0
   --hidden-dropout 0.0
   # should be good for model performance
   --accumulate-allreduce-grads-in-fp32
   --attention-softmax-in-fp32
   # need to comment this when using model with MLA
   --attention-backend flash
)

# --- Topology / weight-transport hook ---------------------------------------
# COLOCATE=1        -> single-job IPC (no external engine, no disk); actor.py
#                      forces UpdateWeightFromTensor when --colocate (actor.py:139-143).
# WEIGHT_TRANSPORT  -> disaggregated carrier: disk (default, proven 3b) | nccl.
#   nccl: slime opens a cross-job NCCL group (trainer rank0 + engine GPUs;
#   update_weight_from_distributed.py:293-321) — the external job dials the
#   TRAINER pod back on a random port. Drops the --update-weight-disk-* args.
# WEIGHT_MODE = full | delta (arguments.py:137). 'delta' bytewise-diffs weights
# vs a pinned-CPU snapshot of the last broadcast and ships only changed
# positions+values -- LOSSLESS apply (no arithmetic), so reward should overlay
# full; the payoff is bandwidth/latency (perf/update_weights_time), config-
# dependent (fewer changed bytes at low LR / bigger win at scale). Delta is
# NOT supported with --colocate and only rides nccl|disk (arguments.py:1734-1742).
WEIGHT_MODE="${WEIGHT_MODE:-full}"
if [ "${COLOCATE:-0}" = "1" ]; then
   if [ "$WEIGHT_MODE" = "delta" ]; then echo "WARN: delta unsupported with --colocate; forcing full"; fi
   TOPO_ARGS=( --colocate --update-weight-mode full )
   echo "TOPOLOGY: colocated (IPC / update_weights_from_tensor)"
else
   TOPO_ARGS=( --rollout-external-engine-addrs ${EXTERNAL_ENGINE_ADDR} --update-weight-mode ${WEIGHT_MODE} )
   if [ "$WEIGHT_MODE" = "delta" ]; then
      # delta REQUIRES a rollout-host-local (NVMe) checkpoint dir: each engine host
      # pulls the published full/delta from --update-weight-disk-dir into here and
      # reloads from it (arguments.py:224, engine.pull_weights). Container-local on
      # the engine pod (per-host, NOT the shared volume). arguments.py:2016 enforces it.
      TOPO_ARGS+=( --update-weight-encoding ${WEIGHT_ENCODING:-indices}
                   --update-weight-local-checkpoint-dir ${WEIGHT_LOCAL_CKPT_DIR:-/root/slime-local-ckpt} )
   fi
   if [ "${WEIGHT_TRANSPORT:-disk}" = "nccl" ]; then
      TOPO_ARGS+=( --update-weight-transport nccl )
      echo "TOPOLOGY: disaggregated, mode=${WEIGHT_MODE}, transport=nccl (cross-job NCCL group)"
   else
      TOPO_ARGS+=( --update-weight-transport disk --update-weight-disk-dir /shared/policy --update-weight-disk-keep-files )
      echo "TOPOLOGY: disaggregated, mode=${WEIGHT_MODE}, transport=disk"
   fi
fi

# launch the master node of ray in container
export MASTER_ADDR=${MASTER_ADDR:-"127.0.0.1"}
# Idempotent: a prior run's Ray head survives job failure (daemonized).
# Probe slime's dashboard (8265; SkyPilot's own runtime uses 8266) and reuse.
if curl -sf http://127.0.0.1:8265/api/version >/dev/null 2>&1; then
  echo "Ray head already running on 8265; reusing."
else
  ray start --head --node-ip-address ${MASTER_ADDR} --num-gpus ${NUM_GPUS} --disable-usage-stats --dashboard-host=0.0.0.0 --dashboard-port=8265 --dashboard-agent-listen-port 52366 --metrics-export-port 8091
fi

# Runtime env for the ray job: PYTHONPATH gets Megatron + our rollout code
# (so load_function can import "generate.generate"); the AGENT_*/sandbox-SDK
# vars must be here because the RolloutManager actor (where generate() runs)
# gets its env from the ray runtime env, not from this shell.
# NCCL_DEBUG=INFO on the nccl variant so the engine/trainer logs print the
# chosen net transport -- grep `NET/IB` vs `NET/Socket` at reload to report
# whether the cross-job group rode RDMA or fell back to TCP.
NCCL_DEBUG_VAL="${NCCL_DEBUG:-}"
if [ "${WEIGHT_TRANSPORT:-disk}" = "nccl" ] && [ "${COLOCATE:-0}" != "1" ]; then
  NCCL_DEBUG_VAL="${NCCL_DEBUG:-INFO}"
fi

# SECURITY: from here on we handle secrets (WANDB_API_KEY, the SA token) inside
# RUNTIME_ENV_JSON and the ray submit command. Turn OFF shell tracing so `set -x`
# never echoes their values into the job log. wandb picks up WANDB_API_KEY from the
# env below (no --wandb-key arg needed — that would re-leak it on the command line).
set +x

RUNTIME_ENV_JSON="{
  \"env_vars\": {
    \"PYTHONPATH\": \"/root/Megatron-LM/:${AGENT_CODE_DIR}\",
    \"CUDA_DEVICE_MAX_CONNECTIONS\": \"1\",
    \"PYTORCH_CUDA_ALLOC_CONF\": \"${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}\",
    \"NCCL_NVLS_ENABLE\": \"${HAS_NVLINK}\",
    \"NCCL_DEBUG\": \"${NCCL_DEBUG_VAL}\",
    \"SKYPILOT_API_SERVER_ENDPOINT\": \"${SKYPILOT_API_SERVER_ENDPOINT:-}\",
    \"SKYPILOT_SERVICE_ACCOUNT_TOKEN\": \"${SKYPILOT_SERVICE_ACCOUNT_TOKEN:-}\",
    \"AGENT_POOL\": \"${AGENT_POOL:-swesmith}\",
    \"AGENT_MAX_TURNS\": \"${AGENT_MAX_TURNS:-1}\",
    \"AGENT_EXEC_TIMEOUT_SEC\": \"${AGENT_EXEC_TIMEOUT_SEC:-60}\",
    \"AGENT_EVAL_TIMEOUT_SEC\": \"${AGENT_EVAL_TIMEOUT_SEC:-120}\",
    \"AGENT_SANDBOX_LIFETIME_SEC\": \"${AGENT_SANDBOX_LIFETIME_SEC:-0}\",
    \"AGENT_ROLLOUT_GUARD_SEC\": \"${AGENT_ROLLOUT_GUARD_SEC:-0}\",
    \"AGENT_SGLANG_URL\": \"${AGENT_SGLANG_URL:-}\",
    \"AGENT_LITELLM_MODEL\": \"${AGENT_LITELLM_MODEL:-openai/slime-actor}\",
    \"AGENT_WORKDIR\": \"${AGENT_WORKDIR:-/workspace}\",
    \"AGENT_LOCAL_EXEC\": \"${AGENT_LOCAL_EXEC:-}\",
    \"SANDBOX_NO_POOL\": \"${SANDBOX_NO_POOL:-0}\",
    \"ROLLOUT_BATCH_SIZE\": \"${ROLLOUT_BATCH_SIZE:-}\",
    \"N_SAMPLES_PER_PROMPT\": \"${N_SAMPLES_PER_PROMPT:-}\",
    \"WANDB_API_KEY\": \"${WANDB_API_KEY:-}\",
    \"WANDB_GROUP\": \"${WANDB_GROUP:-coding-agent}\",
    \"WANDB_PROJECT\": \"${WANDB_PROJECT:-skypilot-rl-workloads}\",
    \"WANDB_TEAM\": \"${WANDB_TEAM:-}\",
    \"MSWEA_SILENT_STARTUP\": \"1\"
  }
}"

# Ray's dashboard job agent registers async after `ray start`; submitting too
# early yields "No available agent to submit job". We retry ONLY that
# submission-REJECTION case. A job that actually RAN and then failed must NOT be
# re-run: `ray job submit` blocks+tails and returns the JOB's exit code, so a
# training crash would otherwise loop forever, re-burning GPU and hiding the real
# error (observed live: an nccl weight-sync 400 at step 2 crash-looped 5x). So we
# distinguish the two by grepping the submit output and propagate real failures.
submit_log="/tmp/raysubmit-$$.log"
submit_ok=0
for attempt in 1 2 3 4 5 6; do
  set +e
  ray job submit --address="http://127.0.0.1:8265" \
     --runtime-env-json="${RUNTIME_ENV_JSON}" \
     -- python3 ${TRAIN_SCRIPT} \
     --actor-num-nodes 1 \
     --actor-num-gpus-per-node ${NUM_GPUS} \
     ${TOPO_ARGS[@]} \
     ${MODEL_ARGS[@]} \
     ${CKPT_ARGS[@]} \
     ${ROLLOUT_ARGS[@]} \
     ${OPTIMIZER_ARGS[@]} \
     ${GRPO_ARGS[@]} \
     ${WANDB_ARGS[@]} \
     ${PERF_ARGS[@]} \
     ${SGLANG_ARGS[@]} \
     ${MISC_ARGS[@]} 2>&1 | tee "${submit_log}"
  rc=${PIPESTATUS[0]}
  set -e
  if [ "$rc" -eq 0 ]; then submit_ok=1; break; fi
  # wandb TEARDOWN RACE: training can finish + save the final checkpoint, then wandb's
  # atexit service-teardown hits an already-closed connection -> ConnectionResetError ->
  # spurious rc=1 (surfaces AFTER `exit 0`). If the run demonstrably completed (checkpoint
  # saved) and that teardown is the failure, treat it as success. Still fails on real
  # errors (OOM / NCCL / pool-not-found / warm-pool timeout).
  if grep -qiE 'successfully saved checkpoint from iteration' "${submit_log}" \
     && grep -qiE 'teardown_atexit|ConnectionResetError' "${submit_log}" \
     && ! grep -qiE 'CUDA out of memory|NCCL error|not found on any context|Timed out after [0-9]+s waiting for warm pool' "${submit_log}"; then
    echo "run completed (final checkpoint saved); rc=$rc is the known wandb atexit teardown race — treating as SUCCESS."
    submit_ok=1; break
  fi
  # Retry ONLY on submission-rejection (agent not up yet), never on a job crash.
  if grep -qiE "No available agent|Failed to submit job|Connection refused|ConnectionError|500 Internal" "${submit_log}"; then
    echo "ray agent not ready (attempt $attempt); retrying in 10s..."
    sleep 10
  else
    echo "ray job RAN and FAILED (rc=$rc) — not retrying; surfacing the error above."
    exit "$rc"
  fi
done
[ "$submit_ok" -eq 1 ]
