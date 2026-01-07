"""Networking utilities for JobGroups.

This module provides functions to set up networking between jobs in a JobGroup.

Architecture:
    Layer 1: User Interface (environment variables)
        - SKYPILOT_JOBGROUP_{JOB_NAME}_HOST = <address>
        - SKYPILOT_JOBGROUP_NAME = <job_group_name>

    Layer 2: JobAddressResolver
        - Resolves job addresses based on placement mode
        - Supports internal (SAME_INFRA) and external (future) addresses

    Layer 3: NetworkConfigurator
        - Configures network infrastructure (e.g., /etc/hosts injection)
        - Handles platform-specific differences (K8s vs SSH clouds)

Design Goals:
    - Future-proof: Currently implements SAME_INFRA, but architecture supports
      CROSS_INFRA and mixed cloud scenarios
    - Unified interface: All jobs access addresses via environment variables
    - Platform abstraction: K8s uses native DNS, SSH clouds use /etc/hosts
"""
import asyncio
import enum
import textwrap
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from sky import clouds as sky_clouds
from sky import sky_logging

if TYPE_CHECKING:
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend
    from sky.utils import command_runner

logger = sky_logging.init_logger(__name__)

# Environment variable for JobGroup name, injected into all jobs
SKYPILOT_JOBGROUP_NAME_ENV_VAR = 'SKYPILOT_JOBGROUP_NAME'


class PlacementMode(enum.Enum):
    """Placement mode for JobGroup networking."""
    SAME_INFRA = 'same_infra'  # All jobs on same K8s cluster or cloud AZ
    # Future: CROSS_INFRA = 'cross_infra'  # Jobs on different infras


# ============================================================================
# Layer 2: JobAddressResolver - Address resolution abstraction
# ============================================================================


def _is_kubernetes(
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle') -> bool:
    """Check if handle is for a Kubernetes cluster."""
    if handle is None:
        return False
    if handle.launched_resources and handle.launched_resources.cloud:
        return handle.launched_resources.cloud.is_same_cloud(
            sky_clouds.Kubernetes())
    return False


def _get_k8s_namespace_from_handle(
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle') -> str:
    """Get Kubernetes namespace from a resource handle.

    Returns:
        Namespace string, defaults to 'default' if not available.
    """
    if handle is None:
        return 'default'

    # Try to get namespace from launched_resources
    if handle.launched_resources and handle.launched_resources.region:
        # In K8s, region is the context name
        try:
            from sky.provision.kubernetes import utils as k8s_utils
            return k8s_utils.get_kube_config_context_namespace(
                handle.launched_resources.region)
        except Exception:
            pass

    # Fallback to default namespace
    return 'default'


def _construct_k8s_internal_svc(cluster_name_on_cloud: str, namespace: str,
                                node_idx: int) -> str:
    """Construct Kubernetes internal service DNS URL.

    The pod creation logic guarantees this format.

    Args:
        cluster_name_on_cloud: Cluster name on cloud
        namespace: Kubernetes namespace
        node_idx: Node index (0 for head, 1+ for workers)

    Returns:
        DNS URL like '{cluster}-head.{namespace}.svc.cluster.local'
    """
    if node_idx == 0:
        # Head node
        return f'{cluster_name_on_cloud}-head.{namespace}.svc.cluster.local'
    else:
        # Worker node
        return (f'{cluster_name_on_cloud}-worker{node_idx}.'
                f'{namespace}.svc.cluster.local')


def _get_k8s_external_ip(
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle'
) -> Optional[str]:
    """Get Kubernetes external IP (LoadBalancer/Ingress).

    This is for future CROSS_INFRA support.

    Returns:
        External IP or hostname, or None if not available.
    """
    # TODO: Implement for CROSS_INFRA support
    # Need to check port_mode and get LoadBalancer/Ingress IP accordingly
    if handle is None or handle.head_ip is None:
        return None
    return handle.head_ip


def _get_job_address(job_name: str,
                     job_group_name: str,
                     node_idx: int = 0) -> str:
    """Get the address for a job node.

    Returns the hostname that will be resolved via /etc/hosts injection.
    Both K8s and SSH clouds use this same hostname format.

    Args:
        job_name: Name of the job.
        job_group_name: Name of the JobGroup.
        node_idx: Node index (0 for head, 1+ for workers). Defaults to 0.

    Returns:
        Hostname string in format: {job_name}-{node_idx}.{job_group_name}
    """
    return f'{job_name}-{node_idx}.{job_group_name}'


# ============================================================================
# Layer 3: NetworkConfigurator - Platform-specific network configuration
# ============================================================================


def _generate_hosts_entries(
    job_group_name: str,
    tasks_handles: List[Tuple['task_lib.Task',
                              'cloud_vm_ray_backend.CloudVmRayResourceHandle']]
) -> str:
    """Generate /etc/hosts entries for all jobs in a JobGroup.

    For SSH clouds: Each node with format {job_name}-{node_idx}.{job_group_name}
    For K8s: Headless service URLs for head and workers with format
        {job_name}-{node_idx}.{job_group_name}

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples for each job.

    Returns:
        String containing /etc/hosts entries, one per line.
    """
    entries = []
    entries.append(f'# JobGroup: {job_group_name}')

    for task, handle in tasks_handles:
        if handle is None:
            logger.warning(f'Skipping job {task.name}: no handle')
            continue

        job_name = task.name

        if _is_kubernetes(handle):
            # K8s: Inject headless service URLs for head and workers
            # Construct internal_svc URLs using the guaranteed format
            cluster_name_on_cloud = handle.cluster_name_on_cloud
            namespace = _get_k8s_namespace_from_handle(handle)

            # Get number of nodes
            num_nodes = (len(handle.stable_internal_external_ips)
                         if handle.stable_internal_external_ips else 1)

            # Generate entries for all nodes (head + workers)
            for node_idx in range(num_nodes):
                hostname = f'{job_name}-{node_idx}.{job_group_name}'
                internal_svc = _construct_k8s_internal_svc(
                    cluster_name_on_cloud, namespace, node_idx)
                entries.append(f'{internal_svc} {hostname}')
                node_type = 'head' if node_idx == 0 else f'worker{node_idx}'
                logger.debug(f'Host entry (K8s {node_type}): '
                             f'{internal_svc} -> {hostname}')
        else:
            # SSH clouds: Inject all nodes with IPs
            if handle.stable_internal_external_ips is None:
                logger.warning(f'Skipping job {task.name}: no IP information')
                continue
            for node_idx, (internal_ip,
                           _) in enumerate(handle.stable_internal_external_ips):
                hostname = f'{job_name}-{node_idx}.{job_group_name}'
                entries.append(f'{internal_ip} {hostname}')
                logger.debug(f'Host entry (SSH): {internal_ip} -> {hostname}')

    return '\n'.join(entries)


async def _inject_hosts_on_node(
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
    # pylint: disable=invalid-string-quote
    escaped_content = hosts_content.replace("'", "'\\''")  # noqa: Q000
    cmd = (
        f"echo '{escaped_content}' | "  # noqa: Q000
        'sudo tee -a /etc/hosts > /dev/null')
    # pylint: enable=invalid-string-quote

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


class NetworkConfigurator:
    """Configures network infrastructure for JobGroups.

    Handles platform-specific network configuration:
    - K8s: No configuration needed (DNS works automatically)
    - SSH clouds: Injects /etc/hosts entries for hostname resolution
    """

    @staticmethod
    async def setup(
        job_group_name: str,
        tasks_handles: List[Tuple[
            'task_lib.Task', 'cloud_vm_ray_backend.CloudVmRayResourceHandle']],
        placement: PlacementMode = PlacementMode.SAME_INFRA,
    ) -> bool:
        """Set up network configuration for JobGroup.

        Args:
            job_group_name: Name of the JobGroup.
            tasks_handles: List of (Task, ResourceHandle) tuples.
            placement: Placement mode.

        Returns:
            True if all configuration succeeded, False otherwise.
        """
        if placement != PlacementMode.SAME_INFRA:
            # Future: Handle CROSS_INFRA
            logger.warning(f'Unsupported placement mode: {placement}')
            return False

        # Inject /etc/hosts on all nodes (both K8s and SSH)
        # This maps the unified hostname format {job_name}-{node_idx}.{job_group_name}
        # to actual addresses (internal_svc URLs for K8s, IPs for SSH)
        success = await NetworkConfigurator._inject_etc_hosts(
            job_group_name, tasks_handles)
        if not success:
            return False

        return True

    @staticmethod
    async def _inject_etc_hosts(
        job_group_name: str,
        tasks_handles: List[Tuple[
            'task_lib.Task', 'cloud_vm_ray_backend.CloudVmRayResourceHandle']],
    ) -> bool:
        """Inject /etc/hosts entries for all clusters in the JobGroup.

        This maps the unified hostname format to actual addresses:
        - K8s: internal_svc URLs
        - SSH: internal IPs

        Args:
            job_group_name: Name of the JobGroup.
            tasks_handles: List of (Task, ResourceHandle) tuples for all jobs.

        Returns:
            True if all injections succeeded, False otherwise.
        """
        logger.info(
            f'Injecting /etc/hosts entries on all {len(tasks_handles)} jobs')

        # Generate hosts content (include all jobs for cross-job resolution)
        hosts_content = _generate_hosts_entries(job_group_name, tasks_handles)
        empty_header = f'# JobGroup: {job_group_name}'
        if not hosts_content or hosts_content == empty_header:
            logger.warning('No hosts entries to inject')
            return True

        logger.debug(f'Hosts entries:\n{hosts_content}')

        # Collect all injection tasks (for all nodes: K8s and SSH)
        inject_tasks = []
        for task, handle in tasks_handles:
            if handle is None:
                continue

            # Use handle.get_command_runners() (not hardcoded SSHCommandRunner)
            try:
                runners = handle.get_command_runners()
            except Exception as e:
                logger.warning(
                    f'Failed to get command runners for {task.name}: {e}')
                continue

            for node_idx, runner in enumerate(runners):
                inject_tasks.append(_inject_hosts_on_node(
                    runner, hosts_content))
                logger.debug(f'Queued injection for {task.name}-{node_idx}')

        if not inject_tasks:
            logger.warning('No nodes to inject hosts entries')
            return True

        # Execute all injections in parallel
        logger.info(f'Injecting hosts entries on {len(inject_tasks)} nodes...')
        results = await asyncio.gather(*inject_tasks, return_exceptions=True)

        # Check results
        success_count = sum(
            1 for r in results if not isinstance(r, Exception) and r)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f'Node {i} injection failed: {result}')
            elif not result:
                logger.error(f'Node {i} injection failed')

        logger.info(
            f'Hosts injection: {success_count}/{len(results)} succeeded')
        return success_count == len(results)


# ============================================================================
# Layer 4: Public API
# ============================================================================


async def setup_job_group_networking(
    job_group_name: str,
    tasks_handles: List[Tuple['task_lib.Task',
                              'cloud_vm_ray_backend.CloudVmRayResourceHandle']],
    ssh_credentials: Optional[Dict[str, str]] = None,
    placement: PlacementMode = PlacementMode.SAME_INFRA,
) -> bool:
    """Set up networking for all jobs in a JobGroup.

    This is the main entry point for JobGroup networking setup.

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples for each job.
        ssh_credentials: Optional SSH credentials (for backward compatibility,
            now uses handle.get_command_runners() instead).
        placement: Placement mode (default: SAME_INFRA).

    Returns:
        True if setup succeeded, False otherwise.
    """
    del ssh_credentials  # Now using handle.get_command_runners()

    logger.info(f'Setting up networking for JobGroup: {job_group_name}')

    # Configure network infrastructure (e.g., /etc/hosts)
    success = await NetworkConfigurator.setup(job_group_name, tasks_handles,
                                              placement)

    return success


def get_job_group_env_vars(
    job_group_name: str,
    tasks_handles: Optional[List[
        Tuple['task_lib.Task',
              'cloud_vm_ray_backend.CloudVmRayResourceHandle']]] = None,
    tasks: Optional[List['task_lib.Task']] = None,
    job_id: Optional[int] = None,
    placement: PlacementMode = PlacementMode.SAME_INFRA,
) -> Dict[str, str]:
    """Get environment variables for JobGroup jobs.

    This function generates environment variables that allow jobs to discover
    each other's addresses using the consistent hostname format.

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples.
        tasks: List of tasks (alternative to tasks_handles).
        job_id: Job ID (unused, kept for backward compatibility).
        placement: Placement mode (unused, kept for backward compatibility).

    Returns:
        Dict of environment variable name to value.
    """
    del job_id, placement  # Unused, reserved for future use

    env_vars = {
        SKYPILOT_JOBGROUP_NAME_ENV_VAR: job_group_name,
    }

    # Get task list from either tasks_handles or tasks
    task_list: List['task_lib.Task'] = []
    if tasks_handles:
        task_list = [task for task, _ in tasks_handles]
    elif tasks:
        task_list = tasks

    # Generate environment variables for all jobs
    for task in task_list:
        if task.name is None:
            continue
        # All jobs use head node address (node index 0)
        address = _get_job_address(task.name, job_group_name, node_idx=0)
        env_var_name = _make_env_var_name(task.name)
        env_vars[env_var_name] = address

    return env_vars


def _make_env_var_name(job_name: str) -> str:
    """Generate environment variable name for a job's address."""
    # Convert job name to uppercase and replace hyphens with underscores
    safe_name = job_name.upper().replace('-', '_')
    return f'SKYPILOT_JOBGROUP_{safe_name}_HOST'


def generate_wait_for_networking_script(job_group_name: str,
                                        other_job_names: List[str]) -> str:
    """Generate a bash script to wait for /etc/hosts injection.

    This script should be prepended to task.setup to ensure networking
    is ready before the task starts.

    Args:
        job_group_name: Name of the JobGroup.
        other_job_names: List of other job names in the group to wait for.

    Returns:
        Bash script as a string.
    """
    # Generate hostnames to wait for
    hostnames = [
        f'{job_name}-0.{job_group_name}' for job_name in other_job_names
    ]

    if not hostnames:
        # No other jobs to wait for
        return ''

    # Create a script that waits for all hostnames to be resolvable
    hostname_list = ' '.join(hostnames)
    script = textwrap.dedent(f"""
        # Wait for JobGroup networking to be ready
        echo "[SkyPilot] Waiting for JobGroup networking setup..."
        HOSTNAMES="{hostname_list}"
        MAX_WAIT=300  # 5 minutes
        ELAPSED=0
        for hostname in $HOSTNAMES; do
          while ! grep -q "$hostname" /etc/hosts 2>/dev/null; do
            if [ $ELAPSED -ge $MAX_WAIT ]; then
              echo "[SkyPilot] Error: Timed out waiting for $hostname in /etc/hosts"
              exit 1
            fi
            echo "[SkyPilot] Waiting for $hostname to appear in /etc/hosts..."
            sleep 2
            ELAPSED=$((ELAPSED + 2))
          done
          echo "[SkyPilot] Found $hostname in /etc/hosts"
        done
        echo "[SkyPilot] JobGroup networking is ready!"
    """)
    return script.strip()
