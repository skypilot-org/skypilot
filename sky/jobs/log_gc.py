"""Log garbage collection for managed jobs."""

import asyncio
from datetime import datetime
import os
import pathlib
import shutil
import time

import anyio
import filelock

from sky import sky_logging
from sky import skypilot_config
from sky.jobs import constants as managed_job_constants
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.utils import context
from sky.utils import context_utils

logger = sky_logging.init_logger(__name__)

# Filelock for garbage collector leader election.
_JOB_CONTROLLER_GC_LOCK_PATH = os.path.expanduser(
    '~/.sky/locks/job_controller_gc.lock')

_DEFAULT_TASK_LOGS_GC_RETENTION_HOURS = 24 * 7
_DEFAULT_CONTROLLER_LOGS_GC_RETENTION_HOURS = 24 * 7

_LEAST_FREQUENT_GC_INTERVAL_SECONDS = 3600
_MOST_FREQUENT_GC_INTERVAL_SECONDS = 30


def _next_gc_interval(retention_seconds: int) -> int:
    """Get the next GC interval."""
    # Run the GC at least per hour to ensure hourly accuracy and
    # at most per 30 seconds (when retention_seconds is small) to
    # avoid too frequent cleanup.
    return max(min(retention_seconds, _LEAST_FREQUENT_GC_INTERVAL_SECONDS),
               _MOST_FREQUENT_GC_INTERVAL_SECONDS)


async def gc_controller_logs_for_job():
    """Garbage collect job and controller logs."""
    while True:
        skypilot_config.reload_config()
        controller_logs_retention = skypilot_config.get_nested(
            ('jobs', 'controller', 'controller_logs_gc_retention_hours'),
            _DEFAULT_CONTROLLER_LOGS_GC_RETENTION_HOURS) * 3600
        # Negative value disables the GC
        if controller_logs_retention >= 0:
            logger.info(f'GC controller logs for job: retention '
                        f'{controller_logs_retention} seconds')
            try:
                finished = False
                while not finished:
                    finished = await _clean_controller_logs_with_retention(
                        controller_logs_retention)
            except asyncio.CancelledError:
                logger.info('Managed jobs logs GC task cancelled')
                break
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error GC controller logs for job: {e}',
                             exc_info=True)
        else:
            logger.info('Controller logs GC is disabled')

        interval = _next_gc_interval(controller_logs_retention)
        logger.info('Next controller logs GC is scheduled after '
                    f'{interval} seconds')
        await asyncio.sleep(interval)


async def gc_task_logs_for_job():
    """Garbage collect task logs for job."""
    while True:
        skypilot_config.reload_config()
        task_logs_retention = skypilot_config.get_nested(
            ('jobs', 'controller', 'task_logs_gc_retention_hours'),
            _DEFAULT_TASK_LOGS_GC_RETENTION_HOURS) * 3600
        # Negative value disables the GC
        if task_logs_retention >= 0:
            logger.info('GC task logs for job: '
                        f'retention {task_logs_retention} seconds')
            try:
                finished = False
                while not finished:
                    finished = await _clean_task_logs_with_retention(
                        task_logs_retention)
            except asyncio.CancelledError:
                logger.info('Task logs GC task cancelled')
                break
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error GC task logs for job: {e}', exc_info=True)
        else:
            logger.info('Controller logs GC is disabled')

        interval = _next_gc_interval(task_logs_retention)
        logger.info(f'Next task logs GC is scheduled after {interval} seconds')
        await asyncio.sleep(_next_gc_interval(task_logs_retention))


async def _clean_controller_logs_with_retention(retention_seconds: int,
                                                batch_size: int = 100):
    """Clean controller logs with retention.

    Returns:
        Whether the GC of this round has finished, False means there might
        still be more controller logs to clean.
    """
    assert batch_size > 0, 'Batch size must be positive'
    jobs = await managed_job_state.get_controller_logs_to_clean_async(
        retention_seconds, batch_size=batch_size)
    job_ids_to_update = []
    for job in jobs:
        job_ids_to_update.append(job['job_id'])
        log_file = managed_job_utils.controller_log_file_for_job(job['job_id'])
        cleaned_at = time.time()
        if await anyio.Path(log_file).exists():
            ts_str = datetime.fromtimestamp(cleaned_at).strftime(
                '%Y-%m-%d %H:%M:%S')
            msg = f'Controller log has been cleaned at {ts_str}.'
            # Sync down logs will reference to this file directly, so we
            # keep the file and delete the content.
            # TODO(aylei): refactor sync down logs if the inode usage
            # becomes an issue.
            async with await anyio.open_file(log_file, 'w',
                                             encoding='utf-8') as f:
                await f.write(msg + '\n')
    # Batch the update, the timestamp will be not accurate but it's okay.
    await managed_job_state.set_controller_logs_cleaned_async(
        job_ids=job_ids_to_update, logs_cleaned_at=time.time())
    complete = len(jobs) < batch_size
    logger.info(f'Cleaned {len(jobs)} controller logs with retention '
                f'{retention_seconds} seconds, complete: {complete}')
    return complete


async def _clean_task_logs_with_retention(retention_seconds: int,
                                          batch_size: int = 100):
    """Clean task logs with retention.

    Returns:
        Whether the GC of this round has finished, False means there might
        still be more task logs to clean.
    """
    assert batch_size > 0, 'Batch size must be positive'
    tasks = await managed_job_state.get_task_logs_to_clean_async(
        retention_seconds, batch_size=batch_size)
    tasks_to_update = []
    for task in tasks:
        local_log_file = anyio.Path(task['local_log_file'])
        # We assume the log directory has the following layout:
        # task-id/
        #   - run.log
        #   - tasks/
        #     - run.log
        # and also remove the tasks directory on cleanup.
        task_log_dir = local_log_file.parent.joinpath('tasks')
        await local_log_file.unlink(missing_ok=True)
        await context_utils.to_thread(shutil.rmtree,
                                      str(task_log_dir),
                                      ignore_errors=True)
        # We have at least once semantic guarantee for the cleanup here.
        tasks_to_update.append((task['job_id'], task['task_id']))
    await managed_job_state.set_task_logs_cleaned_async(
        tasks=list(tasks_to_update), logs_cleaned_at=time.time())
    complete = len(tasks) < batch_size
    logger.info(f'Cleaned {len(tasks)} task logs with retention '
                f'{retention_seconds} seconds, complete: {complete}')
    return complete


@context.contextual_async
async def run_log_gc():
    """Run the log garbage collector."""
    log_dir = os.path.expanduser(managed_job_constants.JOBS_CONTROLLER_LOGS_DIR)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'garbage_collector.log')
    # Remove previous log file
    await anyio.Path(log_path).unlink(missing_ok=True)
    ctx = context.get()
    assert ctx is not None, 'Context is not initialized'
    ctx.redirect_log(pathlib.Path(log_path))
    gc_controller_logs_for_job_task = asyncio.create_task(
        gc_controller_logs_for_job())
    gc_task_logs_for_job_task = asyncio.create_task(gc_task_logs_for_job())
    await asyncio.gather(gc_controller_logs_for_job_task,
                         gc_task_logs_for_job_task)


def elect_for_log_gc():
    """Use filelock to elect for the log garbage collector.

    The log garbage collector runs in the controller process to avoid the
    overhead of launching a new process and the lifecycle management, the
    threads that does not elected as the log garbage collector just wait.
    on the filelock and bring trivial overhead.
    """
    with filelock.FileLock(_JOB_CONTROLLER_GC_LOCK_PATH):
        asyncio.run(run_log_gc())
