"""Controller server for managing jobs."""

import asyncio
import logging
import os
import resource
from typing import Optional

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
import uvicorn

from sky.jobs import constants
from sky.jobs import controller
from sky.jobs import scheduler
from sky.jobs import state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


@app.on_event('startup')
async def startup_event():
    # Will happen multiple times, who cares though
    os.makedirs(constants.SIGNAL_PATH, exist_ok=True)

    # Will loop forever, do it in the background
    asyncio.create_task(controller.cancel_job())

    # Increase number of files we can open
    soft = None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except OSError as e:
        logger.warning(f'Failed to increase number of files we can open: {e}\n'
                       f'Current soft limit: {soft}, hard limit: {hard}')


class JobRequest(BaseModel):
    job_id: int
    dag_yaml_path: str
    env_file_path: Optional[str] = None


@app.post('/create')
async def create_job(job: JobRequest):
    """Create a new job."""
    logger.info(f'Creating job {job.job_id}')
    try:
        await controller.start_job(job.job_id, job.dag_yaml_path)
        state.set_job_controller_pid(job.job_id, -1)
        return {'status': 'success', 'message': f'Job {job.job_id} created'}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f'Failed to create job {job.job_id}: {e}')
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post('/cancel/{job_id}')
async def cancel_job(job_id: int):
    """Cancel an existing job."""
    logger.info(f'Cancelling job {job_id}')
    try:
        with open(f'{constants.SIGNAL_PATH}/{job_id}', 'w',
                  encoding='utf-8') as f:
            f.write('')
        return {'status': 'success', 'message': f'Job {job_id} cancelled'}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f'Failed to cancel job {job_id}: {e}')
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get('/status/{job_id}')
async def get_job_status(job_id: int):
    """Get the status of a job."""
    try:
        status = state.get_status(job_id)
        if status is None:
            raise HTTPException(status_code=404,
                                detail=f'Job {job_id} not found')
        return {'status': status.value}
    except Exception as e:
        logger.error(f'Failed to get status for job {job_id}: {e}')
        raise HTTPException(status_code=500, detail=str(e)) from e


def start_server(host: str = 'localhost', port: int = 8000):
    """Start the controller server."""
    uvicorn.run('sky.jobs.controller_server:app',
                host=host,
                port=port,
                workers=scheduler.WORKERS)


if __name__ == '__main__':
    start_server()
