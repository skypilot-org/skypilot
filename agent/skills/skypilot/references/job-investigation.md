# Investigating a Managed Job

How to answer "what is my job doing and why" with as few commands as
possible. Every command here supports `-o json`; prefer it over parsing
table output.

## Which command answers which question

| Question | Command | Look at |
|----------|---------|---------|
| What state is job N in, and why? | `sky jobs queue -v -o json` | `status`, `details`, `failure_reason`, `schedule_state`, `recovery_count` |
| Why is job N still provisioning / pending on the cluster? | `sky jobs queue -v -o json` | `details`, when it starts with `Launching (` |
| How long has job N been at this? | `sky jobs queue -v -o json` | `submitted_at`, and the duration columns in the table |
| What did job N print recently? | `sky jobs logs N --tail 100 --no-follow` | stdout/stderr of the task |
| What did the controller do (provision, recover, retry)? | `sky jobs logs N --controller --no-follow` | controller log |
| Wait until job N finishes | `sky jobs logs N` (blocks) | exit when the job ends |

Do not poll with `sleep` + `sky jobs queue`. Stream logs to block, or
fetch `--tail` once after the fact.

`details` is the current explanation, not a history. For the sequence of
transitions a job went through — and, on Slurm, how long the allocation
waited and on what — the timeline lives behind the `jobs.events` API and is
surfaced by the enterprise client.

## Reading `details`

`details` in `sky jobs queue -v -o json` is the one-line explanation of the
current state. Common shapes:

| `details` | Meaning | Next step |
|-----------|---------|-----------|
| `Waiting for other jobs to launch` | Controller is at its concurrent-launch limit | Nothing to fix; the duration columns say how long it has waited |
| `Waiting for higher priority jobs to launch` | A higher-priority managed job is ahead | Raise `priority` in the task YAML if this job matters more |
| `In backoff, waiting for resources` | Provisioning failed and is retrying | `sky jobs logs N --controller --no-follow` shows the last failure |
| `Recovering: <reason>` | Cluster was lost or the task crashed (e.g. OOMKilled) | Fix the cause; recovery is automatic |
| `Failure: <reason>` | Terminal failure | Read the reason; `sky jobs logs N --no-follow` for the task output |
| `Cancellation requested by ...` | Someone cancelled it | The requester and request ID are in the text |
| `Launching (pending: <reason>; partition: <p>)` | Job is STARTING and Slurm has not allocated nodes yet | See "Slurm pending reasons" below |
| `Launching (nodes allocated; Slurm job <id> on <cluster>)` | The queue wait is over; the job is still bootstrapping | Nothing to fix. That id is what `sacct -j <id>` takes if you have login-node access |

## Slurm pending reasons

While a managed job's Slurm allocation is queued, `details` carries the
`squeue` reason and the partition it is queued in — the partition matters
because the same reason means different things in different partitions, and
by the time anyone looks the allocation may be gone. The common codes:

| Reason | Category | What to do |
|--------|----------|------------|
| `Resources`, `Priority`, `ReqNodeNotAvail` | No free capacity in the partition | Wait, or resubmit to another partition via `config.slurm` |
| `QOSGrpGRES`, `QOSMax*`, `AssocGrp*` | A QoS / account quota is full | Wait for the group's jobs to finish, or use a different QoS/partition |
| `Dependency`, `DependencyNeverSatisfied` | Waiting on another Slurm job; `NeverSatisfied` will pend forever | Check the dependency job; cancel and resubmit if it failed |
| `JobHeldUser`, `JobHeldAdmin`, `launch failed requeued held` | Job is held | Ask the admin to `scontrol release`, or cancel and relaunch |
| `BeginTime` | Scheduled start time not reached | Nothing to fix |

The reason is recorded each time it changes, so a job that moved from
`Resources` to `Priority` and back leaves all three behind it — `details`
shows only the latest.

## Recognising a stuck job

- Repeated `Recovering: <same reason>` means a persistent problem (bad node,
  OOM, quota). The controller keeps retrying and will not fix it; read
  `sky jobs logs N --controller --no-follow`.
- A long gap between STARTING and RUNNING is provisioning time. On Slurm
  that is queue time, and `details` says what the queue is waiting on.
- `failure_reason` distinguishes the user's program failing from an
  infrastructure failure; do not report the latter as a code bug.
