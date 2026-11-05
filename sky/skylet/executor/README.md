# SkyPilot Task Executors

This module contains task executors for running user scripts on cluster nodes.

## Concepts

- **Code Generator**: A `TaskCodeGen` subclass (e.g., `RayCodeGen`, `SlurmCodeGen`, `PbsCodeGen`) that generates the job driver script. Lives in `sky/backends/task_codegen.py`.
- **Job Driver**: The generated Python script (`~/.sky/sky_app/sky_job_<id>`) that runs on the head node and orchestrates distributed execution across all nodes.
- **Task Executor**: A module that runs on each cluster node to execute the user's script. Handles environment setup, logging, and coordination with the job driver.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Code Generator                       │
│     (Ray/Slurm/PbsCodeGen in task_codegen.py)       │
│            Generates the job driver script              │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│                      Job Driver                         │
│    (~/.sky/sky_app/sky_job_<id> - runs on head node)    │
└─────────────────────────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  Task Executor  │ │  Task Executor  │ │  Task Executor  │
│     (head)      │ │    (worker1)    │ │    (worker2)    │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Executors

### `slurm.py` - Slurm Task Executor

Invoked on each Slurm compute node via:
```bash
srun python -m sky.skylet.executor.slurm --script=<user_script> --log-dir=<path> ...
```

Handles Slurm-specific concerns:
- Determines node identity from `SLURM_PROCID` and cluster IP mapping
- Coordinates setup/run phases via signal files on shared NFS
- Writes and streams logs to unique per-node log files

### `pbs.py` - PBS Task Executor

Invoked on each node of a PBS allocation by the job driver: locally on the
head node, and over the allocation's own user-level `sshd` on the workers:
```bash
python -m sky.skylet.executor.pbs --rank=<N> --script=<user_script> --log-dir=<path> ...
```

Handles PBS-specific concerns:
- Takes its rank from the driver (PBS has no per-task launcher the driver can use from outside the job)
- Runs the script in its own process group, so the driver can cancel it with one signal
- Writes and streams logs to unique per-node log files

Also hosts `TaskResourceSlot`, the per-cluster allocator the driver uses to
queue SkyPilot jobs behind each other: a PBS allocation has no scheduler of
its own for the jobs that share it.

### Ray (no separate executor)

Ray uses `ray.remote()` to dispatch tasks directly to worker nodes. The execution
logic is inlined in the generated driver script rather than a separate module,
since Ray can execute Python functions directly.
