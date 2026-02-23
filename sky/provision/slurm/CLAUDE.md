# Slurm Provisioner — Developer Guide

This document captures non-obvious behaviors, footguns, and architectural
decisions in SkyPilot's Slurm backend. If you're modifying Slurm code, read
this first.

## Architecture Overview

SkyPilot's Slurm backend has three layers:

1. **Provisioning** (`sky/provision/slurm/instance.py`): submits an `sbatch`
   script that allocates nodes, optionally creates containers, and runs
   `sleep infinity` to keep the allocation alive.
2. **Runtime setup** (`sky/provision/instance_setup.py`): installs the
   skypilot-runtime venv, starts the skylet daemon, etc. — all via
   `SlurmCommandRunner`.
3. **Task execution** (`sky/backends/task_codegen.py`): generates a Python
   script that dispatches setup/run commands to nodes. Two implementations:
   `SlurmCodeGen` (srun-based) and `MonarchCodeGen` (actor-based).

## The Two Home Directories

This is the single most confusing aspect of the Slurm backend.

There are **two different "home" directories** and they serve different purposes:

| Directory | Example | Purpose |
|-----------|---------|---------|
| **sky_cluster_home_dir** | `/fsx/ubuntu/.sky_clusters/my-cluster-5f61fc93/` | Per-cluster home on **shared NFS**. This is where skylet, job scripts, logs, and marker files live. `SlurmCommandRunner` sets `HOME` to this directory for non-container commands. |
| **skypilot_runtime_dir** | `/tmp/my-cluster-5f61fc93/` | Per-cluster runtime on **node-local `/tmp`**. This is where the `skypilot-runtime` venv is installed. Each compute node has its own copy. |

### Why this matters

- `os.path.expanduser('~')` returns **different paths** depending on context:
  - **Skylet / job scripts** (non-container): `~` = `sky_cluster_home_dir`
    (because `SlurmCommandRunner._run_via_srun` does `export HOME="$PWD"` after
    `cd sky_dir`)
  - **SSH into container**: `~` = `/root` (Pyxis `--container-remap-root`)
  - **srun into container**: `~` = the user's actual home (`/fsx/ubuntu` on
    HyperPod, `/home/user` elsewhere)
  - **Login node**: `~` = the user's actual home

- **Shared filesystem assumption**: `sky_cluster_home_dir` MUST be on shared
  NFS so all nodes see the same files (job scripts, logs, marker files).
  `skypilot_runtime_dir` is node-local and created on each node separately.

- **Container mounts**: Pyxis only mounts what you specify. On HyperPod,
  `/fsx/ubuntu:/fsx/ubuntu` is mounted. The container's `/tmp` is
  **container-local tmpfs**, NOT the host's `/tmp`. This means
  `skypilot_runtime_dir` (`/tmp/cluster-name/`) is inside the container, not
  shared with the host.

## SlurmCommandRunner: run() vs run_driver()

`SlurmCommandRunner` (in `sky/utils/command_runner.py`) wraps every command in
an `srun` step. There are two methods:

```
runner.run(cmd)        → runs INSIDE the container (if container cluster)
runner.run_driver(cmd) → ALWAYS runs on the HOST
```

Under the hood, both call `_run_via_srun()`. The difference:

- **`run()`**: For container clusters, adds `--container-name=...:exec` and
  `--container-remap-root` to the srun. The command runs inside the container.
  For non-container clusters, sets `HOME` to `sky_dir` and `cd`s there.
- **`run_driver()`**: Always passes `in_container=False`. Sets `HOME` to
  `sky_dir`, `cd`s there. Useful when you need to run host-level commands
  (e.g., inner srun, host filesystem operations) even on a container cluster.

### Footgun: every run() is an srun step

Every `runner.run()` call creates an srun step (`srun --overlap --nodes=1
--ntasks=1 ...`). This has consequences:

- **SLURM env vars are set** by the outer srun: `SLURM_NNODES=1`,
  `SLURM_NTASKS=1`, `SLURM_JOB_NODELIST=<single_node>`, etc.
- If your command launches an **inner srun**, the outer srun's env vars
  **constrain** it. Your inner srun will only see 1 node, 1 task, etc.

**Fix**: Unset all `SLURM_*` env vars (except `SLURM_JOB_ID`) before an inner
srun:
```bash
for v in $(env | grep ^SLURM | cut -d= -f1); do
  [ "$v" != "SLURM_JOB_ID" ] && unset "$v"
done
srun --nodes=N --ntasks-per-node=1 ...
```

Also add `--cpu-bind=none --mem=0` to the inner srun to avoid resource
conflicts with the outer step.

## Container (Pyxis/Enroot) Gotchas

SkyPilot uses [Pyxis](https://github.com/NVIDIA/pyxis) for container support.
Key non-obvious behaviors:

### srun is NOT available inside containers

Bare container images (like `ubuntu`) don't have Slurm tools installed. If you
need to run multi-node commands from inside a container, you can't use srun.
You must either:
- Run srun from the HOST via `run_driver()`, or
- Use per-node `runner.run()` calls (which the SlurmCommandRunner wraps in
  srun + container exec for you)

### python3 is NOT on PATH inside containers

The skypilot-runtime venv is installed at `/tmp/<cluster>/skypilot-runtime/`
(inside the container's local `/tmp`), but it's not on `PATH`. The python
binary path is written to `/root/.sky/python_path` during setup. To find
python inside a container:

```bash
$(cat /root/.sky/python_path 2>/dev/null || echo /root/skypilot-runtime/bin/python3)
```

### Container exec timing

`--container-name=<name>:exec` only works if the container is already running.
If you try to exec into a container that hasn't been created yet (or was just
destroyed by `sky down`), you'll get:

```
"exec" flag was passed to --container-name but the container is not running
```

This can happen during rapid teardown+relaunch cycles on the same node.

### Container networking

Pyxis containers share the host's network namespace. TCP connections between
the host and container, or between containers on the same node, work without
port mapping.

## Proctrack Types

Slurm's process tracking (`ProctrackType`) determines what happens to
background processes when an srun step exits:

| Type | Behavior | Implication |
|------|----------|-------------|
| `cgroup` | All child processes killed when step exits | Background processes (`nohup cmd &`) die when the parent srun step returns |
| `linuxproc` | Only the direct process is tracked | Background processes survive after the srun step exits |

Check with: `scontrol show config | grep ProctrackType`

### Footgun: nohup + disown behavior differs

On `cgroup` proctrack, `nohup cmd & disown` does NOT keep the process alive
after the srun step exits — cgroup kills everything in the cgroup. On
`linuxproc`, it works as expected.

If you need persistent background processes on `cgroup` clusters, you must
start them as part of the main sbatch script (not via an srun step).

The proctrack type is cached in `sky_cluster_home_dir/.sky_proctrack_type`.

## srun Step Concurrency

Each `sky exec` / `sky launch` job runs as an srun step within the sbatch
allocation. The concurrency rules:

- `--exclusive --gpus-per-node=N`: Exclusively claims N GPUs. Multiple steps
  with **different** GPU counts CAN run concurrently — Slurm partitions GPUs.
- `--exclusive` (without `--gpus-per-node`): Claims ALL node resources. Blocks
  behind any running step.
- `--overlap`: Shares resources. Used for setup commands.

**Footgun**: `--exclusive` without `--gpus-per-node` blocks even if you don't
need GPUs. Always pass `--gpus-per-node=0` for non-GPU tasks to avoid blocking.

## Shared Filesystem Discovery Pattern

When workers or processes on different nodes need to find each other, the
pattern is:

1. Create a discovery directory on shared NFS
2. Each process writes a file (named by hostname) with its address
3. The coordinator reads all files in the directory

**Footgun**: Make sure to `rm -rf` the directory before `mkdir -p` on each
launch. Stale files from previous runs will be picked up. Also, never write
non-discovery files (logs, temp files) into the discovery directory.

## Code Generation Architecture

`task_codegen.py` contains code generators that produce Python scripts
executed by the skylet daemon:

- **`RayCodeGen`**: For cloud VMs (AWS, GCP, etc.). Uses Ray actors.
- **`SlurmCodeGen`**: For Slurm. Uses srun + signal files + threads.
- **`MonarchCodeGen`**: For Slurm with `use_monarch: true`. Uses PyTorch
  Monarch actors.

The generated script goes through: `codegen → write to .sky/sky_app/sky_job_N
→ skylet executes via subprocess`.

### SlurmCodeGen signal file coordination

`SlurmCodeGen` uses filesystem-based signal files for coordination:

- `alloc_signal_file`: srun writes this when resources are allocated
- `setup_done_signal_file`: setup thread writes this when setup completes
- `.sky_run_done_*`: proctrack barrier hack for process cleanup

This is fragile. `MonarchCodeGen` replaces all of this with async actor
dispatch.

## Key File Locations on the Cluster

```
# Shared NFS (visible to all nodes)
~/.sky_clusters/<cluster>/              # sky_cluster_home_dir (HOME for jobs)
~/.sky_clusters/<cluster>/.sky/         # skylet, job scripts
~/.sky_clusters/<cluster>/sky_logs/     # job logs
~/.sky_clusters/<cluster>/monarch_workers/  # worker discovery files (if Monarch)
~/.sky_clusters/<cluster>/.sky_slurm_cluster  # marker: "this is a Slurm cluster"
~/.sky_clusters/<cluster>/.sky_proctrack_type  # cached proctrack type

# Node-local /tmp (per compute node, NOT shared)
/tmp/<cluster>/                         # skypilot_runtime_dir
/tmp/<cluster>/skypilot-runtime/        # venv with SkyPilot + dependencies

# Inside containers (node-local, container-scoped)
/root/.sky/python_path                  # path to python binary
/root/skypilot-runtime/                 # symlink or copy of venv
```

## Testing Tips

See also: the `slurm-testing` skill for detailed testing procedures.

### Fast iteration without API server restart

SSH to the login node for direct Slurm inspection:
```bash
ssh -F ~/.slurm/config aws-hyperpod
squeue -o "%i %j %T %N"         # list jobs
srun --overlap --jobid=JOBID bash -c 'ls /path'  # run on compute node
```

### Common debug commands
```bash
# Check what HOME is in different contexts
ssh my-cluster 'echo $HOME'                    # via SlurmCommandRunner
ssh -F ~/.slurm/config aws-hyperpod \
  'srun --jobid=JOBID --overlap bash -c "echo \$HOME"'  # raw srun

# Check container state
ssh -F ~/.slurm/config aws-hyperpod \
  'srun --jobid=JOBID --overlap enroot list'

# View sbatch script output
ssh -F ~/.slurm/config aws-hyperpod \
  'ls -t ~/.sky_provision/slurm*.out | head -1 | xargs tail -50'

# Check proctrack type
ssh -F ~/.slurm/config aws-hyperpod \
  'scontrol show config | grep ProctrackType'
```
