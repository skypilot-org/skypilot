# RL Code-Execution Training with Sandbox Rewards

This example trains an LLM on code-generation tasks with GRPO (Group Relative
Policy Optimization), where **the reward comes from actually running the
model's code**. The policy generates a Python function, we execute that code
against hidden test cases inside a [SkyPilot Sandbox](https://docs.skypilot.co/en/latest/docs/sandboxes.html),
and the reward is 1.0 if every test passes and 0.0 otherwise.

It is the same distributed architecture as
[`rl-post-training-jobgroup`](../rl-post-training-jobgroup), with one component
swapped: the math answer-matching reward server is replaced by a
sandbox-backed code-execution reward server. The task domain changes from
GSM8K math to MBPP-style code generation, but the binary, verifiable reward
shape is the same.

## Architecture

The example consists of 5 task types that communicate over HTTP, with built-in
load balancing for scaling inference:

### Components

1. **data-server** (auxiliary): FastAPI server that serves MBPP-style code
   prompts. Each problem is a task description plus a set of *hidden* test
   cases (assert statements). One example test is included in the prompt as a
   signature hint; the full test list is kept for grading.

2. **rollout-server** (auxiliary, x2): SGLang inference servers with native
   load balancing:
   - Using `num_nodes: 2` creates two GPU instances for higher throughput
   - Head node (rank 0) runs both SGLang server and SGLang router on port 30000
   - SGLang router provides cache-aware load balancing for optimal KV cache reuse

3. **sandbox-reward-server** (auxiliary): The core of this example. For each
   rollout it extracts the code block from the model's response, runs
   `code + setup + hidden tests` inside an isolated SkyPilot Sandbox, and
   returns a binary reward (1.0 if the process exits 0, else 0.0). The whole
   batch is scored concurrently with the async sandbox SDK, one sandbox per
   rollout, claimed from a pre-warmed pool for sub-second launches.

4. **replay-buffer** (auxiliary): Stores experience tuples (prompt, response,
   reward) for sampling during training. Supports priority-based sampling where
   high-reward experiences are sampled more frequently. (Reused unchanged from
   the math example.)

5. **ppo-trainer** (primary): Multi-node training orchestrator that implements
   GRPO. Coordinates with all other services to fetch prompts, generate
   responses, compute rewards, store experiences, and update the policy. Every
   `--policy-sync-interval` steps, the trainer also pushes the freshly-updated
   weights back to the rollout-server via SGLang's `update_weights_from_disk`
   endpoint, so subsequent samples reflect the latest policy.

### The rollout → sandbox → reward loop

```
              ┌─────────────┐   prompts + hidden tests
              │ data-server │ ─────────────────────────┐
              └─────────────┘                           ▼
                                                 ┌───────────────┐
   policy weights  ┌──────────────┐   prompts    │  ppo-trainer  │
  ◀───────────────│ rollout-server│ ◀────────────│    (GRPO)     │
   (every N steps)│   (SGLang)    │ ───────────▶ │               │
                  └──────────────┘   responses   └───────┬───────┘
                                                         │ {response, tests}
                                                         ▼
                                            ┌──────────────────────────┐
                                            │  sandbox-reward-server    │
                                            │  create N sandboxes (pool)│
                                            │  exec code+tests  ──▶ 1/0 │
                                            └──────────────────────────┘
                                                         │ one pod per rollout
                                              ┌──────────┴──────────┐
                                              ▼          ▼          ▼
                                          [sandbox]  [sandbox]  [sandbox]
```

### Why a sandbox for the reward

RL on code (and agentic tasks generally) requires running **untrusted,
model-generated code** as part of the reward loop, at high concurrency. A
sandbox is the right tool because:

- **Per-pod isolation for untrusted code:** every sandbox is its own
  Kubernetes pod with a dedicated image, CPU, and memory. A rollout that writes
  files, forks processes, or loops forever is contained to its own pod and torn
  down afterwards. It never touches the trainer, the other rollouts, or your
  cluster's other workloads.
- **Sub-second launches via warm pools:** the reward server creates a warm pool
  on startup, so each `create` *claims* an already-running pod instead of
  waiting on Kubernetes scheduling and an image pull, cutting a sandbox's launch
  time by more than 50%. That matters when every training step scores a fresh
  batch.
- **Runs on your own infra:** sandboxes live on your own Kubernetes cluster, so
  the model-generated code and your test data never leave your environment.

The reward function never lets a bad rollout raise: a crash, a timeout, or a
non-zero exit code all map to reward 0.0.

### Policy weight sync

The trainer and rollout-server share a `ReadWriteMany` Kubernetes volume
mounted at `/shared/policy`. After every N training steps the trainer saves the
current policy there and POSTs to `/update_weights_from_disk` and `/flush_cache`
on each rollout-server backend. Both endpoints are exposed natively by
`sglang.launch_server` (no extra rollout-server code is required), so all
SGLang instances reload the new weights and drop their KV cache. The next batch
of rollouts is then generated by the updated policy through the router.

The volume is defined in `rl-code-volume.yaml`; swap in a faster shared
filesystem (NFS, etc.) for production-scale runs.

### Primary/Auxiliary Tasks

The ppo-trainer is designated as the **primary task**. When training completes:
- All auxiliary services (data-server, rollout-server, sandbox-reward-server,
  replay-buffer) are automatically terminated after a 10-second grace period
  (`termination_delay: 10s`)
- This ensures GPU and CPU resources are released promptly once training finishes

## Usage

### Prerequisites

- SkyPilot configured with a Kubernetes cluster
- GPU nodes available (H100 recommended for optimal performance)
- The **Sandbox feature** (SkyPilot Platform). The sandbox-reward-server uses
  the `sky.sandbox` SDK and must be able to reach an API server with sandboxes
  enabled. Set `SKYPILOT_API_SERVER_ENDPOINT` in the sandbox-reward-server
  task's `envs` if it isn't picked up from the ambient config.

### Create the Shared Volume

First, create the shared volume for policy weight sync:

```bash
sky volume apply llm/rl-code-execution-sandbox/rl-code-volume.yaml
```

### Launch Training

```bash
sky jobs launch llm/rl-code-execution-sandbox/rl-code-jobgroup.yaml
```

### Quick connectivity test (CPU only)

To exercise just the data-server and the sandbox reward path without GPUs (it
POSTs a known-good and known-bad solution and checks the rewards are 1.0/0.0):

```bash
sky jobs launch llm/rl-code-execution-sandbox/rl-code-jobgroup-cpu.yaml
```

### Monitor Training

```bash
# Check job status
sky jobs queue

# View logs for specific components
sky jobs logs <job-id> data-server
sky jobs logs <job-id> rollout-server
sky jobs logs <job-id> sandbox-reward-server
sky jobs logs <job-id> replay-buffer
sky jobs logs <job-id> ppo-trainer
```

Or use the SkyPilot dashboard to monitor jobs; the **Sandboxes** page shows the
warm pool and the sandboxes being created and torn down during training.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_NAME` | `Qwen/Qwen2.5-0.5B-Instruct` | Model to train |
| `NUM_EPOCHS` | `3` | Number of training epochs |
| `BATCH_SIZE` | `4` | Training batch size (= sandboxes per reward step) |
| `REWARD_POOL_NAME` | `rl-reward` | Warm sandbox pool name |
| `REWARD_POOL_REPLICAS` | `8` | Warm pods kept ready (keep ≥ `BATCH_SIZE`) |
| `REWARD_EXEC_TIMEOUT` | `30` | Per-rollout code execution timeout (seconds) |

### Customizing Resources

Edit the YAML to adjust resources per component:

```yaml
# For larger models, increase GPU memory
resources:
  accelerators: H100:1  # or A100:1
  memory: 64+
```

## Service Discovery

Components discover each other using job group DNS names:

- `data-server-0.${SKYPILOT_JOBGROUP_NAME}:8000`
- `rollout-server-0.${SKYPILOT_JOBGROUP_NAME}:30000` (SGLang router endpoint)
- `rollout-server-0.${SKYPILOT_JOBGROUP_NAME}:30001` (SGLang backend 1)
- `rollout-server-1.${SKYPILOT_JOBGROUP_NAME}:30001` (SGLang backend 2)
- `sandbox-reward-server-0.${SKYPILOT_JOBGROUP_NAME}:8002`
- `replay-buffer-0.${SKYPILOT_JOBGROUP_NAME}:8003`

## The sandbox reward server in detail

`code/sandbox_reward_server.py` exposes the same `/batch_reward` interface the
trainer already calls. The headline pattern is the async batch fan-out:

```python
async def score_batch(items):
    # One create call returns a list of sandboxes, claimed from the warm pool.
    sandboxes = await sky.sandbox.create.aio(
        name="reward", num_sandboxes=len(items), pool=POOL_NAME)
    try:
        # Score every rollout concurrently, one sandbox each.
        rewards = await asyncio.gather(
            *(score_one(sb, item) for sb, item in zip(sandboxes, items)))
    finally:
        # ALWAYS tear sandboxes down, even if an exec raises.
        await asyncio.gather(*(sb.terminate.aio() for sb in sandboxes),
                             return_exceptions=True)
    return list(rewards)
```

Each rollout is scored by running its code in the pod:

```python
result = await asyncio.wait_for(
    sb.exec.aio("python", "-c", script),  # argv tokens, no implicit shell
    timeout=EXEC_TIMEOUT_SECONDS)
passed = result["exit_code"] == 0        # stdout / stderr / exit_code
reward = 1.0 if passed else 0.0
```

The warm pool is created once on server startup (`sky.sandbox.create_pool(...)`)
and the shared session is released once on shutdown (`sky.sandbox.aclose()`).

## GRPO Algorithm

GRPO (Group Relative Policy Optimization) is a simplified variant of PPO that:
- Doesn't require a critic/value model
- Uses group-relative advantages (compares rewards within a batch)
- Works well with verifiable rewards (math, **code**)

The training loop:
1. Fetch a batch of prompts (+ hidden tests) from data-server
2. Generate responses using rollout-server
3. Compute rewards by running each response's code in a sandbox
4. Store experiences in replay-buffer
5. Calculate group-relative advantages
6. Update policy with the policy-gradient loss
7. Sample from replay-buffer for additional updates (experience replay)

## Extending This Example

### Richer reward shaping

The current reward is binary (all tests pass or not). You can make it dense by
counting the fraction of passing tests, rewarding faster solutions, or penalizing
runtime errors differently from assertion failures, all by inspecting the
sandbox's `stdout` / `stderr` / `exit_code`.

### Heavier execution environments

Point the warm pool at a different image (e.g. one with `numpy`, `pandas`, or a
full toolchain) via `--pool-image`, or mount a volume with fixtures into each
sandbox at create time.

### Scaling Up

For larger models or bigger batches:
1. Increase SGLang tensor parallelism
2. Raise `REWARD_POOL_REPLICAS` to keep up with the batch size
3. Use multiple GPUs per trainer node

## References

- [SkyPilot Sandboxes](https://docs.skypilot.co/en/latest/docs/sandboxes.html) - Isolated compute environments
- [GRPO Paper](https://arxiv.org/abs/2402.03300) - Group Relative Policy Optimization
- [MBPP Dataset](https://huggingface.co/datasets/google-research-datasets/mbpp) - Mostly Basic Python Problems
- [`rl-post-training-jobgroup`](../rl-post-training-jobgroup) - The math-reward example this is adapted from
