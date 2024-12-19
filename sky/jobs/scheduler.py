"""Scheduler for managed jobs.

Once managed jobs are added as PENDING to the `spot` table, the scheduler is
responsible for the business logic of deciding when they are allowed to start,
and choosing the right one to start.

The scheduler is not its own process - instead, schedule_step() can be called
from any code running on the managed jobs controller to trigger scheduling of
new jobs if possible. This function should be called immediately after any state
change that could result in new jobs being able to start.

The scheduling logic limits the number of running jobs according to two limits:
1. The number of jobs that can be launching (that is, STARTING or RECOVERING) at
   once, based on the number of CPUs. (See _get_launch_parallelism.) This the
   most compute-intensive part of the job lifecycle, which is why we have an
   additional limit.
2. The number of jobs that can be running at any given time, based on the amount
   of memory. (See _get_job_parallelism.) Since the job controller is doing very
   little once a job starts (just checking its status periodically), the most
   significant resource it consumes is memory.

There are two ways to interact with the scheduler:
- Any code that could result in new jobs being able to start (that is, it
  reduces the number of jobs counting towards one of the above limits) should
  call schedule_step(), which will best-effort attempt to schedule new jobs.
- If a running job need to relaunch (recover), it should use schedule_recovery()
  to obtain a "slot" in the number of allowed starting jobs.

Since the scheduling state is determined by the state of jobs in the `spot`
table, we must sychronize all scheduling logic with a global lock. A per-job
lock would be insufficient, since schedule_step() could race with a job
controller trying to start recovery, "double-spending" the open slot.
"""

import contextlib
import os
import shlex
import subprocess
import time
from typing import Optional, Tuple

import filelock
import psutil

from sky.jobs import state, constants as managed_job_constants
from sky.skylet import constants
from sky.utils import subprocess_utils

# The _MANAGED_JOB_SUBMISSION_LOCK should be held whenever a job transitions to
# STARTING or RECOVERING, so that we can ensure correct parallelism control.
_MANAGED_JOB_SUBMISSION_LOCK = '~/.sky/locks/managed_job_submission.lock'
_ACTIVE_JOB_LAUNCH_WAIT_INTERVAL = 0.5


def schedule_step() -> None:
    """Determine if any jobs can be launched, and if so, launch them.

    This function starts new job controllers for PENDING jobs on a best-effort
    basis. That is, if we can start any jobs, we will, but if not, we will exit
    (almost) immediately. It's expected that if some PENDING jobs cannot be
    started now (either because the lock is held, or because there are not
    enough resources), another call to schedule_step() will be made whenever
    that situation is resolved. (If the lock is held, the lock holder should
    start the jobs. If there aren't enough resources, the next controller to
    exit and free up resources should call schedule_step().)

    This uses subprocess_utils.launch_new_process_tree() to start the controller
    processes, which should be safe to call from pretty much any code running on
    the jobs controller. New job controller processes will be detached from the
    current process and there will not be a parent/child relationship - see
    launch_new_process_tree for more.
    """
    try:
        # We must use a global lock rather than a per-job lock to ensure correct
        # parallelism control.
        # The lock is not held while submitting jobs, so we use timeout=1 as a
        # best-effort protection against the race between a previous
        # schedule_step() releasing the lock and a job submission. Since we call
        # schedule_step() after submitting the job this should capture
        # essentially all cases.
        # (In the worst case, the skylet event should schedule the job.)
        with filelock.FileLock(os.path.expanduser(_MANAGED_JOB_SUBMISSION_LOCK),
                               timeout=1):
            while _can_schedule():
                maybe_next_job = _get_next_job_to_start()
                if maybe_next_job is None:
                    # Nothing left to schedule, break from scheduling loop
                    break

                managed_job_id, dag_yaml_path = maybe_next_job
                run_cmd = (
                    f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV};'
                    f'python -u -m sky.jobs.controller {dag_yaml_path} --job-id {managed_job_id}'
                )

                state.set_submitted(
                    job_id=managed_job_id,
                    # schedule_step() only looks at the first task of each job.
                    task_id=0,
                    # We must call set_submitted now so that this job is counted
                    # as launching by future scheduler runs, but we don't have
                    # the callback_func here. We will call set_submitted again
                    # in the jobs controller, which will call the callback_func.
                    callback_func=lambda _: None)

                logs_dir = os.path.expanduser(
                    managed_job_constants.JOBS_CONTROLLER_LOGS_DIR)
                os.makedirs(logs_dir, exist_ok=True)
                log_path = os.path.join(logs_dir, f'{managed_job_id}.log')

                pid = subprocess_utils.launch_new_process_tree(
                    run_cmd, log_output=log_path)
                state.set_job_controller_pid(managed_job_id, pid)

    except filelock.Timeout:
        # If we can't get the lock, just exit. The process holding the lock
        # should launch any pending jobs.
        pass


@contextlib.contextmanager
def schedule_active_job_launch(is_running: bool):
    """Block until we can trigger a launch as part of an ongoing job.

    schedule_step() will only schedule the first launch of a job. There are two
    scenarios where we may need to call sky.launch again during the course of a
    job controller:
    - for tasks after the first task
    - for recovery

    We must hold the lock before transitioning to STARTING or RECOVERING, for
    these cases, and we have to make sure there are actually available
    resources. So, this context manager will block until we have the launch and
    there are available resources to schedule.

    The context manager should NOT be held for the actual sky.launch - we just
    need to hold it while we transition the job state (to STARTING or
    RECOVERING).

    This function does NOT guarantee any kind of ordering if multiple processes
    call it in parallel. This is why we do not use it for the first task on each
    job.
    """

    def _ready_to_start():
        # If this is being run as part of a job that is already RUNNING, ignore
        # the job parallelism. Comparing to state.get_num_alive_jobs() - 1 is
        # deadlock-prone if we somehow have more than the max number of jobs
        # running (e.g. if 2 jobs are running and _get_job_parallelism() == 1).
        if not is_running and state.get_num_alive_jobs(
        ) >= _get_job_parallelism():
            return False
        if state.get_num_launching_jobs() >= _get_launch_parallelism():
            return False
        return True

    # Ideally, we should prioritize launches that are part of ongoing jobs over
    # scheduling new jobs. Therefore we grab the lock and wait until a slot
    # opens. There is only one lock, so there is no deadlock potential from that
    # perspective. We could deadlock if this is called as part of a job that is
    # currently STARTING, so don't do that. This could spin forever if jobs get
    # stuck as STARTING or RECOVERING, but the same risk exists for the normal
    # scheduler.
    with filelock.FileLock(os.path.expanduser(_MANAGED_JOB_SUBMISSION_LOCK)):
        # Only check launch parallelism, since this should be called as part of
        # a job that is already RUNNING.
        while not _ready_to_start():
            time.sleep(_ACTIVE_JOB_LAUNCH_WAIT_INTERVAL)
        # We can launch now. yield to user code, which should update the state
        # of the job. DON'T ACTUALLY LAUNCH HERE, WE'RE STILL HOLDING THE LOCK!
        yield

    # Release the lock. Wait for more than the lock poll_interval (0.05) in case
    # other jobs are waiting to recover - they should get the lock first.
    time.sleep(0.1)

    # Since we were holding the lock, other schedule_step() calls may have early
    # exited. It's up to us to spawn those controllers.
    schedule_step()


def _get_job_parallelism() -> int:
    # Assume a running job uses 350MB memory.
    # We observe 230-300 in practice.
    job_memory = 350 * 1024 * 1024
    return max(psutil.virtual_memory().total // job_memory, 1)


def _get_launch_parallelism() -> int:
    cpus = os.cpu_count()
    return cpus * 4 if cpus is not None else 1


def _can_schedule() -> bool:
    launching_jobs = state.get_num_launching_jobs()
    alive_jobs = state.get_num_alive_jobs()
    print(launching_jobs, alive_jobs)
    print(_get_launch_parallelism(), _get_job_parallelism())
    return launching_jobs < _get_launch_parallelism(
    ) and alive_jobs < _get_job_parallelism()


def _get_next_job_to_start() -> Optional[Tuple[int, str]]:
    """Returns tuple of job_id, yaml path"""
    return state.get_first_pending_job_id_and_yaml()


#     def _get_pending_job_ids(self) -> List[int]:
#         """Returns the job ids in the pending jobs table

#         The information contains job_id, run command, submit time,
#         creation time.
#         """
#         raise NotImplementedError

if __name__ == '__main__':
    print("main")
    schedule_step()
