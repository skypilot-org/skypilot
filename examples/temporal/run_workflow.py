import asyncio
import traceback

from temporalio.client import Client, WorkflowFailureError
from workflows import SkyPilotWorkflow, SkyPilotWorkflowInput


async def main():
    # Start client
    client = await Client.connect("localhost:7233")

    try:
        # Execute the workflow with cluster name and config path
        result = await client.execute_workflow(
            SkyPilotWorkflow.run,
            SkyPilotWorkflowInput(
                cluster_prefix="my-workflow",  # cluster name prefix
                repo_url="https://github.com/mjkanji/mock_train_workflow.git",
                data_bucket_url="s3://skypilot-temporal-bucket",
            ),  # repo url
            id="skypilot-workflow-id",
            task_queue="skypilot-task-queue",
        )
        print(f"SkyPilot Workflow Result: {result}")
    except WorkflowFailureError:
        print("Got expected exception: ", traceback.format_exc())


if __name__ == "__main__":
    asyncio.run(main())
