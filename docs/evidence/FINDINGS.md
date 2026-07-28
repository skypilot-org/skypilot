# A3 Testbed — Heterogeneous Vendor GPU Discovery Fix

Status as of 2026-07-28 (session paused, resuming later). Branch: `fix/heterogeneous-vendor-discovery`.

## What was found

Root cause confirmed exactly as diagnosed in the handoff brief, in
`sky/provision/kubernetes/utils.py`:

```python
SUPPORTED_GPU_RESOURCE_KEYS = {'amd': 'amd.com/gpu', 'nvidia': 'nvidia.com/gpu'}

def _gpu_resource_key_helper(context) -> str:
    for gpu_key in SUPPORTED_GPU_RESOURCE_KEYS.values():   # amd checked first
        if gpu_key in capacity_keys:
            return gpu_key      # one key for the WHOLE cluster
```

`get_gpu_resource_key()` resolves a single vendor key per context (cached), so on a
cluster with both `amd.com/gpu` and `nvidia.com/gpu` present, every node lacking the
"winning" key (here, AMD wins the dict-iteration race) reports 0 GPUs — even if
healthy with a correctly-resolved accelerator name. Same root key fed pod-scheduling
(`sky/clouds/kubernetes.py`), so requesting an NVIDIA GPU could have generated a pod
requesting `amd.com/gpu` on a mixed cluster.

Confirmed on the live 3-node testbed (1x NVIDIA GB10 `spark-1ba1`, 2x AMD Strix Halo
`evo-x2-green`/`evo-x2-red`): pre-fix, `sky gpus list --infra kubernetes` showed
`GB10 0 of 0 free` while `STRIXHALO` reported correctly. See `before-fix-gpus-list.txt`.

## What was changed

**Task A — discovery (`sky/provision/kubernetes/utils.py`):**
- Added `_candidate_accelerator_resource_keys()`, `resolve_accelerator_resource_key(attribute_dict)`,
  and `get_node_accelerator_resource_key(node)` — resolve the accelerator resource key
  from a single node's/pod's own resource dict instead of one pre-resolved cluster-wide
  key.
- Rewired `detect_accelerator_resource`, `get_unlabeled_accelerator_nodes`,
  `get_node_accelerator_count`, and the pod-parsing GPU count in `process_skypilot_pods`
  to use the new per-dict resolution.
- Left `get_gpu_resource_key()` / `_gpu_resource_key_helper()` untouched (still used in
  error messages and as a fallback) per the brief's guardrail.

**Task B — scheduling (`sky/provision/kubernetes/utils.py` + `sky/clouds/kubernetes.py`):**
- Added `get_accelerator_resource_key(context, acc_type)`: finds which node(s) actually
  advertise the *requested* accelerator name (reusing the existing, already-correct,
  single label-formatter node-matching logic) and returns *that* node's resource key,
  falling back to `get_gpu_resource_key()` only if no node matches (e.g. autoscale from
  zero).
- `sky/clouds/kubernetes.py`'s pod-spec builder now calls
  `kubernetes_utils.get_accelerator_resource_key(context, acc_type)` instead of the
  global `get_gpu_resource_key(context)`, so a pod requesting `GB10` always gets
  `nvidia.com/gpu` regardless of which vendor key would otherwise "win" the cluster.

**D3 — tests (`tests/unit_tests/kubernetes/test_kubernetes_utils.py`):**
- `test_get_node_accelerator_resource_key_mixed_vendor`: direct unit test with a fake
  mixed-vendor node list (one `amd.com/gpu`, one `nvidia.com/gpu`), asserts both resolve
  correctly.
- `test_mixed_vendor_gpu_realtime_availability`: end-to-end test through
  `kubernetes_catalog.list_accelerators_realtime`, asserting both `GB10` and `STRIXHALO`
  report correct non-zero capacity/availability simultaneously, without mocking
  `get_gpu_resource_key` at all.
- **Verified both tests fail on pre-fix code** (one `AttributeError`, one wrong-result
  assertion showing STRIXHALO missing) **and pass after the fix** — confirmed via
  `git stash` of just the two source files, tests left in place, rerun, restored.

## What was verified live (real cluster, not just unit tests)

All evidence saved alongside this file in `docs/evidence/`:

- **S1/S2** (`after-fix-gpus-list.txt`): `sky gpus list --infra kubernetes` shows
  `GB10 1 of 1 free` and `STRIXHALO` (2 total) simultaneously, no
  `CUSTOM_GPU_RESOURCE_KEY` set, no more "0 GPU resources" warning. Per-node table lists
  all three nodes correctly.
- **S3** (`task-b-launch-verification.txt`): `sky launch --gpus GB10:1` landed on
  `spark-1ba1`, ran `nvidia-smi` successfully, job SUCCEEDED.
- **S4** (same file): `sky launch --gpus STRIXHALO:1` correctly requested `amd.com/gpu`
  and correctly targeted only the two AMD nodes (proven by the Pending reason itself:
  `1 node(s) didn't match Pod's node affinity/selector` = the NVIDIA node correctly
  excluded; `2 Insufficient amd.com/gpu` = the AMD nodes correctly targeted, just out of
  capacity because the pre-existing `amdsvc` service held both GPUs at test time). No
  regression.

## Environment note (important for tomorrow)

The WSL `sky-env` venv is a **normal (non-editable) pip install** of `skypilot==0.13.0`
in `~/sky-env/lib/python3.11/site-packages/sky/` — **not** an editable link to this git
checkout. The actual running `sky` CLI/API server only ever reads that site-packages
copy. To test live, the same fix was manually mirrored into that copy (it's roughly one
version behind — e.g. no Neuron support — so it's not a byte-for-byte copy of this
repo's file). **If you restart fresh tomorrow and only see the old broken behavior
again, check whether the site-packages copy still has the fix** (it should, nothing
reverted it) before assuming something regressed. Long-term fix: reinstall
`skypilot` in `sky-env` as an editable install of this checkout
(`pip install -e /mnt/c/projects/skypilot`) so this manual double-patching isn't needed
again.

## What's NOT done yet (next steps)

1. **Task C (stretch, deferred)**: two concurrent `sky serve` services (NVIDIA + AMD),
   prompt-randomization fix for the benchmark harness, in-cluster load generation,
   server-side vllm metrics collection. Not started — lowest priority per the brief,
   explicitly OK to slip.
2. **D1 patch file**: not yet exported as a standalone `.patch` (the commit on this
   branch serves the same purpose for now — `git format-patch master` when ready).
3. Consider whether `get_accelerator_resource_key`'s node-iteration in
   `sky/clouds/kubernetes.py`'s hot path (called on every launch) should be memoized
   per-request like the other `detect_*`/`get_*` functions in this file
   (most use `@annotations.lru_cache(scope='request')`) — not yet profiled for
   overhead on larger clusters.
4. Not yet investigated: the appendix's "verify `kubernetes.pod_config` nodeSelector/
   affinity passthrough" question for the A3 Composer design — explicitly marked as
   context, not a Claude Code task, in the original brief.
5. Upstream PR not opened, per instructions ("do not open it").
