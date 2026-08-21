# Migrating from Slurm Reference

This reference is for converting existing Slurm workloads into SkyPilot task
YAMLs, and for answering "how do I do X, which I used to do with Slurm?".

Two distinct situations, which need different answers:

- **SkyPilot on the user's existing Slurm cluster.** SkyPilot submits to it
  through the login node via `sbatch`. The cluster is unchanged. Most users
  start here — do not assume they want to leave Slurm.
- **SkyPilot on Kubernetes or a cloud.** The workload moves off Slurm. The
  YAML is the same; the constraints are different (see section 8).

Ask which one applies if it is not obvious. The conversion is nearly identical;
the difference shows up in lifecycle (section 7) and what is unsupported
(section 8).

---

## 1. Conversion Procedure

Follow this order. Do not skip step 5.

1. **Read the whole `sbatch` script**, not just the `#SBATCH` block. The
   `srun` invocations and `module load` lines carry as much information as the
   directives.
2. **Map the `#SBATCH` directives** to `resources` / `num_nodes` (section 2).
3. **Map the body**: `module load` → `setup`, environment activation → `setup`,
   the actual work → `run` (section 6).
4. **Translate `srun`** — this is the step most likely to be done wrong
   (section 5).
5. **Validate**: `sky launch --dryrun <yaml>`. This resolves resources and
   runs the optimizer without provisioning anything. Note there is **no**
   `--dryrun` on `sky jobs launch`, so dry-run against `sky launch` even when
   the final command will be `sky jobs launch`.
6. **Tell the user which submit command to use**: `sky jobs launch` for batch
   work (an `sbatch` script), `sky launch` for an interactive allocation (an
   `salloc` session).

### 1.1 Choosing the submit command

| The user's script is | Use | Why |
|---|---|---|
| `sbatch` batch job | `sky jobs launch` | Allocation released when the job ends, and the job is recovered if the node fails |
| `salloc` / `srun --pty` session | `sky launch` + `ssh <cluster>` | Long-lived allocation to work in |
| `sbatch --array` | `sky jobs launch --num-jobs N` | Section 7.1 |
| A chain with `--dependency` | One managed pipeline | Section 7.2 |

---

## 2. `#SBATCH` Directive Mapping

| `#SBATCH` directive | SkyPilot YAML | Notes |
|---|---|---|
| `--nodes=2` | `num_nodes: 2` | Task-level field, **not** under `resources` |
| `--gpus-per-node=8`, `--gres=gpu:h100:8` | `resources.accelerators: H100:8` | Emitted as `--gres=gpu:<type>:<count>`. The name must match the cluster's GRES config |
| `--cpus-per-task=32` | `resources.cpus: 32+` | `+` means "at least". Emitted as `--cpus-per-task` |
| `--mem=256G` | `resources.memory: 256+` | In GB. Emitted as `--mem` |
| `--partition=gpu` | `resources.infra: slurm/<cluster>/gpu` | Omit to let the optimizer choose a partition |
| `--time=24:00:00` | `config.slurm.sbatch_options.time: "24:00:00"` | Defaults to the partition's `MaxTime` if unset. **Not** `autostop` — see section 8 |
| `--job-name=train` | `name: train` | |
| `--output=`, `--error=` | *(nothing)* | SkyPilot manages job logs; see section 7.4 |
| `--account`, `--qos`, `--reservation`, `--exclusive`, `--constraint`, ... | `config.slurm.sbatch_options.<key>` | Section 3 |

### 2.1 Worked example

Input:

```bash
#!/bin/bash
#SBATCH --job-name=train
#SBATCH --nodes=2
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --partition=gpu
#SBATCH --time=24:00:00
#SBATCH --account=ai-research

module load cuda/12.1
source ~/venv/bin/activate

srun python train.py --epochs 100
```

Output:

```yaml
# train.yaml
name: train

num_nodes: 2

resources:
  infra: slurm/mycluster/gpu
  accelerators: H100:8
  cpus: 32+
  memory: 256+

config:
  slurm:
    sbatch_options:
      time: "24:00:00"
      account: ai-research

setup: |
  pip install -r requirements.txt

run: |
  python train.py --epochs 100
```

Submit with `sky jobs launch -n train train.yaml`.

Note what happened to the `srun`: it disappeared. `run` already executes on
every node of the allocation, so wrapping the command in `srun` again would
nest a job step inside SkyPilot's own. See section 5.

---

## 3. Directives SkyPilot Does Not Model

Anything without a `resources` equivalent goes through
`slurm.sbatch_options`, which is emitted verbatim as `#SBATCH` lines. Keys are
`sbatch` long option names; underscores become hyphens; `true` emits a bare
flag; `null` / `false` omit the line.

```yaml
config:
  slurm:
    sbatch_options:
      account: ai-research
      qos: high
      time: "24:00:00"
      exclusive: true
      constraint: ib
```

The same block works in `~/.sky/config.yaml` under a top-level `slurm:` key to
apply fleet-wide, and can be scoped per cluster or per partition under
`slurm.cluster_configs`.

**Do not put these in `sbatch_options`** — SkyPilot manages them and will drop
them with a warning:

```
job-name  output  error  nodes  wait-all-nodes
no-requeue  cpus-per-task  mem  gres  partition
```

If a user asks for one of those, set it through `resources` / `name` instead.

---

## 4. Environment Variable Mapping

| Slurm | SkyPilot | Notes |
|---|---|---|
| `$SLURM_JOB_NODELIST` | `$SKYPILOT_NODE_IPS` | Newline-separated IPs, **not** a Slurm hostlist expression. `$(echo "$SKYPILOT_NODE_IPS" \| head -n1)` is the head node |
| `$SLURM_NNODES` | `$SKYPILOT_NUM_NODES` | |
| `$SLURM_NODEID`, `$SLURM_PROCID` | `$SKYPILOT_NODE_RANK` | `0` to `num_nodes-1` |
| `$SLURM_GPUS_PER_NODE` | `$SKYPILOT_NUM_GPUS_PER_NODE` | |
| `$SLURM_JOB_ID` | `$SKYPILOT_TASK_ID` | |
| `$SLURM_ARRAY_TASK_ID` | `$SKYPILOT_JOB_RANK` | Only set under `--num-jobs`; section 7.1 |
| `$SLURM_ARRAY_TASK_COUNT` | `$SKYPILOT_NUM_JOBS` | |

**On Slurm specifically**, the *job-scoped* `SLURM_*` variables of the
underlying allocation are still present inside `run` — `SLURM_JOB_ID`,
`SLURM_JOB_NODELIST`, `SLURM_GPUS_ON_NODE`, etc. Scripts that read them keep
working. The *step-scoped* variables from SkyPilot's own job step
(`SLURM_CPUS_PER_TASK`, `SLURM_CPU_BIND*`, `SLURM_STEP_*`, `SLURM_PROCID`, ...)
are deliberately unset before the user script runs, so a nested `srun` targets
the full allocation instead of inheriting SkyPilot's step shape.

Prefer the `SKYPILOT_*` variables in generated YAML — they are what makes the
same task portable to Kubernetes and clouds. Use the `SLURM_*` ones only when
translating a script that must keep working unchanged.

---

## 5. Translating `srun`

Three cases. Pick by what the `srun` was *for*.

### 5.1 `srun <program>` launching one process per node

Drop the `srun`. `run` already runs on every node.

```bash
# Slurm
srun python preprocess.py
```

```yaml
run: |
  python preprocess.py
```

### 5.2 `srun` launching distributed training ranks

Use the rank variables and the framework's own launcher. This is the portable
form and should be the default for training workloads.

```yaml
num_nodes: 2

resources:
  accelerators: H100:8

run: |
  MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)
  torchrun \
    --nnodes=$SKYPILOT_NUM_NODES \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --node_rank=$SKYPILOT_NODE_RANK \
    --master_addr=$MASTER_ADDR \
    --master_port=8008 \
    train.py
```

### 5.3 `srun` launching an MPI/PMIx program

Some binaries must be launched by Slurm's own PMI (NCCL tests, anything
MPI-linked, `--mpi=pmix`). Keep `srun`, but:

- **Gate it on rank 0**, or every node will launch the whole job.
- **Pass `--overlap`**, so the step can share the allocation with SkyPilot's
  own step.

```yaml
num_nodes: 2

resources:
  infra: slurm
  accelerators: H100:8

run: |
  if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
    srun --overlap --mpi=pmix \
      --ntasks-per-node=$SKYPILOT_NUM_GPUS_PER_NODE \
      ./my_mpi_program
  fi
```

This form is Slurm-only by construction — it will not run on Kubernetes or a
cloud. Say so when generating it.

---

## 6. Replacing the Module System

`module load` reads a cluster-wide tree shared by all jobs. SkyPilot gives
each job its own environment. In increasing order of isolation:

```yaml
# 1. setup commands — runs once per cluster, before the job
setup: |
  pip install -r requirements.txt
```

```yaml
# 2. a container image — the closest thing to a reproducible module set.
#    On Slurm this needs the Pyxis SPANK plugin on the cluster.
resources:
  image_id: docker:pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime
```

```yaml
# 3. conda on the shared filesystem, if the user wants to keep their layout
setup: |
  conda create -n myenv python=3.10 -y
  conda activate myenv
  conda install pytorch pytorch-cuda=12.1 -c pytorch -c nvidia -y

run: |
  conda activate myenv
  python train.py
```

`module load` itself still works inside `setup` / `run` on a Slurm cluster,
as long as the task is not running in a container. Keeping it is a legitimate
first migration step — it just does not port to Kubernetes or clouds.

---

## 7. Patterns Without a One-to-One Field

### 7.1 Job arrays → `--num-jobs`

`sbatch --array=0-99` becomes:

```bash
sky jobs launch --num-jobs 100 -y -d task.yaml
```

Each job gets `$SKYPILOT_JOB_RANK` (the index) and `$SKYPILOT_NUM_JOBS`:

```yaml
run: |
  python train.py --shard $SKYPILOT_JOB_RANK --num-shards $SKYPILOT_NUM_JOBS
```

Each job provisions its own allocation. To reuse workers across submissions
instead — closer to how a long-lived Slurm allocation behaves, and much
cheaper when `setup` is expensive — submit to a pool:

```bash
sky jobs pool apply -p mypool pool.yaml
sky jobs launch -p mypool --num-jobs 100 task.yaml
```

For sweeps over *named* parameters rather than an index, loop with `--env`:

```bash
for lr in 0.001 0.01 0.1; do
  sky jobs launch --env LR=$lr -y -d task.yaml
done
```

### 7.2 Job dependencies → a managed pipeline

`sbatch --dependency=afterok:<jobid>` becomes a sequence of tasks in one
managed job, separated by `---`. Each task can request different resources.

```yaml
name: train-then-eval

---

name: train
resources:
  accelerators: H100:8
run: python train.py

---

name: eval
resources:
  accelerators: L4:1
run: python eval.py
```

For tasks that should run *in parallel* rather than in sequence, use a job
group instead of a pipeline.

### 7.3 Login node workflow

There is no login node in the SkyPilot workflow. Submit from the user's laptop
or CI; SkyPilot makes the SSH hop to the login node itself. Sync code with
`workdir` (a local path or a git URL) rather than editing on NFS. For
interactive work, `ssh <cluster>` lands on the *allocated compute node*, not a
shared login node.

### 7.4 Job output files

Slurm writes `slurm-<jobid>.out`. SkyPilot streams logs instead:

```bash
sky jobs logs <job_id>       # managed job
sky logs <cluster>           # latest job on a cluster
sky logs <cluster> 2         # a specific job
```

Do not translate `--output` / `--error` into anything. If the script writes
its own result files, point them at the shared filesystem or a bucket.

---

## 8. Constraints When Running on Slurm

Do not generate YAML that uses these on a Slurm target — they will fail or be
rejected:

| Feature | Status on Slurm | What to do instead |
|---|---|---|
| `autostop` / `sky stop` | Unsupported | `sky down` explicitly; bound wall-clock with `sbatch_options.time` |
| `ports:` / `sky serve` | Unsupported | Run serving on Kubernetes or a cloud |
| `use_spot: true` | Unsupported | Omit; there is no spot tier |
| Jobs/serve controllers | Not hosted on Slurm | With a remote API server, consolidation mode runs the controller inside the API server, so `sky jobs launch` works against a Slurm-only fleet. With a local API server the controller needs Kubernetes or a cloud |
| `image_id: docker:...` | Needs Pyxis on the cluster | Fall back to `setup` commands |
| Bucket mounting (`mode: MOUNT`) | Needs FUSE on compute nodes | Use the shared filesystem, or `mode: COPY` |

Because autostop does not exist on Slurm, an idle `sky launch` cluster holds
its allocation until `sky down`. Prefer `sky jobs launch` for anything
batch-like so the allocation is released automatically.

### 8.1 If the target is Kubernetes instead

The assumption that breaks first is shared storage: Kubernetes does not mount
home directories. A Slurm script that reads `~/data` or writes `~/checkpoints`
needs one of:

- an NFS mount via `config.kubernetes.pod_config` (if the nodes can reach the
  existing NFS server),
- a `ReadWriteMany` PVC through SkyPilot volumes,
- a bucket under `file_mounts` with `mode: MOUNT`,
- `workdir` for code only.

Partitions map to Kubernetes contexts (`infra: kubernetes/<context>`), and
Slurm's fairshare/QoS has no built-in equivalent — that is Kueue or priority
classes.

---

## 9. Validation Checklist

Before handing a converted YAML to the user:

- [ ] `sky launch --dryrun <yaml>` resolves without error.
- [ ] `num_nodes` is at task level, not under `resources`.
- [ ] No `autostop` / `ports` / `use_spot` if the target is Slurm.
- [ ] Every `srun` is accounted for by section 5 — dropped, replaced with
      rank variables, or kept with `--overlap` and gated on rank 0.
- [ ] GPU name matches the cluster's GRES names (check `sky gpus list --infra
      slurm`).
- [ ] Protected `sbatch` options are not in `sbatch_options` (section 3).
- [ ] The user is told whether to run `sky launch` or `sky jobs launch`.
