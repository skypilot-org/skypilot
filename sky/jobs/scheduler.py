"""Scheduler for managed jobs.

Once managed jobs are submitted via submit_job, the scheduler is responsible for
the business logic of deciding when they are allowed to start, and choosing the
right one to start. The scheduler will also schedule jobs that are already live
but waiting to launch a new task or recover.

The scheduler is not its own process - instead, maybe_schedule_next_jobs() can
be called from any code running on the managed jobs controller instance to
trigger scheduling of new jobs if possible. This function should be called
immediately after any state change that could result in jobs newly being able to
be scheduled.

The scheduling logic limits the number of running jobs according to two limits:
1. The number of jobs that can be launching (that is, STARTING or RECOVERING) at
   once, based on the number of CPUs. (See _get_launch_parallelism.) This the
   most compute-intensive part of the job lifecycle, which is why we have an
   additional limit.
2. The number of jobs that can be running at any given time, based on the amount
   of memory. (See _get_job_parallelism.) Since the job controller is doing very
   little once a job starts (just checking its status periodically), the most
   significant resource it consumes is memory.

The state of the scheduler is entirely determined by the schedule_state column
of all the jobs in the job_info table. This column should only be modified via
the functions defined in this file. We will always hold the lock while modifying
this state. See state.ManagedJobScheduleState.

Nomenclature:
- job: same as managed job (may include multiple tasks)
- launch/launching: launching a cluster (sky.launch) as part of a job
- start/run: create the job controller process for a job
- schedule: transition a job to the LAUNCHING state, whether a new job or a job
  that is already alive
- alive: a job controller exists (includes multiple schedule_states: ALIVE,
  ALIVE_WAITING, LAUNCHING)
"""

from argparse import ArgumentParser
import contextlib
from functools import lru_cache
import os
import time
import typing

import filelock

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.jobs import constants as managed_job_constants
from sky.jobs import state
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import subprocess_utils

if typing.TYPE_CHECKING:
    import psutil
else:
    psutil = adaptors_common.LazyImport('psutil')

logger = sky_logging.init_logger('sky.jobs.controller')

# The _MANAGED_JOB_SCHEDULER_LOCK should be held whenever we are checking the
# parallelism control or updating the schedule_state of any job.
# Any code that takes this lock must conclude by calling
# maybe_schedule_next_jobs.
_MANAGED_JOB_SCHEDULER_LOCK = '~/.sky/locks/managed_job_scheduler.lock'
_ALIVE_JOB_LAUNCH_WAIT_INTERVAL = 0.5

# Based on testing, assume a running job uses 350MB memory.
JOB_MEMORY_MB = 350
# Past 2000 simultaneous jobs, we become unstable.
# See https://github.com/skypilot-org/skypilot/issues/4649.
MAX_JOB_LIMIT = 2000
# Number of ongoing launches launches allowed per CPU.
LAUNCHES_PER_CPU = 4


@lru_cache(maxsize=1)
def _get_lock_path() -> str:
    path = os.path.expanduser(_MANAGED_JOB_SCHEDULER_LOCK)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def maybe_schedule_next_jobs() -> None:
    """Determine if any managed jobs can be scheduled, and if so, schedule them.

    Here, "schedule" means to select job that is waiting, and allow it to
    proceed. It does NOT mean to submit a job to the scheduler.

    For newly submitted jobs, scheduling means updating the state of the jobs,
    and starting the job controller process. For jobs that are already alive but
    are waiting to launch a new task or recover, just update the state of the
    job to indicate that the launch can proceed.

    This function transitions jobs into LAUNCHING on a best-effort basis. That
    is, if we can start any jobs, we will, but if not, we will exit (almost)
    immediately. It's expected that if some WAITING or ALIVE_WAITING jobs cannot
    be started now (either because the lock is held, or because there are not
    enough resources), another call to this function will be made whenever that
    situation is resolved. (If the lock is held, the lock holder should start
    the jobs. If there aren't enough resources, the next controller to exit and
    free up resources should start the jobs.)

    If this function obtains the lock, it will launch as many jobs as possible
    before releasing the lock. This is what allows other calls to exit
    immediately if the lock is held, while ensuring that all jobs are started as
    soon as possible.

    This uses subprocess_utils.launch_new_process_tree() to start the controller
    processes, which should be safe to call from pretty much any code running on
    the jobs controller instance. New job controller processes will be detached
    from the current process and there will not be a parent/child relationship.
    See launch_new_process_tree for more.
    """
    try:
        # We must use a global lock rather than a per-job lock to ensure correct
        # parallelism control. If we cannot obtain the lock, exit immediately.
        # The current lock holder is expected to launch any jobs it can before
        # releasing the lock.
        with filelock.FileLock(_get_lock_path(), blocking=False):
            while True:
                maybe_next_job = state.get_waiting_job()
                if maybe_next_job is None:
                    # Nothing left to start, break from scheduling loop
                    break

                current_state = maybe_next_job['schedule_state']

                assert current_state in (
                    state.ManagedJobScheduleState.ALIVE_WAITING,
                    state.ManagedJobScheduleState.WAITING), maybe_next_job

                # Note: we expect to get ALIVE_WAITING jobs before WAITING jobs,
                # since they will have been submitted and therefore started
                # first. The requirements to launch in an alive job are more
                # lenient, so there is no way that we wouldn't be able to launch
                # an ALIVE_WAITING job, but we would be able to launch a WAITING
                # job.
                if current_state == state.ManagedJobScheduleState.ALIVE_WAITING:
                    if not _can_lauch_in_alive_job():
                        # Can't schedule anything, break from scheduling loop.
                        break
                elif current_state == state.ManagedJobScheduleState.WAITING:
                    if not _can_start_new_job():
                        # Can't schedule anything, break from scheduling loop.
                        break

                logger.debug(f'Scheduling job {maybe_next_job["job_id"]}')
                state.scheduler_set_launching(maybe_next_job['job_id'],
                                              current_state)

                if current_state == state.ManagedJobScheduleState.WAITING:
                    # The job controller has not been started yet. We must start
                    # it.

                    job_id = maybe_next_job['job_id']
                    dag_yaml_path = maybe_next_job['dag_yaml_path']

                    activate_python_env_cmd = (
                        f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV};')
                    env_file = maybe_next_job['env_file_path']
                    source_environment_cmd = (f'source {env_file};'
                                              if env_file else '')
                    run_controller_cmd = ('python -u -m sky.jobs.controller '
                                          f'{dag_yaml_path} --job-id {job_id};')

                    # If the command line here is changed, please also update
                    # utils._controller_process_alive. `--job-id X` should be at
                    # the end.
                    run_cmd = (f'{activate_python_env_cmd}'
                               f'{source_environment_cmd}'
                               f'{run_controller_cmd}')

                    logs_dir = os.path.expanduser(
                        managed_job_constants.JOBS_CONTROLLER_LOGS_DIR)
                    os.makedirs(logs_dir, exist_ok=True)
                    log_path = os.path.join(logs_dir, f'{job_id}.log')

                    pid = subprocess_utils.launch_new_process_tree(
                        run_cmd, log_output=log_path)
                    state.set_job_controller_pid(job_id, pid)

                    logger.debug(f'Job {job_id} started with pid {pid}')

    except filelock.Timeout:
        # If we can't get the lock, just exit. The process holding the lock
        # should launch any pending jobs.
        pass


def submit_job(job_id: int, dag_yaml_path: str, env_file_path: str) -> None:
    """Submit an existing job to the scheduler.

    This should be called after a job is created in the `spot` table as
    PENDING. It will tell the scheduler to try and start the job controller, if
    there are resources available. It may block to acquire the lock, so it
    should not be on the critical path for `sky jobs launch -d`.

    The user hash should be set (e.g. via SKYPILOT_USER_ID) before calling this.
    """
    with filelock.FileLock(_get_lock_path()):
        state.scheduler_set_waiting(job_id, dag_yaml_path, env_file_path,
                                    common_utils.get_user_hash())
    maybe_schedule_next_jobs()


@contextlib.contextmanager
def scheduled_launch(job_id: int):
    """Launch as part of an ongoing job.

    A newly started job will already be LAUNCHING, and this will immediately
    enter the context.

    If a job is ongoing (ALIVE schedule_state), there are two scenarios where we
    may need to call sky.launch again during the course of a job controller:
    - for tasks after the first task
    - for recovery

    This function will mark the job as ALIVE_WAITING, which indicates to the
    scheduler that it wants to transition back to LAUNCHING. Then, it will wait
    until the scheduler transitions the job state, before entering the context.

    On exiting the context, the job will transition to ALIVE.

    This should only be used within the job controller for the given job_id. If
    multiple uses of this context are nested, behavior is undefined. Don't do
    that.
    """

    # If we're already in LAUNCHING schedule_state, we don't need to wait.
    # This may be the case for the first launch of a job.
    if (state.get_job_schedule_state(job_id) !=
            state.ManagedJobScheduleState.LAUNCHING):
        # Since we aren't LAUNCHING, we need to wait to be scheduled.
        _set_alive_waiting(job_id)

        while (state.get_job_schedule_state(job_id) !=
               state.ManagedJobScheduleState.LAUNCHING):
            time.sleep(_ALIVE_JOB_LAUNCH_WAIT_INTERVAL)

    yield

    with filelock.FileLock(_get_lock_path()):
        state.scheduler_set_alive(job_id)
    maybe_schedule_next_jobs()


def job_done(job_id: int, idempotent: bool = False) -> None:
    """Transition a job to DONE.

    If idempotent is True, this will not raise an error if the job is already
    DONE.

    The job could be in any terminal ManagedJobStatus. However, once DONE, it
    should never transition back to another state.
    """
    if idempotent and (state.get_job_schedule_state(job_id)
                       == state.ManagedJobScheduleState.DONE):
        return

    with filelock.FileLock(_get_lock_path()):
        state.scheduler_set_done(job_id, idempotent)
    maybe_schedule_next_jobs()


def _set_alive_waiting(job_id: int) -> None:
    """Should use wait_until_launch_okay() to transition to this state."""
    with filelock.FileLock(_get_lock_path()):
        state.scheduler_set_alive_waiting(job_id)
    maybe_schedule_next_jobs()


def _get_job_parallelism() -> int:
    job_memory = JOB_MEMORY_MB * 1024 * 1024

    job_limit = min(psutil.virtual_memory().total // job_memory, MAX_JOB_LIMIT)

    return max(job_limit, 1)


def _get_launch_parallelism() -> int:
    cpus = os.cpu_count()
    return cpus * LAUNCHES_PER_CPU if cpus is not None else 1


def _can_start_new_job() -> bool:
    launching_jobs = state.get_num_launching_jobs()
    alive_jobs = state.get_num_alive_jobs()
    return launching_jobs < _get_launch_parallelism(
    ) and alive_jobs < _get_job_parallelism()


def _can_lauch_in_alive_job() -> bool:
    launching_jobs = state.get_num_launching_jobs()
    return launching_jobs < _get_launch_parallelism()


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('dag_yaml',
                        type=str,
                        help='The path to the user job yaml file.')
    parser.add_argument('--job-id',
                        required=True,
                        type=int,
                        help='Job id for the controller job.')
    parser.add_argument('--env-file',
                        type=str,
                        help='The path to the controller env file.')
    args = parser.parse_args()
    submit_job(args.job_id, args.dag_yaml, args.env_file)
