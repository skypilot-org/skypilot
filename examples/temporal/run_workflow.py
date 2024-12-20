import asyncio
import os
import traceback
from pathlib import Path

from dotenv import load_dotenv
from temporalio.client import Client, WorkflowFailureError
from workflows import SkyPilotWorkflow, SkyPilotWorkflowInput

load_dotenv(Path(__file__).parent.joinpath(".env"))


async def main():
    # Start client
    client = await Client.connect("localhost:7233")

    try:
        # Execute the workflow with cluster name and config path
        result = await client.execute_workflow(
            SkyPilotWorkflow.run,
            SkyPilotWorkflowInput(
                cloud=os.getenv("SKYPILOT_CLOUD", ""),
                cluster_prefix="my-workflow",  # cluster name prefix
                repo_url="https://github.com/mjkanji/mock_train_workflow.git",
                data_bucket_url=os.getenv("SKYPILOT_BUCKET_URL", ""),
            ),  # repo url
            id="skypilot-workflow-id",
            task_queue="skypilot-distribution-queue",
        )
        print(f"SkyPilot Workflow Result: {result}")
    except WorkflowFailureError:
        print("Got expected exception: ", traceback.format_exc())


if __name__ == "__main__":
    asyncio.run(main())
