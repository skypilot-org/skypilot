"""Controller server for managing jobs."""

import asyncio
import logging
import os
import resource

from fastapi import FastAPI
import uvicorn

from sky.jobs import constants
from sky.jobs import controller
from sky.jobs import scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


@app.on_event('startup')
async def startup_event():
    # Will happen multiple times, who cares though
    os.makedirs(constants.SIGNAL_PATH, exist_ok=True)

    # Will loop forever, do it in the background
    asyncio.create_task(controller.cancel_job())
    asyncio.create_task(controller.monitor_loop())

    # Increase number of files we can open
    soft = None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except OSError as e:
        logger.warning(f'Failed to increase number of files we can open: {e}\n'
                       f'Current soft limit: {soft}, hard limit: {hard}')


def start_server(host: str = 'localhost', port: int = 8000):
    """Start the controller server."""
    uvicorn.run('sky.jobs.controller_server:app',
                host=host,
                port=port,
                workers=scheduler.WORKERS)


if __name__ == '__main__':
    start_server()
