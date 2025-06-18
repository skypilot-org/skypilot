"""Controller server for managing jobs."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import asyncio
from typing import Dict, Optional
import logging

from sky.jobs import controller
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
        return {"status": "success", "message": f"Job {job.job_id} created"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create job {job.job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cancel")
async def cancel_job(job: JobRequest):
    """Cancel an existing job."""
    logger.info(f"Cancelling job {job.job_id}")
    try:
        await controller.cancel_job(job.job_id)
        return {"status": "success", "message": f"Job {job.job_id} cancelled"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to cancel job {job.job_id}: {e}")
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

def start_server(host: str = "0.0.0.0", port: int = 8000):
    """Start the controller server."""
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    start_server()
