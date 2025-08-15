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
import asyncio
import contextlib
import os
import sys
import typing
from typing import Set
import uuid

import filelock

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.jobs import constants as managed_job_constants
from sky.jobs import state
from sky.jobs import utils as managed_job_utils
from sky.server import config as server_config
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import subprocess_utils

if typing.TYPE_CHECKING:
    import logging

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
MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB = 2048

# Maximum values for above constants. There will start to be lagging issues
# at these numbers already.
# JOB_MEMORY_MB = 200
# LAUNCHES_PER_WORKER = 16
# JOBS_PER_WORKER = 400


def get_number_of_controllers() -> int:
    consolidation_mode = skypilot_config.get_nested(
        ('jobs', 'controller', 'consolidation_mode'), default_value=False)

    total_memory_mb = common_utils.get_mem_size_gb() * 1024
    if consolidation_mode:
        config = server_config.compute_server_config(deploy=True, quiet=True)

        used = 0.0
        used += MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB
        used += (config.long_worker_config.garanteed_parallelism +
                    config.long_worker_config.burstable_parallelism) * \
            server_config.LONG_WORKER_MEM_GB * 1024
        used += (config.short_worker_config.garanteed_parallelism +
                    config.short_worker_config.burstable_parallelism) * \
            server_config.SHORT_WORKER_MEM_GB * 1024

        return max(1, int((total_memory_mb - used) // JOB_MEMORY_MB))
    else:
        return max(
            1,
            int((total_memory_mb - MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB) /
                ((LAUNCHES_PER_WORKER * server_config.LONG_WORKER_MEM_GB) * 1024
                 + JOB_MEMORY_MB)))


def start_controller() -> None:
    """Start the job controller process.

    This requires that the env file is already set up.
    """
    os.environ[constants.ENV_VAR_IS_JOB_CONTROLLER] = 'true'
    logs_dir = os.path.expanduser(
        managed_job_constants.JOBS_CONTROLLER_LOGS_DIR)
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f'controller_{uuid.uuid4()}.log')

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
    there are resources available.

    The user hash should be set (e.g. via SKYPILOT_USER_ID) before calling this.
    """
    controller_pid = state.get_job_controller_pid(job_id)
    if controller_pid is not None:
        if managed_job_utils.controller_process_alive(controller_pid, job_id):
            maybe_start_controllers()
            return
    state.scheduler_set_waiting(job_id, dag_yaml_path,
                                original_user_yaml_path, env_file_path,
                                common_utils.get_user_hash(), priority)
    maybe_start_controllers()


@contextlib.asynccontextmanager
async def scheduled_launch(
    job_id: int,
    starting: Set[int],
    starting_lock: asyncio.Lock,
    starting_signal: asyncio.Condition,
    job_logger: 'logging.Logger',
):
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

    assert starting_lock == starting_signal._lock, (  # type: ignore #pylint: disable=protected-access
        'starting_lock and starting_signal must use the same lock')

    while True:
        async with starting_lock:
            starting_count = len(starting)
            if starting_count < LAUNCHES_PER_WORKER:
                break
            job_logger.info('Too many jobs starting, waiting for a slot')
            await starting_signal.wait()

    job_logger.info(f'Starting job {job_id}')

    async with starting_lock:
        starting.add(job_id)

    await state.scheduler_set_launching_async(job_id)

    try:
        yield
    except Exception as e:
        raise e
    else:
        await state.scheduler_set_alive_async(job_id)
    finally:
        async with starting_lock:
            starting.remove(job_id)
            starting_signal.notify()


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


def job_done_async(job_id: int, idempotent: bool = False):
    """Async version of job_done."""
    if idempotent and (state.get_job_schedule_state(job_id)
                       == state.ManagedJobScheduleState.DONE):
        return

    state.scheduler_set_done_async(job_id, idempotent)


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
               args.priority)
