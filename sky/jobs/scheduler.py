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
import pathlib
import shutil
import sys
import typing
from typing import List, Optional, Set
import uuid

import filelock

from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import common as adaptors_common
from sky.client import sdk
from sky.jobs import constants as managed_job_constants
from sky.jobs import state
from sky.jobs import utils as managed_job_utils
from sky.server import config as server_config
from sky.skylet import constants
from sky.utils import annotations
from sky.utils import controller_utils
from sky.utils import subprocess_utils

if typing.TYPE_CHECKING:
    import logging

    import psutil
else:
    psutil = adaptors_common.LazyImport('psutil')

logger = sky_logging.init_logger('sky.jobs.controller')

# Job controller lock. This is used to synchronize writing/reading the
# controller pid file.
JOB_CONTROLLER_PID_LOCK = os.path.expanduser(
    '~/.sky/locks/job_controller_pid.lock')

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
MAX_JOBS_PER_WORKER = 200
# Maximum number of controllers that can be running. Hard to handle more than
# 512 launches at once.
MAX_CONTROLLERS = 512 // LAUNCHES_PER_WORKER
# Limit the number of jobs that can be running at once on the entire jobs
# controller cluster. It's hard to handle cancellation of more than 2000 jobs at
# once.
# TODO(cooperc): Once we eliminate static bottlenecks (e.g. sqlite), remove this
# hardcoded max limit.
MAX_TOTAL_RUNNING_JOBS = 2000
# Maximum values for above constants. There will start to be lagging issues
# at these numbers already.
# JOB_MEMORY_MB = 200
# LAUNCHES_PER_WORKER = 16
# JOBS_PER_WORKER = 400

# keep 2GB reserved after the controllers
MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB = 2048

CURRENT_HASH = os.path.expanduser('~/.sky/wheels/current_sky_wheel_hash')


def _parse_controller_pid_entry(
        entry: str) -> Optional[state.ControllerPidRecord]:
    entry = entry.strip()
    if not entry:
        return None
    # The entry should be like <pid>,<started_at>
    # pid is an integer, started_at is a float
    # For backwards compatibility, we also support just <pid>
    entry_parts = entry.split(',')
    if len(entry_parts) == 2:
        [raw_pid, raw_started_at] = entry_parts
    elif len(entry_parts) == 1:
        # Backwards compatibility, pre-#7847
        # TODO(cooperc): Remove for 0.13.0
        raw_pid = entry_parts[0]
        raw_started_at = None
    else:
        # Unknown format
        return None

    try:
        pid = int(raw_pid)
    except ValueError:
        return None

    started_at: Optional[float] = None
    if raw_started_at:
        try:
            started_at = float(raw_started_at)
        except ValueError:
            started_at = None
    return state.ControllerPidRecord(pid=pid, started_at=started_at)


def get_controller_process_records(
) -> Optional[List[state.ControllerPidRecord]]:
    """Return recorded controller processes if the file can be read."""
    if not os.path.exists(JOB_CONTROLLER_PID_PATH):
        # If the file doesn't exist, it means the controller server is not
        # running, so we return an empty list
        return []
    try:
        with open(JOB_CONTROLLER_PID_PATH, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
    except (FileNotFoundError, OSError):
        return None

    records: List[state.ControllerPidRecord] = []
    for line in lines:
        record = _parse_controller_pid_entry(line)
        if record is not None:
            records.append(record)
    return records


def _append_controller_pid_record(pid: int,
                                  started_at: Optional[float]) -> None:
    # Note: started_at is a float, but converting to a string will not lose any
    # precision. See https://docs.python.org/3/tutorial/floatingpoint.html and
    # https://github.com/python/cpython/issues/53583
    entry = str(pid) if started_at is None else f'{pid},{started_at}'
    with open(JOB_CONTROLLER_PID_PATH, 'a', encoding='utf-8') as f:
        f.write(entry + '\n')


@annotations.lru_cache(scope='global')
def get_number_of_controllers() -> int:
    """Returns the number of controllers that should be running.

    This is the number of controllers that should be running to maximize
    resource utilization.

    In consolidation mode, we use the existing API server so our resource
    requirements are just for the job controllers. We try taking up as much
    much memory as possible left over from the API server.

    In non-consolidation mode, we have to take into account the memory of the
    API server workers. We limit to only 8 launches per worker, so our logic is
    each controller will take CONTROLLER_MEMORY_MB + 8 * WORKER_MEMORY_MB. We
    leave some leftover room for ssh codegen and ray status overhead.
    """
    consolidation_mode = skypilot_config.get_nested(
        ('jobs', 'controller', 'consolidation_mode'), default_value=False)

    total_memory_mb = controller_utils.get_controller_mem_size_gb() * 1024
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

        return min(MAX_CONTROLLERS,
                   max(1, int((total_memory_mb - used) // JOB_MEMORY_MB)))
    else:
        return min(
            MAX_CONTROLLERS,
            max(
                1,
                int((total_memory_mb - MAXIMUM_CONTROLLER_RESERVED_MEMORY_MB) /
                    ((LAUNCHES_PER_WORKER * server_config.LONG_WORKER_MEM_GB) *
                     1024 + JOB_MEMORY_MB))))


def start_controller() -> None:
    """Start the job controller process.

    This requires that the env file is already set up.
    """
    os.environ[constants.OVERRIDE_CONSOLIDATION_MODE] = 'true'
    logs_dir = os.path.expanduser(
        managed_job_constants.JOBS_CONTROLLER_LOGS_DIR)
    os.makedirs(logs_dir, exist_ok=True)
    controller_uuid = str(uuid.uuid4())
    log_path = os.path.join(logs_dir, f'controller_{controller_uuid}.log')

    activate_python_env_cmd = (f'{constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV};')
    run_controller_cmd = (f'{sys.executable} -u -m'
                          f'sky.jobs.controller {controller_uuid}')

    run_cmd = (f'{activate_python_env_cmd}'
               f'{run_controller_cmd}')

    logger.info(f'Running controller with command: {run_cmd}')

    pid = subprocess_utils.launch_new_process_tree(run_cmd, log_output=log_path)
    pid_started_at = psutil.Process(pid).create_time()
    _append_controller_pid_record(pid, pid_started_at)


def get_alive_controllers() -> Optional[int]:
    records = get_controller_process_records()
    if records is None:
        # If we cannot read the file reliably, avoid starting extra controllers.
        return None
    if not records:
        return 0

    alive = 0
    for record in records:
        if managed_job_utils.controller_process_alive(record, quiet=False):
            alive += 1
    return alive


def maybe_start_controllers(from_scheduler: bool = False) -> None:
    """Start the job controller process.

    If the process is already running, it will not start a new one.
    Will also add the job_id, dag_yaml_path, and env_file_path to the
    controllers list of processes.
    """
    try:
        with filelock.FileLock(JOB_CONTROLLER_PID_LOCK, blocking=False):
            if from_scheduler and not managed_job_utils.is_consolidation_mode():
                cur = pathlib.Path(CURRENT_HASH)
                old = pathlib.Path(f'{CURRENT_HASH}.old')

                if old.exists() and cur.exists():
                    if (old.read_text(encoding='utf-8') !=
                            cur.read_text(encoding='utf-8')):
                        # TODO(luca): there is a 1/2^160 chance that there will
                        # be a collision. using a geometric distribution and
                        # assuming one update a day, we expect a bug slightly
                        # before the heat death of the universe. should get
                        # this fixed before then.
                        try:
                            # this will stop all the controllers and the api
                            # server.
                            sdk.api_stop()
                            # All controllers should be dead. Remove the PIDs so
                            # that update_managed_jobs_statuses won't think they
                            # have failed.
                            state.reset_jobs_for_recovery()
                        except Exception as e:  # pylint: disable=broad-except
                            logger.error(f'Failed to stop the api server: {e}')
                            pass
                        else:
                            shutil.copyfile(cur, old)
                if not old.exists():
                    shutil.copyfile(cur, old)

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
    controller_process = state.get_job_controller_process(job_id)
    if controller_process is not None:
        # why? TODO(cooperc): figure out why this is needed, fix it, and remove
        if managed_job_utils.controller_process_alive(controller_process,
                                                      job_id):
            # This can happen when HA recovery runs for some reason but the job
            # controller is still alive.
            logger.warning(f'Job {job_id} is still alive, skipping submission')
            maybe_start_controllers(from_scheduler=True)
            return

    with open(dag_yaml_path, 'r', encoding='utf-8') as dag_file:
        dag_yaml_content = dag_file.read()
    with open(original_user_yaml_path, 'r',
              encoding='utf-8') as original_user_yaml_file:
        original_user_yaml_content = original_user_yaml_file.read()
    with open(env_file_path, 'r', encoding='utf-8') as env_file:
        env_file_content = env_file.read()
    logger.debug(f'Storing job {job_id} file contents in database '
                 f'(DAG bytes={len(dag_yaml_content)}, '
                 f'original user yaml bytes={len(original_user_yaml_content)}, '
                 f'env bytes={len(env_file_content)}).')
    state.scheduler_set_waiting(job_id, dag_yaml_content,
                                original_user_yaml_content, env_file_content,
                                priority)
    if state.get_ha_recovery_script(job_id) is None:
        # the run command is just the command that called scheduler
        run = (f'source {env_file_path} && '
               f'{sys.executable} -m sky.jobs.scheduler {dag_yaml_path} '
               f'--job-id {job_id} --env-file {env_file_path} '
               f'--user-yaml-path {original_user_yaml_path} '
               f'--priority {priority}')
        state.set_ha_recovery_script(job_id, run)
    maybe_start_controllers(from_scheduler=True)


@contextlib.asynccontextmanager
async def scheduled_launch(
    job_id: int,
    starting: Set[int],
    starting_lock: asyncio.Lock,
    starting_signal: asyncio.Condition,
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
    pool = state.get_pool_from_job_id(job_id)
    # For pool, since there is no execution.launch, we don't need to have all
    # the ALIVE_WAITING state. The state transition will be
    # WAITING -> ALIVE -> DONE without any intermediate transitions.
    if pool is not None:
        yield
        return

    assert starting_lock == starting_signal._lock, (  # type: ignore #pylint: disable=protected-access
        'starting_lock and starting_signal must use the same lock')

    while True:
        async with starting_lock:
            starting_count = len(starting)
            if starting_count < LAUNCHES_PER_WORKER:
                break
            logger.info('Too many jobs starting, waiting for a slot')
            await starting_signal.wait()

    logger.info(f'Starting job {job_id}')

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


async def job_done_async(job_id: int, idempotent: bool = False):
    """Async version of job_done."""
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
