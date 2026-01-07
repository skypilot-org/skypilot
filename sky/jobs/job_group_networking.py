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
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from sky import clouds as sky_clouds
from sky import sky_logging
from sky.jobs import utils as managed_job_utils
from sky.utils import common_utils

if TYPE_CHECKING:
    from sky import clouds
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
        # Import here to avoid circular dependency
        from sky import clouds as sky_clouds
        return handle.launched_resources.cloud.is_same_cloud(
            sky_clouds.Kubernetes())
    return False


def _get_k8s_internal_svc(
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle'
) -> Optional[str]:
    """Get Kubernetes internal service DNS URL.

    Returns:
        DNS URL like 'pod-name.namespace.svc.cluster.local', or None if
        not available.
    """
    if handle is None or handle.cached_cluster_info is None:
        return None

    try:
        # cached_cluster_info is already a ClusterInfo object
        cluster_info = handle.cached_cluster_info
        head_instance = cluster_info.get_head_instance()
        if head_instance and head_instance.internal_svc:
            return head_instance.internal_svc
    except Exception as e:
        logger.warning(f'Failed to get K8s internal_svc: {e}')

    return None


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


class JobAddressResolver:
    """Resolves job addresses based on placement mode.

    This class abstracts address resolution to support different placement
    modes. Currently only SAME_INFRA is implemented.

    For SAME_INFRA:
        - K8s: Uses internal DNS (pod.namespace.svc.cluster.local)
        - SSH clouds: Uses cluster hostname (resolved via /etc/hosts)

    For future CROSS_INFRA:
        - K8s: Uses external IP (LoadBalancer/Ingress)
        - SSH clouds: Uses public IP
    """

    @staticmethod
    def get_address(
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
        job_name: str,
        job_group_name: str,
        placement: PlacementMode = PlacementMode.SAME_INFRA,
    ) -> str:
        """Get the address for a job.

        Args:
            handle: Resource handle for the job's cluster.
            job_name: Name of the job.
            job_group_name: Name of the JobGroup.
            placement: Placement mode.

        Returns:
            Address string (DNS hostname or IP).
        """
        if placement == PlacementMode.SAME_INFRA:
            return JobAddressResolver._get_internal_address(
                handle, job_name, job_group_name)
        else:
            # Future: CROSS_INFRA
            return JobAddressResolver._get_external_address(handle)

    @staticmethod
    def _get_internal_address(
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',
        job_name: str,
        job_group_name: str,
    ) -> str:
        """Get internal address for SAME_INFRA placement."""
        if _is_kubernetes(handle):
            # K8s: Use native DNS URL
            internal_svc = _get_k8s_internal_svc(handle)
            if internal_svc:
                return internal_svc
            # Fallback: use head IP
            logger.warning(f'K8s internal_svc not available for {job_name}, '
                           'falling back to head_ip')
            if handle and handle.head_ip:
                return handle.head_ip
            return f'{job_name}.{job_group_name}'
        else:
            # SSH clouds: Use hostname (resolved via /etc/hosts)
            # Format: {job_name}.{job_group_name} for head node
            return f'{job_name}.{job_group_name}'

    @staticmethod
    def _get_external_address(
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle',) -> str:
        """Get external address for CROSS_INFRA placement (future)."""
        if _is_kubernetes(handle):
            external_ip = _get_k8s_external_ip(handle)
            if external_ip:
                return external_ip
        # SSH clouds: Use external IP
        if handle and handle.head_ip:
            return handle.head_ip
        raise ValueError('Cannot determine external address for job')


# ============================================================================
# Layer 3: NetworkConfigurator - Platform-specific network configuration
# ============================================================================


def _generate_hosts_entries(
    job_group_name: str,
    tasks_handles: List[Tuple['task_lib.Task',
                              'cloud_vm_ray_backend.CloudVmRayResourceHandle']]
) -> str:
    """Generate /etc/hosts entries for all jobs in a JobGroup.

    For SSH clouds: Only head node (index 0) with format {job_name}.{job_group_name}
    For K8s: Headless service URL with format {job_name}.{job_group_name}

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
        hostname = f'{job_name}.{job_group_name}'

        if _is_kubernetes(handle):
            # K8s: Inject headless service URL
            internal_svc = _get_k8s_internal_svc(handle)
            if internal_svc:
                entries.append(f'{internal_svc} {hostname}')
                logger.debug(f'Host entry (K8s): {internal_svc} -> {hostname}')
            else:
                logger.warning(f'K8s internal_svc not available for {job_name}')
        else:
            # SSH clouds: Inject head node IP only
            if handle.stable_internal_external_ips is None:
                logger.warning(f'Skipping job {task.name}: no IP information')
                continue
            # Only use head node (index 0)
            if len(handle.stable_internal_external_ips) > 0:
                head_ip = handle.stable_internal_external_ips[0][0]
                entries.append(f'{head_ip} {hostname}')
                logger.debug(f'Host entry (SSH): {head_ip} -> {hostname}')

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
    escaped_content = hosts_content.replace("'", "'\\''")
    cmd = (
        f"echo '{escaped_content}' | "  # noqa: Q000
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

        # Separate K8s and SSH handles
        k8s_handles = [(t, h) for t, h in tasks_handles if _is_kubernetes(h)]
        ssh_handles = [(t, h)
                       for t, h in tasks_handles
                       if h is not None and not _is_kubernetes(h)]

        # K8s: No /etc/hosts injection needed (DNS works automatically)
        if k8s_handles:
            logger.info(f'K8s clusters ({len(k8s_handles)} jobs): '
                        'using native DNS, no /etc/hosts injection needed')

        # SSH clouds: Inject /etc/hosts with entries for ALL jobs (SSH + K8s)
        # This allows SSH nodes to resolve K8s jobs via headless service URLs
        if ssh_handles:
            success = await NetworkConfigurator._inject_etc_hosts(
                job_group_name, tasks_handles)  # Pass all handles, not just SSH
            if not success:
                return False

        return True

    @staticmethod
    async def _inject_etc_hosts(
        job_group_name: str,
        tasks_handles: List[Tuple[
            'task_lib.Task', 'cloud_vm_ray_backend.CloudVmRayResourceHandle']],
    ) -> bool:
        """Inject /etc/hosts entries for SSH cloud clusters.

        Args:
            job_group_name: Name of the JobGroup.
            tasks_handles: List of (Task, ResourceHandle) tuples (all jobs, both
                K8s and SSH). Hosts entries will include all jobs, but injection
                only happens on SSH nodes.

        Returns:
            True if all injections succeeded, False otherwise.
        """
        # Count SSH jobs for logging
        ssh_job_count = sum(1 for _, h in tasks_handles
                            if h is not None and not _is_kubernetes(h))
        logger.info(f'Injecting /etc/hosts entries on {ssh_job_count} SSH jobs')

        # Generate hosts content (include all jobs for cross-job resolution)
        hosts_content = _generate_hosts_entries(job_group_name, tasks_handles)
        empty_header = f'# JobGroup: {job_group_name}'
        if not hosts_content or hosts_content == empty_header:
            logger.warning('No hosts entries to inject')
            return True

        logger.debug(f'Hosts entries:\n{hosts_content}')

        # Collect all injection tasks (only for SSH nodes)
        inject_tasks = []
        for task, handle in tasks_handles:
            if handle is None or _is_kubernetes(handle):
                # Skip K8s jobs (they use native DNS)
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
    each other's addresses.

    There are two modes:
    1. Post-launch (tasks_handles provided): Uses actual handles
    2. Pre-launch (tasks + job_id provided): Predicts addresses based on task
       resources

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples. If provided,
            generates address environment variables using actual handles.
        tasks: List of tasks (for pre-launch prediction).
        job_id: Job ID (for pre-launch prediction).
        placement: Placement mode.

    Returns:
        Dict of environment variable name to value.
    """
    env_vars = {
        SKYPILOT_JOBGROUP_NAME_ENV_VAR: job_group_name,
    }

    # Post-launch mode: use actual handles
    if tasks_handles:
        for task, handle in tasks_handles:
            if task.name is None:
                continue
            address = JobAddressResolver.get_address(handle, task.name,
                                                     job_group_name, placement)
            env_var_name = _make_env_var_name(task.name)
            env_vars[env_var_name] = address
        return env_vars

    # Pre-launch mode: predict addresses based on task resources
    if tasks and job_id is not None:
        for task in tasks:
            if task.name is None:
                continue
            address = _predict_job_address(task, task.name, job_group_name,
                                           job_id, placement)
            env_var_name = _make_env_var_name(task.name)
            env_vars[env_var_name] = address

    return env_vars


def _predict_job_address(
    task: 'task_lib.Task',
    job_name: str,
    job_group_name: str,
    job_id: int,
    placement: PlacementMode,
) -> str:
    """Predict job address before launch.

    For SSH clouds: Uses hostname format {job_name}.{job_group_name}
    For K8s: Predicts DNS URL based on cluster naming convention
    """
    # Check if task targets Kubernetes
    is_k8s = False
    if task.resources:
        for resources in task.resources:
            if resources.cloud is not None:
                if resources.cloud.is_same_cloud(sky_clouds.Kubernetes()):
                    is_k8s = True
                    break

    if is_k8s:
        # K8s: Predict DNS URL based on managed_job cluster naming convention
        cluster_name = managed_job_utils.generate_managed_job_cluster_name(
            job_name, job_id)
        # Apply the same transformation that happens during cluster launch
        # to get the actual cluster_name_on_cloud
        k8s_cloud = sky_clouds.Kubernetes()
        cluster_name_on_cloud = common_utils.make_cluster_name_on_cloud(
            cluster_name, max_length=k8s_cloud.max_cluster_name_length())
        # K8s headless service DNS format
        # Default namespace is 'default', can be configured
        namespace = _get_k8s_namespace_for_task(task)
        # Head pod DNS: {cluster_name_on_cloud}-head.{namespace}.svc.cluster.local
        return f'{cluster_name_on_cloud}-head.{namespace}.svc.cluster.local'
    else:
        # SSH clouds: Use hostname format (resolved via /etc/hosts)
        # Format: {job_name}.{job_group_name} for head node
        return f'{job_name}.{job_group_name}'


def _get_k8s_namespace_for_task(task: 'task_lib.Task') -> str:
    """Get Kubernetes namespace for a task.

    Checks task resources for K8s context/region and gets the associated
    namespace. Falls back to 'default'.
    """
    # Try to get namespace from task resources
    if task.resources:
        for resources in task.resources:
            if resources.region:
                # In K8s, region is the context name
                try:
                    from sky.provision.kubernetes import utils as k8s_utils
                    return k8s_utils.get_kube_config_context_namespace(
                        resources.region)
                except Exception:
                    pass

    # Fallback: try current context
    try:
        from sky.provision.kubernetes import utils as k8s_utils
        return k8s_utils.get_kube_config_context_namespace(None)
    except Exception:
        pass

    # Default namespace
    return 'default'


def _make_env_var_name(job_name: str) -> str:
    """Generate environment variable name for a job's address."""
    # Convert job name to uppercase and replace hyphens with underscores
    safe_name = job_name.upper().replace('-', '_')
    return f'SKYPILOT_JOBGROUP_{safe_name}_HOST'
