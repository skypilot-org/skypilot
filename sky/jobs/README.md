# SkyPilot Managed Jobs

This module is used for running and managing user jobs, which automatically recovers failed jobs from spot preemptions and/or machine failures.

## Concepts

- Task: A task (sky.Task) is a unit of work. SkyPilot will launch a cluster to run the task, automatically recover the task from preemptions, and terminate the cluster when the task is done.
- Job: A job in the context of SkyPilot managed jobs, is equivalent to a SkyPilot DAG (sky.Dag). A job is a collection of tasks that are executed in a specific order based on the dependencies between the tasks. Each controller process will be in charge of the whole lifecycle of a job.

Note that for singleton (1-task) jobs, we will use the term "task" and "job" interchangeably.

A job of n tasks (experimental; we support a pipeline of such tasks only): the job has its own job ID and name, and the tasks have their own task IDs (0, 1, ..., n-1) and names.


## Architecture

![Architecture](../../docs/source/images/managed-jobs-arch.png)
<!-- Raw file: https://docs.google.com/presentation/d/1AoFewsxm7jEsnFYyovyuTqKZs8W59qD9sNcM7Wcic4I/edit#slide=id.p -->

### State diagrams

There are two "state" notions for managed jobs:
- The managed job "status" reflects the user-facing status of the job.
- The "schedule state" is an internal-only state that is used by the scheduler to track controller processes and limit parallelism.

There is no consistent mapping between these two state notions. See comments/docstrings in sky/jobs/state.py.

Managed job status follows the following state diagram:

![Managed job status state diagram](../../docs/repo-images/managed-job-status-diagram.png).
<!-- PlantUML source: (NOTE: remove the \ from "-\->". The \ is there to prevent exiting the HTML comment.)
@startuml

state "All States" as AllStates {
    state "Inner Controller Loop" as InnerLoop {
        PENDING -> STARTING : scheduled
        STARTING -> PENDING : backoff
        STARTING -\-> RUNNING
        PENDING -\-> RUNNING : empty job\nshortcut
        RUNNING -> RECOVERING : preempted
        state "PENDING" as PENDING_RECOVERY : during recovery
        RECOVERING -> PENDING_RECOVERY : backoff
        PENDING_RECOVERY -left> RECOVERING : rescheduled
        RECOVERING -> RUNNING : recovered
    }

    [*] -\-> PENDING: job created

    state "Normal Job Exit" as Terminal {
        state FAILED_NO_RESOURCE : launch failed:\ninsufficient cloud resources available
        state FAILED_PRECHECKS : launch failed:\ne.g. no creds or invalid spec
        state SUCCEEDED
        state FAILED : user program failed
        state FAILED_SETUP : user setup failed
        STARTING -\-> FAILED_NO_RESOURCE
        RECOVERING -\-> FAILED_NO_RESOURCE
        STARTING -\-> FAILED_PRECHECKS
        RECOVERING -\-> FAILED_PRECHECKS
        RUNNING -\-> SUCCEEDED
        RUNNING -\-> FAILED
        RUNNING -\-> FAILED_SETUP
    }

    InnerLoop -\-> CANCELLING : user cancel request
    RUNNING -[dotted]-> RUNNING : controller error\nreattach monitoring
    InnerLoop -[dotted]> RECOVERING : HA controller recovery\nor unverified emergency
    CANCELLING -> CANCELLED : cluster\ncleaned up
    CANCELLING -[dotted]-> Terminal: job could complete\nbefore we can cancel
}

AllStates -\-> FAILED_CONTROLLER : controller failed or\nunexpected state

@enduml
-->

Note that ANY status can legally transition to FAILED_CONTROLLER, even another terminal status. This is because we can have a controller failure or other problem after the job has already exited, e.g. when cleaning up the cluster.

RECOVERING covers three causes, recorded as a RecoverySource on the RECOVERING job event (job_events.recovery_source) so consumers can tell them apart: (1) FAILURE — a cluster preemption/failure or user-code failure; (2) EMERGENCY — the controller hit an unexpected internal error but could not prove that its latest task is still RUNNING, so it retries managing the job in place, bounded by a per-job budget with exponential backoff (see EMERGENCY_RECOVERY_* in sky/jobs/constants.py); when that budget is exhausted the job goes to FAILED_CONTROLLER with full cleanup. An unexpected controller error with a durably RUNNING latest task instead reloads controller state and reattaches monitoring after the same initial backoff, without a RECOVERING transition, cancellation, teardown, or relaunch. A bounded emergency retry tears down and relaunches its task cluster; (3) RESTART — the controller process restarted (upgrade/rollout; the resume path is historically called "HA recovery" but runs on any controller restart) and forces recovery on resume. Independently, a failed remote-job status lookup while the cluster is UP is degraded monitoring: it retries without a RECOVERING transition until a lookup proves a terminal remote process or the cluster is non-UP. Separately from these per-occurrence causes, spot.recovering_from_failure tracks whether the currently open episode carries failure credit — TRUE iff a genuine preemption/failure is involved; a system-driven interruption (emergency or restart) of a failure recovery neither grants nor erases the credit. When the episode completes, recovery_count is incremented only for credited episodes (NULL, from rows written before the column existed, counts as credited), so the user-visible "Recoveries" count reflects genuine failure recoveries rather than system-driven ones.

The schedule_state follows a simpler diagram:

![Managed job schedule_state diagram](../../docs/repo-images/managed-job-schedule-state-diagram.png).
<!-- PlantUML source: (NOTE: remove the \ from "-\->". The \ is there to prevent exiting the HTML comment.)
@startuml

INACTIVE -\-> WAITING : submitted
WAITING -\-> LAUNCHING : scheduled

state "Controller process alive (pid set)" as ControllerProc {
    state "LAUNCHING" as LAUNCHING_PID : (pid set)
    state ALIVE_BACKOFF : (waiting for resources)
    LAUNCHING -> LAUNCHING_PID
    LAUNCHING_PID -> ALIVE : launch\nfinished
    LAUNCHING_PID -up-> ALIVE_BACKOFF
    ALIVE_BACKOFF -> ALIVE_WAITING
    ALIVE -up-> ALIVE_WAITING : recover or\nnew task
    ALIVE_WAITING -down-> LAUNCHING_PID : scheduled
}

ALIVE -> DONE : controller\nprocess exits
ControllerProc -[dotted]down-> DONE : controlled process\ndied unexpectedly

@enduml
-->
