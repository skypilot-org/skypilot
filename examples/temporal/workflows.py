from dataclasses import dataclass
from datetime import timedelta
from textwrap import dedent
from typing import Optional, Dict, Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import (
        GitCloneInput,
        GitCloneOutput,
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
    envs_override: Optional[Dict[str, str]] = None
    branch: Optional[str] = None
    api_server_endpoint: Optional[str] = None


# Define the fixed worker queue name
WORKER_TASK_QUEUE = "skypilot-workflow-queue"


@workflow.defn
class SkyPilotWorkflow:
    @workflow.run
    async def run(self, input: SkyPilotWorkflowInput) -> str:
        cluster_prefix = input.cluster_prefix
        repo_url = input.repo_url
        api_server_endpoint = input.api_server_endpoint
        branch = input.branch
        
        # Configure launch/exec kwargs
        launch_kwargs = {}
        
        exec_kwargs = {}

        retry_policy = RetryPolicy(
            maximum_attempts=3,
            maximum_interval=timedelta(seconds=2),
        )

        workflow.logger.info(f"Using worker task queue: {WORKER_TASK_QUEUE}")

        workflow.logger.info(
            f"Running SkyPilot workflow with cluster prefix: {cluster_prefix}"
        )
        
        if api_server_endpoint:
            workflow.logger.info(f"Using SkyPilot API server at: {api_server_endpoint}")
            
        if input.envs_override:
            workflow.logger.info(f"Using environment overrides: {input.envs_override}")
            
        if branch:
            workflow.logger.info(f"Using branch: {branch}")

        # 1. Clone the repository and retrieve YAML contents
        clone_path = "/tmp/skypilot_repo"
        yaml_paths = ["data_preprocessing.yaml", "train.yaml", "eval.yaml"]
        
        clone_result = await workflow.execute_activity(
            run_git_clone,
            GitCloneInput(
                repo_url=repo_url, 
                clone_path=clone_path, 
                yaml_file_paths=yaml_paths,
                branch=branch,
                api_server_endpoint=api_server_endpoint
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
            task_queue=WORKER_TASK_QUEUE,
        )
        
        if not clone_result.success:
            raise Exception(f"Failed to clone repository: {clone_result.message}")
            
        workflow.logger.info(f"Cloned repository and retrieved {len(clone_result.yaml_contents)} YAML files")
        
        # 2. Launch data preprocessing
        preprocess_yaml = clone_result.yaml_contents["data_preprocessing.yaml"]
        cluster_name = f"{cluster_prefix}-preprocess"
        
        preprocess_result = await workflow.execute_activity(
            run_sky_launch,
            SkyLaunchCommand(
                cluster_name=cluster_name,
                yaml_content=preprocess_yaml,
                launch_kwargs=launch_kwargs,
                envs_override=input.envs_override,
                api_server_endpoint=api_server_endpoint
            ),
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=retry_policy,
            task_queue=WORKER_TASK_QUEUE,
        )
        workflow.logger.info(f"Preprocessing result: {preprocess_result}")

        # 3. Down the cluster
        down_result = await workflow.execute_activity(
            run_sky_down,
            SkyDownCommand(
                cluster_name=cluster_name,
                api_server_endpoint=api_server_endpoint
            ),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
            task_queue=WORKER_TASK_QUEUE,
        )
        workflow.logger.info(f"Down result: {down_result}")

        # 4. Launch training
        train_yaml = clone_result.yaml_contents["train.yaml"]
        cluster_name = f"{cluster_prefix}-train"
        
        train_result = await workflow.execute_activity(
            run_sky_launch,
            SkyLaunchCommand(
                cluster_name=cluster_name,
                yaml_content=train_yaml,
                launch_kwargs=launch_kwargs,
                envs_override=input.envs_override,
                api_server_endpoint=api_server_endpoint
            ),
            start_to_close_timeout=timedelta(minutes=60),
            retry_policy=retry_policy,
            task_queue=WORKER_TASK_QUEUE,
        )
        workflow.logger.info(f"Training result: {train_result}")

        # 5. Execute evaluation on the same cluster
        eval_yaml = clone_result.yaml_contents["eval.yaml"]
        
        eval_result = await workflow.execute_activity(
            run_sky_exec,
            SkyExecCommand(
                cluster_name=cluster_name,
                yaml_content=eval_yaml,
                exec_kwargs=exec_kwargs,
                envs_override=input.envs_override,
                api_server_endpoint=api_server_endpoint
            ),
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=retry_policy,
            task_queue=WORKER_TASK_QUEUE,
        )
        workflow.logger.info(f"Evaluation result: {eval_result}")

        # 6. Down the cluster
        down_result = await workflow.execute_activity(
            run_sky_down,
            SkyDownCommand(
                cluster_name=cluster_name,
                api_server_endpoint=api_server_endpoint
            ),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
            task_queue=WORKER_TASK_QUEUE,
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
