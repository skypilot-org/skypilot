"""Controller server for managing jobs."""

import asyncio
import logging
import os
import resource
import sys

from sky.jobs import constants
from sky.jobs import controller

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    # Will happen multiple times, who cares though
    os.makedirs(constants.SIGNAL_PATH, exist_ok=True)

    # Increase number of files we can open
    soft = None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except OSError as e:
        logger.warning(f'Failed to increase number of files we can open: {e}\n'
                       f'Current soft limit: {soft}, hard limit: {hard}')

    # Will loop forever, do it in the background
    cancel_job_task = asyncio.create_task(controller.cancel_job())
    monitor_loop_task = asyncio.create_task(controller.monitor_loop())

    try:
        await asyncio.gather(cancel_job_task, monitor_loop_task)
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Controller server crashed: {e}')
        sys.exit(1)


if __name__ == '__main__':
    logger.info('Starting controller server...')
    asyncio.run(main())
