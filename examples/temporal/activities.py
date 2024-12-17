import asyncio
import os
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

    proc = await asyncio.create_subprocess_shell(
        command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        activity.logger.info(f"Sky launch output: {stdout}")
        return stdout.decode().strip()  # Return the output from the subprocess
    else:
        activity.logger.error(f"Sky launch failed with error: {stderr}")
        raise Exception(f"sky launch failed.\nStdout: {stdout}\nStderr:{stderr}")


@activity.defn
async def run_sky_down(input: SkyDownCommand) -> str:
    activity.logger.info(f"Running Sky Down on cluster: {input.cluster_name}")

    # Run the sky down command using subprocess
    command = f"sky down -y {input.cluster_name}"

    proc = await asyncio.create_subprocess_shell(
        command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        activity.logger.info(f"Sky down output: {stdout}")
        return stdout.decode().strip()  # Return the output from the subprocess
    else:
        activity.logger.error(f"Sky down failed with error: {stderr}")
        raise Exception(f"Sky down failed.\nStdout: {stdout}\nStderr:{stderr}")


@activity.defn
async def run_sky_exec(input: SkyExecCommand) -> str:
    activity.logger.info(
        f"Running Sky exec on cluster: {input.cluster_name} "
        f"with entrypoint: {input.entrypoint} and flags: {input.flags}"
    )

    # Run the sky exec command using subprocess
    full_command = f"sky exec {input.cluster_name} {input.flags} {input.entrypoint}"

    proc = await asyncio.create_subprocess_shell(
        full_command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        activity.logger.info(f"Sky exec output: {stdout}")
        return stdout.decode().strip()  # Return the output from the subprocess
    else:
        activity.logger.error(f"Sky exec failed with error: {stderr}")
        raise Exception(f"sky exec failed.\nStdout: {stdout}\nStderr:{stderr}")


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

    proc = await asyncio.create_subprocess_shell(
        command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        activity.logger.info(f"git clone output: {stdout}")
        return stdout.decode().strip()  # Return the output from the subprocess
    else:
        activity.logger.error(f"git clone failed with error: {stderr}")
        raise Exception(f"git clone failed.\nStdout: {stdout}\nStderr:{stderr}")
