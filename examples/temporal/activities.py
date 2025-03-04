import asyncio
import os
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

import yaml
from temporalio import activity


async def run_subprocess_with_streams(command) -> tuple[int, str, str]:
    """Runs a command using asyncio's subprocess module, with streaming prints for STDOUT and STDERR.

    Returns a tuple with the returncode, and the accumulated STDOUT and STDERR from the process."""
    proc = await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.DEVNULL,  # Close stdin to avoid deadlocks
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    print("Streaming stdout and stderr in real-time...\n")

    # Buffers to retain output
    stdout_lines = []
    stderr_lines = []

    # Stream stdout and save output
    async def stream_output(stream, stream_name, buffer):
        while True:
            line = await stream.readline()
            if not line:  # EOF
                break
            decoded_line = line.decode().strip()
            print(f"{stream_name}: {decoded_line}")
            buffer.append(decoded_line)

    # Run the streams concurrently
    await asyncio.gather(
        stream_output(proc.stdout, "STDOUT", stdout_lines),
        stream_output(proc.stderr, "STDERR", stderr_lines),
    )

    # Wait for the process to finish
    returncode = await proc.wait()
    stdout = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)

    if returncode == 0:
        activity.logger.info(f"{command} output: {stdout}")
    else:
        activity.logger.error(f"{command} failed with error: {stderr}")
        raise Exception(f"{command} failed.\nStdout: {stdout}\nStderr:{stderr}")

    return returncode, stdout, stderr


@dataclass
class SkyLaunchCommand:
    """Command to launch a SkyPilot cluster with a task."""
    cluster_name: str
    yaml_content: str
    launch_kwargs: Dict[str, Any] = None
    envs_override: Dict[str, str] = None
    api_server_endpoint: Optional[str] = None


@dataclass
class SkyDownCommand:
    """Command to terminate a SkyPilot cluster."""
    cluster_name: str
    api_server_endpoint: Optional[str] = None


@dataclass
class SkyExecCommand:
    """Command to execute a task on an existing SkyPilot cluster."""
    cluster_name: str
    yaml_content: str
    exec_kwargs: Dict[str, Any] = None
    envs_override: Dict[str, str] = None
    api_server_endpoint: Optional[str] = None


@activity.defn
async def run_sky_launch(input: SkyLaunchCommand) -> str:
    activity.logger.info(
        f"Running Sky Launch on cluster: {input.cluster_name} "
        f"with kwargs: {input.launch_kwargs}"
    )
    
    if input.envs_override:
        activity.logger.info(f"With environment overrides: {input.envs_override}")

    # Set API server endpoint if provided
    if input.api_server_endpoint:
        activity.logger.info(f"Using SkyPilot API server at: {input.api_server_endpoint}")
        os.environ["SKYPILOT_API_SERVER_ENDPOINT"] = input.api_server_endpoint

    # Import sky after setting environment variables
    import sky

    # Parse the YAML content into a task config
    task_config = yaml.safe_load(input.yaml_content)
        
    # Add environment variables if provided
    if input.envs_override:
        if 'envs' not in task_config:
            task_config['envs'] = {}
        task_config['envs'].update(input.envs_override)

    # Create task from config
    task = sky.Task.from_yaml_config(task_config)
    
    # Prepare launch kwargs
    launch_kwargs = {}
    if input.launch_kwargs:
        launch_kwargs = input.launch_kwargs.copy()
    
    # Launch the task, passing kwargs directly to sky.launch
    launch_request_id = sky.launch(task, cluster_name=input.cluster_name, **launch_kwargs)
    job_id, status = sky.stream_and_get(launch_request_id)
    
    # Stream the logs
    log_output = sky.tail_logs(cluster_name=input.cluster_name, job_id=job_id, follow=True)
    
    return f"Launched cluster {input.cluster_name} with job ID {job_id}. Status: {status}\n{log_output}"


@activity.defn
async def run_sky_down(input: SkyDownCommand) -> str:
    activity.logger.info(f"Running Sky Down on cluster: {input.cluster_name}")

    # Set API server endpoint if provided
    if input.api_server_endpoint:
        activity.logger.info(f"Using SkyPilot API server at: {input.api_server_endpoint}")
        os.environ["SKYPILOT_API_SERVER_ENDPOINT"] = input.api_server_endpoint

    # Import sky after setting environment variables
    import sky
    
    down_request_id = sky.down(cluster_name=input.cluster_name)
    sky.stream_and_get(down_request_id)
    
    return f"Terminated cluster {input.cluster_name}."


@activity.defn
async def run_sky_exec(input: SkyExecCommand) -> str:
    activity.logger.info(
        f"Running Sky exec on cluster: {input.cluster_name} "
        f"with kwargs: {input.exec_kwargs}"
    )
    
    if input.envs_override:
        activity.logger.info(f"With environment overrides: {input.envs_override}")

    # Set API server endpoint if provided
    if input.api_server_endpoint:
        activity.logger.info(f"Using SkyPilot API server at: {input.api_server_endpoint}")
        os.environ["SKYPILOT_API_SERVER_ENDPOINT"] = input.api_server_endpoint

    # Import sky after setting environment variables
    import sky

    # Parse the YAML content into a task config
    task_config = yaml.safe_load(input.yaml_content)
        
    # Add environment variables if provided
    if input.envs_override:
        if 'envs' not in task_config:
            task_config['envs'] = {}
        task_config['envs'].update(input.envs_override)

    # Create task from config
    task = sky.Task.from_yaml_config(task_config)
    
    # Prepare exec kwargs
    exec_kwargs = {}
    if input.exec_kwargs:
        exec_kwargs = input.exec_kwargs.copy()
    
    # Execute the task on the existing cluster, passing kwargs directly to sky.exec
    exec_request_id = sky.exec(task, cluster_name=input.cluster_name, **exec_kwargs)
    job_id, handle = sky.stream_and_get(exec_request_id)
    
    # Stream the logs
    log_output = sky.tail_logs(cluster_name=input.cluster_name, job_id=job_id, follow=True)
    
    return f"Executed task on cluster {input.cluster_name} with job ID {job_id}.\n{log_output}"


@dataclass
class GitCloneInput:
    repo_url: str
    clone_path: str
    yaml_file_paths: list[str]
    branch: Optional[str] = None
    api_server_endpoint: Optional[str] = None


@dataclass
class GitCloneOutput:
    success: bool
    message: str
    yaml_contents: Dict[str, str]


@activity.defn
async def run_git_clone(input: GitCloneInput) -> GitCloneOutput:
    activity.logger.info(
        f"Cloning git repository: {input.repo_url} to {input.clone_path}"
    )
    
    if input.branch:
        activity.logger.info(f"Will check out branch: {input.branch}")

    # Set API server endpoint if provided, for consistency
    if input.api_server_endpoint:
        os.environ["SKYPILOT_API_SERVER_ENDPOINT"] = input.api_server_endpoint

    # Create clone path if it doesn't exist
    os.makedirs(input.clone_path, exist_ok=True)

    # Check if the repository already exists
    if os.path.exists(os.path.join(input.clone_path, ".git")):
        # If it exists, pull the latest changes
        command = f"git -C {input.clone_path} pull"
        returncode, stdout, stderr = await run_subprocess_with_streams(command)
        
        # If a branch is specified, check it out
        if input.branch:
            command = f"git -C {input.clone_path} checkout {input.branch}"
            returncode, branch_stdout, branch_stderr = await run_subprocess_with_streams(command)
            stdout += f"\n{branch_stdout}"
    else:
        # If it doesn't exist, clone the repository
        clone_cmd = f"git clone {input.repo_url} {input.clone_path}"
        if input.branch:
            clone_cmd = f"git clone -b {input.branch} {input.repo_url} {input.clone_path}"
        
        returncode, stdout, stderr = await run_subprocess_with_streams(clone_cmd)

    # Now read the YAML files and return their contents
    yaml_contents = {}
    for yaml_path in input.yaml_file_paths:
        full_path = os.path.join(input.clone_path, yaml_path)
        try:
            with open(full_path, 'r') as file:
                yaml_contents[yaml_path] = file.read()
            activity.logger.info(f"Successfully read YAML file: {yaml_path}")
        except Exception as e:
            activity.logger.error(f"Failed to read YAML file {yaml_path}: {str(e)}")
            return GitCloneOutput(
                success=False, 
                message=f"Failed to read YAML file {yaml_path}: {str(e)}",
                yaml_contents={}
            )
    
    return GitCloneOutput(
        success=True,
        message=stdout,
        yaml_contents=yaml_contents
    )
