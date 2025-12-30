"""Networking utilities for JobGroups.

This module provides functions to set up networking between jobs in a JobGroup,
primarily through /etc/hosts injection to enable hostname-based communication.
"""
import asyncio
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from sky import sky_logging
from sky.utils import command_runner

if TYPE_CHECKING:
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)

# Environment variable for JobGroup name, injected into all jobs
SKYPILOT_JOBGROUP_NAME_ENV_VAR = 'SKYPILOT_JOBGROUP_NAME'


def generate_hosts_entries(
    job_group_name: str,
    tasks_handles: List[Tuple['task_lib.Task',
                              'cloud_vm_ray_backend.CloudVmRayResourceHandle']]
) -> str:
    """Generate /etc/hosts entries for all jobs in a JobGroup.

    Each node gets a hostname in the format:
        {job_name}-{node_index}.{job_group_name}

    For example, with job_group_name='rl-experiment' and a job named 'trainer'
    with 2 nodes:
        10.0.0.1 trainer-0.rl-experiment
        10.0.0.2 trainer-1.rl-experiment

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples for each job.

    Returns:
        String containing /etc/hosts entries, one per line.
    """
    entries = []
    entries.append(f'# JobGroup: {job_group_name}')

    for task, handle in tasks_handles:
        if handle is None or handle.stable_internal_external_ips is None:
            logger.warning(f'Skipping job {task.name}: no IP information')
            continue

        job_name = task.name
        for node_idx, (internal_ip, external_ip) in enumerate(
                handle.stable_internal_external_ips):
            del external_ip  # Unused, only need internal IP for hosts file
            hostname = f'{job_name}-{node_idx}.{job_group_name}'
            entries.append(f'{internal_ip} {hostname}')
            logger.debug(f'Host entry: {internal_ip} -> {hostname}')

    return '\n'.join(entries)


async def inject_hosts_on_node(
    runner: 'command_runner.CommandRunner',
    hosts_content: str,
) -> bool:
    """Inject /etc/hosts entries on a single node.

    Args:
        runner: CommandRunner for the target node.
        hosts_content: Content to append to /etc/hosts.

    Returns:
        True if successful, False otherwise.
    """
    # Use tee with sudo to append to /etc/hosts
    # Escape single quotes in content for shell command
    escaped_content = hosts_content.replace("'", "'\\''")  # pylint: disable=invalid-string-quote
    cmd = (
        f"echo '{escaped_content}' | "  # pylint: disable=invalid-string-quote
        'sudo tee -a /etc/hosts > /dev/null')

    try:
        returncode, _, stderr = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: runner.run(cmd, stream_logs=False, require_outputs=True))
        if returncode != 0:
            logger.error(f'Failed to inject /etc/hosts: {stderr}')
            return False
        return True
    except Exception as e:
        logger.error(f'Exception while injecting /etc/hosts: {e}')
        return False


async def setup_job_group_networking(
    job_group_name: str,
    tasks_handles: List[Tuple['task_lib.Task',
                              'cloud_vm_ray_backend.CloudVmRayResourceHandle']],
    ssh_credentials: Optional[Dict[str, str]] = None,
) -> bool:
    """Set up networking for all jobs in a JobGroup.

    This function:
    1. Generates /etc/hosts entries for all nodes
    2. Injects entries on all nodes in parallel

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples for each job.
        ssh_credentials: Optional SSH credentials dict.

    Returns:
        True if all injections succeeded, False otherwise.
    """
    logger.info(f'Setting up networking for JobGroup: {job_group_name}')

    # Generate hosts content
    hosts_content = generate_hosts_entries(job_group_name, tasks_handles)
    if not hosts_content or hosts_content == f'# JobGroup: {job_group_name}':
        logger.warning('No hosts entries to inject')
        return True

    logger.debug(f'Hosts entries:\n{hosts_content}')

    # Collect all nodes to inject
    inject_tasks = []
    for task, handle in tasks_handles:
        if handle is None or handle.stable_internal_external_ips is None:
            continue

        for node_idx, (internal_ip, external_ip) in enumerate(
                handle.stable_internal_external_ips):
            del internal_ip  # Unused, only need external IP for SSH
            # Get SSH port for this node
            ssh_port = 22
            if (handle.stable_ssh_ports is not None and
                    node_idx < len(handle.stable_ssh_ports)):
                ssh_port = handle.stable_ssh_ports[node_idx]

            # Create command runner for this node
            # Default SSH user is 'sky' which is the standard SkyPilot user
            default_ssh_user = 'sky'
            runner = command_runner.SSHCommandRunner(
                node=(external_ip, ssh_port),
                ssh_user=ssh_credentials.get('ssh_user', default_ssh_user)
                if ssh_credentials else default_ssh_user,
                ssh_private_key=ssh_credentials.get('ssh_private_key')
                if ssh_credentials else None,
            )

            inject_tasks.append(inject_hosts_on_node(runner, hosts_content))
            logger.debug(f'Queued injection for {task.name}-{node_idx} '
                         f'({external_ip}:{ssh_port})')

    if not inject_tasks:
        logger.warning('No nodes to inject hosts entries')
        return True

    # Execute all injections in parallel
    logger.info(f'Injecting hosts entries on {len(inject_tasks)} nodes...')
    results = await asyncio.gather(*inject_tasks, return_exceptions=True)

    # Check results
    success_count = 0
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f'Node {i} injection failed with exception: {result}')
        elif result:
            success_count += 1
        else:
            logger.error(f'Node {i} injection failed')

    logger.info(f'Hosts injection complete: {success_count}/{len(results)} '
                f'nodes succeeded')

    return success_count == len(results)


def get_job_group_env_vars(job_group_name: str) -> Dict[str, str]:
    """Get environment variables to inject for JobGroup jobs.

    Args:
        job_group_name: Name of the JobGroup.

    Returns:
        Dict of environment variable name to value.
    """
    return {
        SKYPILOT_JOBGROUP_NAME_ENV_VAR: job_group_name,
    }
