# Two-cluster coding-agent RL rollouts on SkyPilot sandboxes

A minimal coding-agent RL inner loop split across two Kubernetes clusters:

- a **model server** (any OpenAI-compatible endpoint — e.g. vLLM serving a coder
  model) runs on a **GPU cluster**;
- **rollout sandboxes** run on a (cheap, CPU) **sandbox cluster** and dial the
  model server across the cluster boundary to fix a bug;
- each diff is graded in a fresh **anti-cheat eval sandbox** that starts clean,
  so only the model-produced diff affects the reward;
- rollouts fan out concurrently, so raising `--num-sandboxes` also exercises
  sandbox-cluster autoscaling.

> Requires the SkyPilot **sandbox feature** (the `sky.sandbox` SDK) and a
> SkyPilot API server the SDK can reach.

## Files

| File | Role |
|------|------|
| `run_split_cluster.py` | the driver: fan out rollouts on the sandbox cluster, grade, report rewards |
| `skypilot_backend.py` | `SkyPilotSandbox` — implements the vime async `Sandbox` protocol over `sky.sandbox` |
| `agent_openai.py` | the agent that runs *inside* each rollout sandbox (OpenAI client → model server) |
| `gpu_model_server.yaml` | example vLLM serving job for the GPU cluster |
| `expose_model_server.sh` | exposes the model server via a LoadBalancer; prints the `/v1` URL |

## Run

```bash
# 1. Serve a model on the GPU cluster (detached). --ports creates the service.
sky launch -c model-server -d --infra k8s/<gpu-context> \
  llm/coding-agent-rl-rollout/gpu_model_server.yaml

# 2. Expose it for the sandbox cluster to reach; capture the endpoint.
eval "$(./llm/coding-agent-rl-rollout/expose_model_server.sh <gpu-context> | grep HEAD_URL)"
#  -> HEAD_URL=http://<ip>:8000/v1

# 3. Run rollouts on the sandbox cluster against the model server.
cd llm/coding-agent-rl-rollout
python run_split_cluster.py --head-url "$HEAD_URL" \
  --sandbox-context <cpu-context> --num-sandboxes 1     # smoke test
python run_split_cluster.py --head-url "$HEAD_URL" \
  --sandbox-context <cpu-context> --num-sandboxes 32    # fan out / autoscale
```

The driver prints per-rollout rewards and a summary (`rollouts=N solved=M
mean_reward=… wall=…s`). Rollout and eval sandboxes are given an auto-reap
`timeout` so an abandoned run cleans itself up.

## The task

A buggy `add()` that subtracts instead of adds. The agent reads
`PROBLEM_STATEMENT.md`, edits `calc.py`, and the diff is graded against an
authoritative `test_calc.py` that is re-laid-down clean in the eval sandbox —
so the agent can't win by editing the test.
