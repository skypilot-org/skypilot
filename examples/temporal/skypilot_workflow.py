import asyncio
from dataclasses import dataclass
from datetime import timedelta
import subprocess

from temporalio import activity
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker


@dataclass
class SkyLaunchCommand:
    cluster_name: str
    entrypoint: str
    flags: str


@dataclass
class SkyDownCommand:
    cluster_name: str


@dataclass
class SkyExecCommand:
    cluster_name: str
    entrypoint: str
    flags: str


@activity.defn
async def run_sky_launch(input: SkyLaunchCommand) -> str:
    activity.logger.info(
        f'Running Sky Launch on cluster: {input.cluster_name} '
        f'with entrypoint: {input.entrypoint} and flags: {input.flags}')

    # Run the provided SkyPilot command using subprocess
    command = f"sky launch -y -c {input.cluster_name} {input.flags} {input.entrypoint}"

    try:
        result = subprocess.run(
            command.split(),
            capture_output=True,
            text=True,
            check=True
        )
        activity.logger.info(f"Sky launch output: {result.stdout}")
        return result.stdout.strip()  # Return the output from the subprocess
    except subprocess.CalledProcessError as e:
        activity.logger.error(f"Sky launch failed with error: {e}")
        raise  # Re-raise the exception to indicate failure


@activity.defn
async def run_sky_down(input: SkyDownCommand) -> str:
    activity.logger.info(f'Running Sky Down on cluster: {input.cluster_name}')

    # Run the sky down command using subprocess
    command = f"sky down -y {input.cluster_name}"

    try:
        result = subprocess.run(command.split(),
                                capture_output=True,
                                text=True,
                                check=True)
        activity.logger.info(f"Sky down output: {result.stdout}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        activity.logger.error(f"Sky down failed with error: {e}")
        raise  # Re-raise the exception to indicate failure


@activity.defn
async def run_sky_exec(input: SkyExecCommand) -> str:
    activity.logger.info(
        f'Running Sky exec on cluster: {input.cluster_name} '
        f'with entrypoint: {input.entrypoint} and flags: {input.flags}')

    # Run the sky exec command using subprocess
    full_command = f"sky exec {input.cluster_name} {input.flags} {input.entrypoint}"

    try:
        result = subprocess.run(full_command,
                                shell=True,
                                capture_output=True,
                                text=True,
                                check=True)
        activity.logger.info(f"Sky exec output: {result.stdout}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        activity.logger.error(f"Sky exec failed with error: {e}")
        raise  # Re-raise the exception to indicate failure


@dataclass
class GitCloneInput:
    repo_url: str
    clone_path: str


@activity.defn
async def run_git_clone(input: GitCloneInput) -> str:
    activity.logger.info(
        f'Cloning git repository: {input.repo_url} to {input.clone_path}')

    # Run the git clone command using subprocess
    command = f"git clone {input.repo_url} {input.clone_path}"

    try:
        result = subprocess.run(command.split(),
                                capture_output=True,
                                text=True,
                                check=True)
        activity.logger.info(f"Git clone output: {result.stdout}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        activity.logger.error(f"Git clone failed with error: {e}")
        raise  # Re-raise the exception to indicate failure


@workflow.defn
class SkyPilotWorkflow:

    @workflow.run
    async def run(self, cluster_prefix: str, repo_url: str) -> str:
        workflow.logger.info(
            f"Running SkyPilot workflow with cluster prefix: {cluster_prefix} ")

        # 1. Clone the repository
        clone_path = "/tmp/skypilot_repo"
        clone_result = await workflow.execute_activity(
            run_git_clone,
            GitCloneInput(repo_url, clone_path),
            start_to_close_timeout=timedelta(minutes=5),
        )
        workflow.logger.info(f"Clone result: {clone_result}")

        # 2. Launch data preprocessing
        cluster_name = f"{cluster_prefix}-preprocess"
        preprocess_result = await workflow.execute_activity(
            run_sky_launch,
            SkyLaunchCommand(cluster_name,
                             f"{clone_path}/data_preprocessing.yaml",
                             "--cloud kubernetes"),
            start_to_close_timeout=timedelta(minutes=30),
        )
        workflow.logger.info(f"Preprocessing result: {preprocess_result}")

        # 3. Down the cluster
        down_result = await workflow.execute_activity(
            run_sky_down,
            SkyDownCommand(cluster_name),
            start_to_close_timeout=timedelta(minutes=10),
        )
        workflow.logger.info(f"Down result: {down_result}")

        # 4. Launch training
        cluster_name = f"{cluster_prefix}-train"
        train_result = await workflow.execute_activity(
            run_sky_launch,
            SkyLaunchCommand(cluster_name, f"{clone_path}/train.yaml",
                             "--cloud kubernetes"),
            start_to_close_timeout=timedelta(minutes=60),
        )
        workflow.logger.info(f"Training result: {train_result}")

        # 5. Execute evaluation on the same
        eval_result = await workflow.execute_activity(
            run_sky_exec,
            SkyExecCommand(cluster_name, f"{clone_path}/eval.yaml", ""),
            start_to_close_timeout=timedelta(minutes=30),
        )
        workflow.logger.info(f"Evaluation result: {eval_result}")

        # Return the combined result
        return f"Preprocessing: {preprocess_result}, Training: {train_result}, Evaluation: {eval_result}"


async def main():
    # Start client
    client = await Client.connect("localhost:7233")

    # Run a worker for the workflow
    async with Worker(
        client,
        task_queue="skypilot-task-queue",
        workflows=[SkyPilotWorkflow],
        activities=[run_sky_launch, run_sky_down, run_sky_exec, run_git_clone
                   ],  # Register all Sky activities to the same worker
    ):
        # Execute the workflow with cluster name and config path
        result = await client.execute_workflow(
            SkyPilotWorkflow.run,
            "my-workflow",  # cluster name prefix
            id="skypilot-workflow-id",
            task_queue="skypilot-task-queue",
        )
        print(f"SkyPilot Workflow Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())