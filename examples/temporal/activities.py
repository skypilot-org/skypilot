import asyncio
import os
from dataclasses import dataclass

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
    returncode, stdout, stderr = await run_subprocess_with_streams(command)
    return stdout


@activity.defn
async def run_sky_down(input: SkyDownCommand) -> str:
    activity.logger.info(f"Running Sky Down on cluster: {input.cluster_name}")

    # Run the sky down command using subprocess
    command = f"sky down -y {input.cluster_name}"
    returncode, stdout, stderr = await run_subprocess_with_streams(command)
    return stdout


@activity.defn
async def run_sky_exec(input: SkyExecCommand) -> str:
    activity.logger.info(
        f"Running Sky exec on cluster: {input.cluster_name} "
        f"with entrypoint: {input.entrypoint} and flags: {input.flags}"
    )

    # Run the sky exec command using subprocess
    command = f"sky exec {input.cluster_name} {input.flags} {input.entrypoint}"
    returncode, stdout, stderr = await run_subprocess_with_streams(command)
    return stdout


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

    returncode, stdout, stderr = await run_subprocess_with_streams(command)
    return stdout
