import asyncio
import logging
from uuid import UUID

from activities import run_git_clone, run_sky_down, run_sky_exec, run_sky_launch
from temporalio.client import Client
from temporalio.worker import Worker
from workflows import SkyPilotWorkflow


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Start client
    client = await Client.connect("localhost:7233", namespace="default")

    # Create a single worker that handles all workflows and activities
    handle = Worker(
        client,
        task_queue="skypilot-workflow-queue",
        workflows=[SkyPilotWorkflow],
        activities=[
            run_sky_launch,
            run_sky_down,
            run_sky_exec,
            run_git_clone,
        ],
    )
    
    print("Worker started on queue 'skypilot-workflow-queue'")
    
    # Run the worker
    await handle.run()


if __name__ == "__main__":
    asyncio.run(main())
