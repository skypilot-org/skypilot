"""Scheduler for managed jobs.

Once managed jobs are submitted via submit_job, the scheduler is responsible for
the business logic of deciding when they are allowed to start, and choosing the
right one to start. The scheduler will also schedule jobs that are already live
but waiting to launch a new task or recover.

The scheduler is not its own process - instead, maybe_schedule_next_jobs() can
be called from any code running on the managed jobs controller instance to
trigger scheduling of new jobs if possible. This function should be called
immediately after any state change that could result in jobs newly being able to
be scheduled. If the job is running in a pool, the scheduler will only schedule
jobs for the same pool, because the resources limitations are per-pool (see the
following section for more details).

The scheduling logic limits #running jobs according to three limits:
1. The number of jobs that can be launching (that is, STARTING or RECOVERING) at
   once, based on the number of CPUs. This the most compute-intensive part of
   the job lifecycle, which is why we have an additional limit.
   See sky/utils/controller_utils.py::_get_launch_parallelism.
2. The number of jobs that can be running at any given time, based on the amount
   of memory. Since the job controller is doing very little once a job starts
   (just checking its status periodically), the most significant resource it
   consumes is memory.
   See sky/utils/controller_utils.py::_get_job_parallelism.
3. The number of jobs that can be running in a pool at any given time, based on
   the number of ready workers in the pool. (See _can_start_new_job.)

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
import os
import sys
import time
from typing import Optional

import filelock

from sky import exceptions
from sky import sky_logging
from sky.jobs import constants as managed_job_constants
from sky.jobs import state
from sky.serve import serve_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import subprocess_utils

logger = sky_logging.init_logger('sky.jobs.controller')

_ALIVE_JOB_LAUNCH_WAIT_INTERVAL = 0.5


def _start_controller(job_id: int, dag_yaml_path: str, env_file_path: str,
                      pool: Optional[str]) -> None:
    activate_python_env_cmd = (f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV};')
    source_environment_cmd = (f'source {env_file_path};'
                              if env_file_path else '')
    maybe_pool_arg = (f'--pool {pool}' if pool is not None else '')
    run_controller_cmd = (
        f'{sys.executable} -u -m sky.jobs.controller '
        f'{dag_yaml_path} --job-id {job_id} {maybe_pool_arg};')

    # If the command line here is changed, please also update
    # utils._controller_process_alive. The substring `--job-id X`
    # should be in the command.
    run_cmd = (f'{activate_python_env_cmd}'
               f'{source_environment_cmd}'
               f'{run_controller_cmd}')

    logs_dir = os.path.expanduser(
        managed_job_constants.JOBS_CONTROLLER_LOGS_DIR)
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f'{job_id}.log')

    pid = subprocess_utils.launch_new_process_tree(run_cmd, log_output=log_path)
    state.set_job_controller_pid(job_id, pid)

    logger.debug(f'Job {job_id} started with pid {pid}')


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

    After adding the pool support, this function will be called in a per-pool
    basis. We employ resources limitation for each pool given the number of
    ready workers in the pool. Each pool will have its own scheduler queue,
    indicating by the argument `pool`. Finished job in pool 1 will only trigger
    another jobs in pool 1, but the job in pool 2 will still be waiting. When
    the `pool` argument is None, it schedules a job regardless of the pool.
    """
    try:
        # We must use a global lock rather than a per-job lock to ensure correct
        # parallelism control. If we cannot obtain the lock, exit immediately.
        # The current lock holder is expected to launch any jobs it can before
        # releasing the lock.
        with filelock.FileLock(controller_utils.get_resources_lock_path(),
                               blocking=False):
            while True:
                maybe_next_job = state.get_waiting_job()
                if maybe_next_job is None:
                    # Nothing left to start, break from scheduling loop
                    break
                actual_pool = maybe_next_job['pool']

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
                    if not controller_utils.can_provision():
                        # Can't schedule anything, break from scheduling loop.
                        break
                elif current_state == state.ManagedJobScheduleState.WAITING:
                    if not _can_start_new_job(actual_pool):
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
                    env_file_path = maybe_next_job['env_file_path']

                    _start_controller(job_id, dag_yaml_path, env_file_path,
                                      actual_pool)

    except filelock.Timeout:
        # If we can't get the lock, just exit. The process holding the lock
        # should launch any pending jobs.
        pass


def submit_job(job_id: int, dag_yaml_path: str, original_user_yaml_path: str,
               env_file_path: str, priority: int, pool: Optional[str]) -> None:
    """Submit an existing job to the scheduler.

    This should be called after a job is created in the `spot` table as
    PENDING. It will tell the scheduler to try and start the job controller, if
    there are resources available. It may block to acquire the lock, so it
    should not be on the critical path for `sky jobs launch -d`.

    The user hash should be set (e.g. via SKYPILOT_USER_ID) before calling this.
    """
    with filelock.FileLock(controller_utils.get_resources_lock_path()):
        is_resume = state.scheduler_set_waiting(job_id, dag_yaml_path,
                                                original_user_yaml_path,
                                                env_file_path,
                                                common_utils.get_user_hash(),
                                                priority)
    if is_resume:
        _start_controller(job_id, dag_yaml_path, env_file_path, pool)
    else:
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
    pool = state.get_pool_from_job_id(job_id)
    # For pool, since there is no execution.launch, we don't need to have all
    # the ALIVE_WAITING state. The state transition will be
    # WAITING -> ALIVE -> DONE without any intermediate transitions.
    if pool is not None:
        yield
        return

    # If we're already in LAUNCHING schedule_state, we don't need to wait.
    # This may be the case for the first launch of a job.
    if (state.get_job_schedule_state(job_id) !=
            state.ManagedJobScheduleState.LAUNCHING):
        # Since we aren't LAUNCHING, we need to wait to be scheduled.
        _set_alive_waiting(job_id)

        while (state.get_job_schedule_state(job_id) !=
               state.ManagedJobScheduleState.LAUNCHING):
            time.sleep(_ALIVE_JOB_LAUNCH_WAIT_INTERVAL)

    try:
        yield
    except exceptions.NoClusterLaunchedError:
        # NoClusterLaunchedError is indicates that the job is in retry backoff.
        # We should transition to ALIVE_BACKOFF instead of ALIVE.
        with filelock.FileLock(controller_utils.get_resources_lock_path()):
            state.scheduler_set_alive_backoff(job_id)
        raise
    else:
        with filelock.FileLock(controller_utils.get_resources_lock_path()):
            state.scheduler_set_alive(job_id)
    finally:
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

    with filelock.FileLock(controller_utils.get_resources_lock_path()):
        state.scheduler_set_done(job_id, idempotent)
    maybe_schedule_next_jobs()


def _set_alive_waiting(job_id: int) -> None:
    """Should use wait_until_launch_okay() to transition to this state."""
    with filelock.FileLock(controller_utils.get_resources_lock_path()):
        state.scheduler_set_alive_waiting(job_id)
    maybe_schedule_next_jobs()


def _can_start_new_job(pool: Optional[str]) -> bool:
    # Check basic resource limits
    # Pool jobs don't need to provision resources, so we skip the check.
    if not ((controller_utils.can_provision() or pool is not None) and
            controller_utils.can_start_new_process()):
        return False

    # Check if there are available workers in the pool
    if pool is not None:
        alive_jobs_in_pool = state.get_num_alive_jobs(pool)
        if alive_jobs_in_pool >= len(serve_utils.get_ready_replicas(pool)):
            logger.debug(f'No READY workers available in pool {pool}')
            return False

    return True


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('dag_yaml',
                        type=str,
                        help='The path to the user job yaml file.')
    parser.add_argument('--user-yaml-path',
                        type=str,
                        help='The path to the original user job yaml file.')
    parser.add_argument('--job-id',
                        required=True,
                        type=int,
                        help='Job id for the controller job.')
    parser.add_argument('--env-file',
                        type=str,
                        help='The path to the controller env file.')
    parser.add_argument('--pool',
                        type=str,
                        required=False,
                        default=None,
                        help='The pool to use for the controller job.')
    parser.add_argument(
        '--priority',
        type=int,
        default=constants.DEFAULT_PRIORITY,
        help=
        f'Job priority ({constants.MIN_PRIORITY} to {constants.MAX_PRIORITY}).'
        f' Default: {constants.DEFAULT_PRIORITY}.')
    args = parser.parse_args()
    submit_job(args.job_id, args.dag_yaml, args.user_yaml_path, args.env_file,
               args.priority, args.pool)
