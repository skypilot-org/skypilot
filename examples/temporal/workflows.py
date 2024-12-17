from dataclasses import dataclass
from datetime import timedelta
from textwrap import dedent

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import (
        GitCloneInput,
        SkyDownCommand,
        SkyExecCommand,
        SkyLaunchCommand,
        run_git_clone,
        run_sky_down,
        run_sky_exec,
        run_sky_launch,
    )


@dataclass
class SkyPilotWorkflowInput:
    cluster_prefix: str
    repo_url: str
    data_bucket_url: str | None = None


@workflow.defn
class SkyPilotWorkflow:
    @workflow.run
    async def run(self, input: SkyPilotWorkflowInput) -> str:
        cluster_prefix = input.cluster_prefix
        repo_url = input.repo_url
        data_bucket_url = input.data_bucket_url

        retry_policy = RetryPolicy(
            maximum_attempts=3,
            maximum_interval=timedelta(seconds=2),
        )

        workflow.logger.info(
            f"Running SkyPilot workflow with cluster prefix: {cluster_prefix} "
        )

        # 1. Clone the repository
        clone_path = "/tmp/skypilot_repo"
        clone_result = await workflow.execute_activity(
            run_git_clone,
            GitCloneInput(repo_url, clone_path),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )
        workflow.logger.info(f"Clone result: {clone_result}")

        data_bucket_flag = (
            "--env DATA_BUCKET_URL=" + data_bucket_url if data_bucket_url else ""
        )

        # 2. Launch data preprocessing
        cluster_name = f"{cluster_prefix}-preprocess"
        preprocess_result = await workflow.execute_activity(
            run_sky_launch,
            SkyLaunchCommand(
                cluster_name,
                f"{clone_path}/data_preprocessing.yaml",
                f"--cloud aws {data_bucket_flag}",
            ),
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=retry_policy,
        )
        workflow.logger.info(f"Preprocessing result: {preprocess_result}")

        # 3. Down the cluster
        down_result = await workflow.execute_activity(
            run_sky_down,
            SkyDownCommand(cluster_name),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )
        workflow.logger.info(f"Down result: {down_result}")

        # 4. Launch training
        cluster_name = f"{cluster_prefix}-train"
        train_result = await workflow.execute_activity(
            run_sky_launch,
            SkyLaunchCommand(
                cluster_name,
                f"{clone_path}/train.yaml",
                f"--cloud aws {data_bucket_flag}",
            ),
            start_to_close_timeout=timedelta(minutes=60),
            retry_policy=retry_policy,
        )
        workflow.logger.info(f"Training result: {train_result}")

        # 5. Execute evaluation on the same
        eval_result = await workflow.execute_activity(
            run_sky_exec,
            SkyExecCommand(
                cluster_name, f"{clone_path}/eval.yaml", f"{data_bucket_flag}"
            ),
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=retry_policy,
        )
        workflow.logger.info(f"Evaluation result: {eval_result}")

        # 6. Down the cluster
        down_result = await workflow.execute_activity(
            run_sky_down,
            SkyDownCommand(cluster_name),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )
        workflow.logger.info(f"Down result: {down_result}")

        # Return the combined result
        return dedent(
            f"""
            Preprocessing
            =============
            {preprocess_result}
            
            Training
            ========
            {train_result}
            
            Evaluation
            ==========
            {eval_result}"""
        )
