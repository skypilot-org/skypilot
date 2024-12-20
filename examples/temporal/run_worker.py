import asyncio
import logging
import random
from uuid import UUID

from activities import run_git_clone, run_sky_down, run_sky_exec, run_sky_launch
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
from workflows import SkyPilotWorkflow


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    skypilot_specific_queue = (
        f"skypilot_specific_queue-host-{UUID(int=random.getrandbits(128))}"
    )

    @activity.defn(name="get_available_task_queue")
    async def select_task_queue() -> str:
        """Randomly assign the job to a queue"""
        return skypilot_specific_queue

    # Start client
    client = await Client.connect("localhost:7233", namespace="default")

    # Run a worker to distribute the workflows
    run_futures = []
    handle = Worker(
        client,
        task_queue="skypilot-distribution-queue",
        workflows=[SkyPilotWorkflow],
        activities=[select_task_queue],
    )
    run_futures.append(handle.run())
    print("Base worker started")

    # Run unique task queue for this particular host
    handle = Worker(
        client,
        task_queue=skypilot_specific_queue,
        activities=[
            run_sky_launch,
            run_sky_down,
            run_sky_exec,
            run_git_clone,
        ],  # Register all Sky activities to the same worker
    )
    run_futures.append(handle.run())
    # Wait until interrupted
    print(f"Worker {skypilot_specific_queue} started")

    print("All workers started, ctrl+c to exit")
    await asyncio.gather(*run_futures)


if __name__ == "__main__":
    asyncio.run(main())
