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

    # Get API server endpoint from environment or .env file
    api_server_endpoint = os.getenv("SKYPILOT_API_SERVER_ENDPOINT")
    if api_server_endpoint:
        print(f"Using SkyPilot API server at: {api_server_endpoint}")
    else:
        print("SKYPILOT_API_SERVER_ENDPOINT not set. SkyPilot will use local state.")
        
    # Get environment variables for tasks
    data_bucket_url = os.getenv("SKYPILOT_BUCKET_URL", "")
    
    # Create environment overrides dictionary
    envs_override = {}
    if data_bucket_url:
        print(f"Using DATA_BUCKET_URL: {data_bucket_url}")
        envs_override["DATA_BUCKET_NAME"] = data_bucket_url
    else:
        raise ValueError("SKYPILOT_BUCKET_URL not set. Please set it in your environment or .env file.")

    try:
        # Execute the workflow with cluster name and config path
        result = await client.execute_workflow(
            SkyPilotWorkflow.run,
            SkyPilotWorkflowInput(
                cluster_prefix="my-workflow",  # cluster name prefix
                repo_url="https://github.com/skypilot-org/mock-train-workflow.git",
                envs_override=envs_override,
                branch="clientserver_example",  # Specify the branch to use
                api_server_endpoint=api_server_endpoint,  # Add the API server endpoint
            ),
            id="skypilot-workflow-id",
            task_queue="skypilot-distribution-queue",
        )
        print(f"SkyPilot Workflow Result: {result}")
    except WorkflowFailureError:
        print("Got expected exception: ", traceback.format_exc())


if __name__ == "__main__":
    asyncio.run(main())
