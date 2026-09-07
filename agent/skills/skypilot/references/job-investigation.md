# Investigating a Managed Job

How to answer "what is my job doing and why" with as few commands as
possible. Every command here supports `-o json`; prefer it over parsing
table output.

## Which command answers which question

| Question | Command | Look at |
|----------|---------|---------|
| What state is job N in, and why? | `sky jobs queue -v -o json` | `status`, `details`, `failure_reason`, `schedule_state`, `recovery_count` |
| What has happened to job N over time? | `sky jobs events N -o json` | `new_status`, `reason`, `timestamp` (newest first) |
| Why is job N still provisioning / pending on the cluster? | `sky jobs events N --cluster-events -o json` | `reason` lines starting with `Launching (` |
| What did job N print recently? | `sky jobs logs N --tail 100 --no-follow` | stdout/stderr of the task |
| What did the controller do (provision, recover, retry)? | `sky jobs logs N --controller --no-follow` | controller log |
| Wait until job N finishes | `sky jobs logs N` (blocks) | exit when the job ends |

Do not poll with `sleep` + `sky jobs queue`. Stream logs to block, or
fetch `--tail` once after the fact.

## Reading `details`

`details` in `sky jobs queue -v -o json` is the one-line explanation of the
current state. Common shapes:

| `details` | Meaning | Next step |
|-----------|---------|-----------|
| `Waiting for other jobs to launch` | Controller is at its concurrent-launch limit | Nothing to fix; check `sky jobs events N` for how long it has waited |
| `Waiting for higher priority jobs to launch` | A higher-priority managed job is ahead | Raise `priority` in the task YAML if this job matters more |
| `In backoff, waiting for resources` | Provisioning failed and is retrying | `sky jobs logs N --controller --no-follow` shows the last failure |
| `Recovering: <reason>` | Cluster was lost or the task crashed (e.g. OOMKilled) | Fix the cause; recovery is automatic |
| `Failure: <reason>` | Terminal failure | Read the reason; `sky jobs logs N --no-follow` for the task output |
| `Launching (pending: <Slurm reason>)` | Job is STARTING and Slurm has not allocated nodes yet (same text appears in `sky jobs events N --cluster-events`) | See "Slurm pending reasons" below |

## Slurm pending reasons

When a managed job runs on Slurm, `sky jobs events N --cluster-events`
carries the `squeue` reason while the allocation is pending. The common
codes:

| Reason | Category | What to do |
|--------|----------|------------|
| `Resources`, `Priority`, `ReqNodeNotAvail` | No free capacity in the partition | Wait, or resubmit to another partition via `config.slurm` |
| `QOSGrpGRES`, `QOSMax*`, `AssocGrp*` | A QoS / account quota is full | Wait for the group's jobs to finish, or use a different QoS/partition |
| `Dependency`, `DependencyNeverSatisfied` | Waiting on another Slurm job; `NeverSatisfied` will pend forever | Check the dependency job; cancel and resubmit if it failed |
| `JobHeldUser`, `JobHeldAdmin`, `launch failed requeued held` | Job is held | Ask the admin to `scontrol release`, or cancel and relaunch |
| `BeginTime` | Scheduled start time not reached | Nothing to fix |

## Reading `sky jobs events`

Events are newest first. A healthy job reads (bottom to top)
`PENDING -> STARTING -> RUNNING -> SUCCEEDED`. Things to notice:

- Many `RECOVERING` events with the same `reason` mean a persistent
  problem (bad node, OOM, quota); the controller keeps retrying but will
  not fix it.
- A long gap between `STARTING` and `RUNNING` is provisioning time; add
  `--cluster-events` to see what the cluster was waiting on.
- `code` is a short machine-readable tag when the controller knows one;
  `reason` is the human text.
