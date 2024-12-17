import os
import subprocess
from dataclasses import dataclass

from temporalio import activity


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
        f"Running Sky Launch on cluster: {input.cluster_name} "
        f"with entrypoint: {input.entrypoint} and flags: {input.flags}"
    )

    # Run the provided SkyPilot command using subprocess
    command = f"sky launch -y -c {input.cluster_name} {input.flags} {input.entrypoint}"

    try:
        result = subprocess.run(
            command.split(), capture_output=True, text=True, check=True
        )
        activity.logger.info(f"Sky launch output: {result.stdout}")
        return result.stdout.strip()  # Return the output from the subprocess
    except subprocess.CalledProcessError as e:
        activity.logger.error(f"Sky launch failed with error: {e}")
        activity.logger.error(f"Stdout: {e.stdout}")
        activity.logger.error(f"Stderr: {e.stderr}")
        raise  # Re-raise the exception to indicate failure


@activity.defn
async def run_sky_down(input: SkyDownCommand) -> str:
    activity.logger.info(f"Running Sky Down on cluster: {input.cluster_name}")

    # Run the sky down command using subprocess
    command = f"sky down -y {input.cluster_name}"

    try:
        result = subprocess.run(
            command.split(), capture_output=True, text=True, check=True
        )
        activity.logger.info(f"Sky down output: {result.stdout}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        activity.logger.error(f"Sky down failed with error: {e}")
        activity.logger.error(f"Stdout: {e.stdout}")
        activity.logger.error(f"Stderr: {e.stderr}")
        raise  # Re-raise the exception to indicate failure


@activity.defn
async def run_sky_exec(input: SkyExecCommand) -> str:
    activity.logger.info(
        f"Running Sky exec on cluster: {input.cluster_name} "
        f"with entrypoint: {input.entrypoint} and flags: {input.flags}"
    )

    # Run the sky exec command using subprocess
    full_command = f"sky exec {input.cluster_name} {input.flags} {input.entrypoint}"

    try:
        result = subprocess.run(
            full_command, shell=True, capture_output=True, text=True, check=True
        )
        activity.logger.info(f"Sky exec output: {result.stdout}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        activity.logger.error(f"Sky exec failed with error: {e}")
        activity.logger.error(f"Stdout: {e.stdout}")
        activity.logger.error(f"Stderr: {e.stderr}")
        raise  # Re-raise the exception to indicate failure


@dataclass
class GitCloneInput:
    repo_url: str
    clone_path: str


@activity.defn
async def run_git_clone(input: GitCloneInput) -> str:
    activity.logger.info(
        f"Cloning git repository: {input.repo_url} to {input.clone_path}"
    )

    # Create clone path if it doesn't exist
    os.makedirs(input.clone_path, exist_ok=True)

    # Check if the repository already exists
    if os.path.exists(os.path.join(input.clone_path, ".git")):
        # If it exists, pull the latest changes
        command = f"git -C {input.clone_path} pull"
    else:
        # If it doesn't exist, clone the repository
        command = f"git clone {input.repo_url} {input.clone_path}"

    try:
        result = subprocess.run(
            command.split(), capture_output=True, text=True, check=True
        )
        activity.logger.info(f"Git clone output: {result.stdout}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        activity.logger.error(f"Git clone failed with error: {e}")
        raise  # Re-raise the exception to indicate failure
