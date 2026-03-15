# SkyPilot Task Executors

This module contains task executors for running user scripts on cluster nodes.

## Concepts

- **Code Generator**: A `TaskCodeGen` subclass (e.g., `RayCodeGen`, `SlurmCodeGen`) that generates the job driver script. Lives in `sky/backends/task_codegen.py`.
- **Job Driver**: The generated Python script (`~/.sky/sky_app/sky_job_<id>`) that runs on the head node and orchestrates distributed execution across all nodes.
- **Task Executor**: A module that runs on each cluster node to execute the user's script. Handles environment setup, logging, and coordination with the job driver.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Code Generator                       │
│     (RayCodeGen / SlurmCodeGen in task_codegen.py)      │
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

### Ray (no separate executor)

Ray uses `ray.remote()` to dispatch tasks directly to worker nodes. The execution
logic is inlined in the generated driver script rather than a separate module,
since Ray can execute Python functions directly.
