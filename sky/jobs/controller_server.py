"""Controller server for managing jobs."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import asyncio
from typing import Dict, Optional
import logging
import multiprocessing

from sky.jobs import controller, state
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

class JobRequest(BaseModel):
    job_id: int
    dag_yaml_path: str
    env_file_path: Optional[str] = None

@app.post("/create")
async def create_job(job: JobRequest):
    """Create a new job."""
    logger.info(f"Creating job {job.job_id}")
    try:
        await controller.start_job(job.job_id, job.dag_yaml_path)
        state.set_job_controller_pid(job.job_id, -1)
        return {"status": "success", "message": f"Job {job.job_id} created"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create job {job.job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cancel/{job_id}")
async def cancel_job(job_id: int):
    """Cancel an existing job."""
    logger.info(f"Cancelling job {job_id}")
    try:
        await controller.cancel_job(job_id)
        return {"status": "success", "message": f"Job {job_id} cancelled"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to cancel job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status/{job_id}")
async def get_job_status(job_id: int):
    """Get the status of a job."""
    try:
        status = managed_job_state.get_status(job_id)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return {"status": status.value}
    except Exception as e:
        logger.error(f"Failed to get status for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def start_server(host: str = "localhost", port: int = 8000):
    """Start the controller server."""
    # num_workers = multiprocessing.cpu_count()
    # uvicorn.run("sky.jobs.controller_server:app", host=host, port=port, workers=2 * num_workers)
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    start_server()
