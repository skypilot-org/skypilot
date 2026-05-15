---
name: skypilot
description: "Use when launching cloud VMs, Kubernetes pods, or Slurm jobs for GPU/TPU/CPU workloads; training, fine-tuning, or batch inference on cloud GPUs; deploying inference servers with SkyServe; writing or debugging SkyPilot task YAML; using spot/preemptible instances; optimizing GPU cost or availability; interpreting SkyPilot job status/logs; SSH-based remote diagnosis and repair; checkpoint/resume workflows; or troubleshooting SkyPilot API server, resource, or workload failures."
---

# SkyPilot Skill

SkyPilot is a unified interface for running AI workloads on clouds, Kubernetes,
Slurm, and SSH node pools. Treat it as the agent's primary control plane for
provisioning, job execution, logs, status, and cleanup. Use SSH only when the
SkyPilot CLI view is not enough to diagnose or repair the remote workload.

Keep this file loaded as the always-needed operating guide. The following files
are bundled with this skill under its `references/` directory; load them relative
to this `SKILL.md`, not relative to the user's current repository:

- `references/cli-reference.md`: exact commands and flags
- `references/yaml-spec.md`: full task schema
- `references/python-sdk.md`: programmatic use
- `references/advanced-patterns.md`: multi-cloud, distributed training,
  production patterns
- `references/troubleshooting.md`: installation, credentials, cloud/provider
  errors
- `references/examples.md`: copy-paste task YAML examples

## Must-Know Agent Rules

These are the SkyPilot behaviors agents most often get wrong:

1. Start by checking the API server once with `sky api info -o json`. If it is
   already connected, do not run setup checks unless the user's task needs them.
2. Use structured output (`-o json`) for status and queue commands. Do not parse
   human tables when JSON exists.
3. Let SkyPilot choose cloud/region by default. Specify `infra:` only when the
   user explicitly asks for a cloud, region, Kubernetes context, Slurm pool, or
   SSH node pool.
4. Use named resources. Always pass `-c NAME` for clusters and `-n NAME` or a
   YAML `name:` for jobs/tasks so logs and queues are traceable.
5. Prefer `sky exec` on existing clusters for fast iteration. Use
   `sky exec CLUSTER task.yaml -d -n JOB_NAME --env KEY=VALUE --secret NAME`
   for detached resubmission with parameter overrides and secrets. `sky exec`
   does not accept `-y`.
6. Treat `sky logs CLUSTER JOB_ID --status` exit codes semantically:
   `0=succeeded`, `100=failed`, `101=not finished`, `102=not found`,
   `103=cancelled`. Nonzero does not always mean failure.
7. Combine scheduler state with remote process state.
   `sky queue CLUSTER -o json --skip-finished` and `sky logs --status` show
   SkyPilot's view; `ssh CLUSTER` with `ps` and `nvidia-smi` shows what is
   actually running.
8. `workdir:` syncs to `~/sky_workdir`. Always `cd ~/sky_workdir` before remote
   project commands. Use `find ~/sky_workdir -maxdepth N -name NAME` instead of
   assuming local paths exactly match remote paths.
9. Local-path `workdir:` sync includes uncommitted local changes and uses rsync
   without `--delete`. Deleted local files may remain on the remote. Verify
   remote code/config when correctness depends on a specific revision.
10. SkyPilot injects `envs:`/`--env` and `secrets:`/`--secret` into the
    task setup/run environment, not into an arbitrary interactive SSH shell.
    For manual commands requiring secrets, run through `sky exec --secret ...`
    or source a deliberate remote env file.
11. Current cluster job logs live under
    `~/sky_logs/<job-id>-<job-name>/run.log`; if the directory is unclear, use
    `sky logs`/`sky logs --sync-down` or inspect `sky queue` for the job ID.
    Normalize carriage returns before grepping progress-bar logs:
    tail -n 1000 ~/sky_logs/<job>/run.log | tr "\r" "\n" | grep -E "PATTERN"
12. Use targeted log filters for long jobs. Prefer patterns like
    `Traceback|ERROR|RuntimeError|CUDA out|checkpoint|eval|loss|step` and
    `tail -n 50` over streaming full logs.
13. A stale log file is not enough to declare a hang. Check GPU/process state:
    `nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits`
    and `ps -eo pid,ppid,stat,pcpu,pmem,etime,args | grep python`. If several
    Python processes exist, use `nvidia-smi pmon -c 1`.
14. Cancel jobs at safe state boundaries. Wait for a checkpoint or durable
    progress artifact before cancelling, then verify both files on disk and log
    lines before relying on resume.
15. Keep manual remote repairs narrow and use the project's own helpers where
    possible, for example
    `cd ~/sky_workdir && PYTHONPATH=src python3 -c "from pkg import repair; repair.fix()"`.
16. If task YAML generates runtime config from env vars, inspect the generated
    config on the cluster, not only the static local default.
17. Progress bars often reset per phase, epoch, shard, or domain. Use phase
    boundary logs and persisted progress files to estimate true progress.
18. Preflight unfamiliar YAML or resource choices with
    `sky launch --dryrun task.yaml` before provisioning.
19. Respect an established persistent-cluster workflow. Otherwise set
    autostop/autodown or clean up clusters when done; cost leaks are a
    correctness issue for cloud agents.

## Bootstrap

Run:

```bash
sky api info -o json
```

Interpret the result:

| Result | Meaning | Next action |
|--------|---------|-------------|
| JSON with `server.status`, version, URL, and user | Client is connected to a reachable API server | Proceed to the user's task |
| `No SkyPilot API server is connected` | No local/default server is reachable | Start local server or connect to the endpoint the user provides |
| `Could not connect to SkyPilot API server at ...` | Remote endpoint is unreachable or auth expired | Ask the user to confirm the endpoint/auth, or use `sky api login --relogin -e URL` if URL is known |
| `command not found: sky` | SkyPilot is not installed | Install the needed extras, then re-run bootstrap |

If the user already provided a remote endpoint, do not ask for it again:

```bash
sky api login -e https://example.company.internal
sky api info -o json
```

If the user has no remote server and wants a local one:

```bash
sky api start
sky api info -o json
```

Run `sky check -o json` only for fresh setup, credential diagnosis, or when a
target cloud/infra is not enabled. Skip it when the API server is already
connected and the user is asking for ordinary launch/debug work.

## Choose The Right Abstraction

Use the highest-level SkyPilot abstraction that fits the task:

| Need | Use | Notes |
|------|-----|-------|
| Interactive development, debugging, fast reruns | `sky launch` then `sky exec` | Cluster persists until stopped/downed or autostop triggers |
| Long unattended training, fine-tuning, batch inference | `sky jobs launch` | Managed jobs handle provisioning, recovery, and teardown |
| Production-ish inference endpoints with autoscaling | `sky serve up` | Test with `sky launch` first if the run command is unproven |

Use `sky exec` rather than relaunching when only the run command, code, envs, or
small configs changed. Use `sky launch` again when setup, image, file mounts,
dependencies, resources, or cluster-level configuration changed.

## Resource Selection

Default to optimizer-friendly YAML:

```yaml
resources:
  accelerators: H100:8
```

When multiple GPU types are acceptable, use `any_of` so SkyPilot can maximize
availability and minimize cost:

```yaml
resources:
  any_of:
    - accelerators: H100:8
    - accelerators: A100-80GB:8
    - accelerators: A100:8
```

Use `ordered` only when the user has a strict preference order. Pin `infra:`
only when the user explicitly requests a provider/region/context/pool:

```yaml
resources:
  infra: aws/us-east-1
  accelerators: H100:8
```

If the user does not specify GPU requirements, ask for the workload/model or
propose a small set of GPU choices. Do not scrape `sky gpus list` and silently
choose a cloud/region yourself.

## Core Commands

Cluster iteration:

```bash
sky launch --dryrun task.yaml
sky launch -c CLUSTER task.yaml -i 30 --down
sky exec CLUSTER task.yaml -d -n JOB_NAME --env KEY=VALUE --secret SECRET_NAME
sky queue CLUSTER -o json --skip-finished
sky logs CLUSTER JOB_ID --status
sky logs CLUSTER JOB_ID --no-follow --tail 100
sky logs CLUSTER JOB_ID --sync-down
ssh CLUSTER
sky down CLUSTER
```

Managed jobs:

```bash
sky jobs launch task.yaml -n JOB_NAME -d
sky jobs queue -o json
sky jobs logs JOB_ID --no-follow --tail 100
sky jobs cancel JOB_ID
```

For managed jobs, `-d` / `--detach-run` only suppresses CLI log streaming after
submission; it does not change the managed-job lifecycle or recovery behavior.

SkyServe:

```bash
sky serve up serve.yaml -n SERVICE
sky serve status SERVICE
sky serve status SERVICE --endpoint
sky serve update SERVICE new-serve.yaml
sky serve down SERVICE
```

## Monitoring And Diagnosis

For short jobs, stream logs:

```bash
sky logs CLUSTER JOB_ID
```

For long jobs, avoid noisy loops. Use the scheduler status for state and sparse,
targeted remote probes for evidence:

```bash
sky logs CLUSTER JOB_ID --status
sky queue CLUSTER -o json --skip-finished
ssh CLUSTER 'log_dir=$(ls -dt ~/sky_logs/JOB_ID-* 2>/dev/null | head -1) && tail -n 1000 "$log_dir/run.log" | tr "\r" "\n" | grep -E "Traceback|ERROR|checkpoint|eval|loss|step" | tail -n 50'
ssh CLUSTER 'nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits'
ssh CLUSTER 'ps -eo pid,ppid,stat,pcpu,pmem,etime,args | grep python'
```

Use `sky logs CLUSTER JOB_ID --sync-down` when you need the complete log
directory for post-mortem analysis or local artifact inspection.

If scheduler status and process state disagree, report both and investigate
before taking destructive action. A RUNNING job with no new logs may still be
compute-bound; an active GPU process may belong to a different job than the one
the scheduler reports.

## Remote Repair Pattern

Use this when a launched job is failing or partially complete:

1. Identify the cluster and job ID from
   `sky queue CLUSTER -o json --skip-finished`.
2. Check `sky logs CLUSTER JOB_ID --status` and recent targeted logs.
3. SSH in, `cd ~/sky_workdir`, and verify the actual remote code/config.
4. Inspect generated config, checkpoints, progress files, process table, and
   GPU state.
5. Apply the narrowest repair using project code/helpers.
6. Resume with `sky exec CLUSTER task.yaml -d -n DESCRIPTIVE_NAME ...` or the
   appropriate managed-job command.

Do not cancel, wipe, or relaunch blindly when checkpoint/progress consistency
matters.

## Task YAML Essentials

Keep YAML simple and parameterized:

```yaml
name: train
workdir: .

resources:
  accelerators: H100:1
  disk_size: 256

envs:
  MODEL_NAME: meta-llama/Llama-3.1-8B-Instruct

setup: |
  pip install -r requirements.txt

run: |
  python train.py --model "$MODEL_NAME"
```

Use `envs:` plus CLI `--env` for routine parameters. Use `--secret` or
`secrets:` for credentials. See the bundled `references/yaml-spec.md` for file
mounts, service blocks, volumes, multi-node fields, and advanced resource
syntax.

## Common Mistakes

| Mistake | Better behavior |
|---------|-----------------|
| Re-running `sky launch` for every code change | Use `sky exec` on the existing cluster |
| Treating exit code 101 from `sky logs --status` as failure | Interpret it as "not finished" |
| Polling with tight `sleep` + `sky queue` loops | Use `sky logs CLUSTER JOB_ID --status`, or sparse targeted probes for diagnosis |
| Debugging only from logs | Combine logs, queue status, `ps`, and `nvidia-smi` |
| Assuming SSH has task secrets | Use `sky exec --secret` or a deliberate remote env file |
| Assuming local deleted files are gone remotely | Inspect or clean `~/sky_workdir` |
| Parsing progress bars literally | Normalize `\r` and use phase/checkpoint evidence |
| Cancelling before checkpoint durability is known | Wait for and verify durable state |
| Hardcoding cloud/region without a user request | Let SkyPilot optimize with `accelerators:` or `any_of` |
| Generating old `cloud:`/`region:`/`zone:` YAML fields | Use `resources.infra` instead, e.g. `infra: aws/us-east-1` |
| Forgetting cost control for disposable clusters | Launch with `-i MINUTES --down`, clean up explicitly, or keep the cluster only when the user wants reuse |
