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
import os
import sys
import time
import typing

import filelock

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.jobs import constants as managed_job_constants
from sky.jobs import state
from sky.server import config as server_config
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
JOB_CONTROLLER_PID_LOCK = os.path.expanduser(
    '~/.sky/locks/job_controller_pid.lock')
_ALIVE_JOB_LAUNCH_WAIT_INTERVAL = 0.5

JOB_CONTROLLER_PID_PATH = os.path.expanduser('~/.sky/job_controller_pid')
JOB_CONTROLLER_ENV_PATH = os.path.expanduser('~/.sky/job_controller_env')

# Based on testing, each worker takes around 200-300MB memory. Keeping it
# higher to be safe.
JOB_MEMORY_MB = 400
# Number of ongoing launches launches allowed per worker. Can probably be
# increased a bit to around 16 but keeping it lower to just to be safe
LAUNCHES_PER_WORKER = 8
# this can probably be increased to around 300-400 but keeping it lower to just
# to be safe
JOBS_PER_WORKER = 200

# keep 1GB reserved after the controllers
MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB = 1024

# Maximum values for above constants. There will start to be lagging issues
# at these numbers already.
# JOB_MEMORY_MB = 200
# LAUNCHES_PER_WORKER = 16
# JOBS_PER_WORKER = 400


def get_number_of_controllers() -> int:
    config = server_config.compute_server_config(deploy=True, quiet=True)
    free = common_utils.get_mem_size_gb() * 1024
    
    used = 0.0
    used += MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB
    used += (config.long_worker_config.garanteed_parallelism +
                config.long_worker_config.burstable_parallelism) * \
        server_config.LONG_WORKER_MEM_GB * 1024
    used += (config.short_worker_config.garanteed_parallelism +
                config.short_worker_config.burstable_parallelism) * \
        server_config.SHORT_WORKER_MEM_GB * 1024

    return max(1, int((free - used) // JOB_MEMORY_MB))


def start_controller() -> None:
    """Start the job controller process.

    This requires that the env file is already set up.
    """
    logs_dir = os.path.expanduser(
        managed_job_constants.JOBS_CONTROLLER_LOGS_DIR)
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, 'controller.log')

    activate_python_env_cmd = (f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV};')
    run_controller_cmd = (f'{sys.executable} -u -m'
                          'sky.jobs.controller')

    run_cmd = (f'{activate_python_env_cmd}'
               f'{run_controller_cmd}')

    logger.info(f'Running controller with command: {run_cmd}')

    pid = subprocess_utils.launch_new_process_tree(run_cmd, log_output=log_path)
    with open(JOB_CONTROLLER_PID_PATH, 'a', encoding='utf-8') as f:
        f.write(str(pid) + '\n')


def get_alive_controllers() -> typing.Optional[int]:
    if not os.path.exists(JOB_CONTROLLER_PID_PATH):
        # if the file doesn't exist, it means the controller server is not
        # running, so we return 0
        return 0

    try:
        with open(JOB_CONTROLLER_PID_PATH, 'r', encoding='utf-8') as f:
            pids = f.read().split('\n')[:-1]
    except OSError:
        # if the file is corrupted, or any issues with reading it, we just
        # return None to be safe and not over start
        return None

    alive = 0
    for pid in pids:
        try:
            if subprocess_utils.is_process_alive(int(pid.strip())):
                alive += 1
        except ValueError:
            # if the pid is not an integer, let's assume it's alive to not
            # over start new processes
            alive += 1
    return alive


def maybe_start_controllers() -> None:
    """Start the job controller process.

    If the process is already running, it will not start a new one.
    Will also add the job_id, dag_yaml_path, and env_file_path to the
    controllers list of processes.
    """
    try:
        with filelock.FileLock(JOB_CONTROLLER_PID_LOCK, blocking=False):
            alive = get_alive_controllers()
            if alive is None:
                return
            wanted = get_number_of_controllers()
            started = 0

            while alive + started < wanted:
                start_controller()
                started += 1

            if started > 0:
                logger.info(f'Started {started} controllers')
    except filelock.Timeout:
        # If we can't get the lock, just exit. The process holding the lock
        # should launch any pending jobs.
        pass


def submit_job(job_id: int, dag_yaml_path: str, original_user_yaml_path: str,
               env_file_path: str, priority: int) -> None:
    """Submit an existing job to the scheduler.

    This should be called after a job is created in the `spot` table as
    PENDING. It will tell the scheduler to try and start the job controller, if
    there are resources available. It may block to acquire the lock, so it
    should not be on the critical path for `sky jobs launch -d`.

    The user hash should be set (e.g. via SKYPILOT_USER_ID) before calling this.
    """
    state.scheduler_set_waiting(job_id, dag_yaml_path,
                                original_user_yaml_path, env_file_path,
                                common_utils.get_user_hash(), priority)
    maybe_start_controllers()


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

    try:
        yield
    except exceptions.NoClusterLaunchedError:
        # NoClusterLaunchedError is indicates that the job is in retry backoff.
        # We should transition to ALIVE_BACKOFF instead of ALIVE.
        state.scheduler_set_alive_backoff(job_id)
        raise
    else:
        state.scheduler_set_alive(job_id)


def job_done(job_id: int, idempotent: bool = False) -> None:
    """Transition a job to DONE.

    If idempotent is True, this will not raise an error if the job is already
    DONE.

    The job could be in any terminal ManagedJobStatus. However, once DONE, it
    should never transition back to another state.

    This is only called by utils.update_managed_jobs_statuses which is sync.
    """
    if idempotent and (state.get_job_schedule_state(job_id)
                       == state.ManagedJobScheduleState.DONE):
        return

    state.scheduler_set_done(job_id, idempotent)


def _set_alive_waiting(job_id: int) -> None:
    """Should use wait_until_launch_okay() to transition to this state."""
    state.scheduler_set_alive_waiting(job_id)


# === Async versions of functions called by controller.py ===


async def job_done_async(job_id: int, idempotent: bool = False) -> None:
    """Async version of job_done. Transition a job to DONE.

    If idempotent is True, this will not raise an error if the job is already
    DONE.

    The job could be in any terminal ManagedJobStatus. However, once DONE, it
    should never transition back to another state.

    This is called by controller.py.
    """
    if idempotent and (await state.get_job_schedule_state_async(job_id)
                       == state.ManagedJobScheduleState.DONE):
        return

    await state.scheduler_set_done_async(job_id, idempotent)


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
    parser.add_argument(
        '--priority',
        type=int,
        default=constants.DEFAULT_PRIORITY,
        help=
        f'Job priority ({constants.MIN_PRIORITY} to {constants.MAX_PRIORITY}).'
        f' Default: {constants.DEFAULT_PRIORITY}.')
    args = parser.parse_args()
    submit_job(args.job_id, args.dag_yaml, args.user_yaml_path, args.env_file,
               args.priority)
