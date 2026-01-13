"""Networking utilities for JobGroups.

This module provides functions to set up networking between jobs in a JobGroup.

Architecture:
    Layer 1: User Interface (environment variables)
        - SKYPILOT_JOBGROUP_HOST_{JOB_NAME} = <address>
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
import os
import tempfile
import textwrap
import traceback
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
            # pylint: disable=import-outside-toplevel
            from sky.provision.kubernetes import utils as k8s_utils
            return k8s_utils.get_kube_config_context_namespace(
                handle.launched_resources.region)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to get K8s namespace from handle, '
                         f'falling back to default: {e}')

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


def _generate_k8s_dns_mappings(
    job_group_name: str,
    tasks_handles: List[Tuple['task_lib.Task',
                              'cloud_vm_ray_backend.CloudVmRayResourceHandle']]
) -> List[Tuple[str, str]]:
    """Generate K8s DNS to hostname mappings for background updater.

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples.

    Returns:
        List of (k8s_dns, simple_hostname) tuples.
    """
    mappings = []
    for task, handle in tasks_handles:
        if handle is None or not _is_kubernetes(handle):
            continue

        job_name = task.name
        cluster_name_on_cloud = handle.cluster_name_on_cloud
        namespace = _get_k8s_namespace_from_handle(handle)
        num_nodes = (len(handle.stable_internal_external_ips)
                     if handle.stable_internal_external_ips else 1)

        for node_idx in range(num_nodes):
            hostname = f'{job_name}-{node_idx}.{job_group_name}'
            internal_svc = _construct_k8s_internal_svc(cluster_name_on_cloud,
                                                       namespace, node_idx)
            mappings.append((internal_svc, hostname))
            node_type = 'head' if node_idx == 0 else f'worker{node_idx}'
            logger.debug(f'K8s DNS mapping ({node_type}): '
                         f'{internal_svc} -> {hostname}')

    return mappings


def _generate_hosts_entries(
    job_group_name: str,
    tasks_handles: List[Tuple['task_lib.Task',
                              'cloud_vm_ray_backend.CloudVmRayResourceHandle']]
) -> str:
    """Generate /etc/hosts entries for SSH cloud nodes.

    K8s nodes use a background updater to dynamically resolve IPs.

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

        # Only SSH clouds get static /etc/hosts entries
        # K8s uses background updater instead
        if not _is_kubernetes(handle):
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
        loop = asyncio.get_running_loop()
        returncode, _, stderr = await loop.run_in_executor(
            None,
            lambda: runner.run(cmd, stream_logs=False, require_outputs=True))
        if returncode != 0:
            logger.error(f'Failed to inject /etc/hosts: {stderr}')
            return False
        return True
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Exception while injecting /etc/hosts: {e}')
        return False


def generate_k8s_dns_updater_script(dns_mappings: List[Tuple[str, str]],
                                    job_group_name: str) -> str:
    """Generate background script to update /etc/hosts with K8s DNS IPs.

    Args:
        dns_mappings: List of (k8s_dns, simple_hostname) tuples.
        job_group_name: Name of the job group (for process identification).

    Returns:
        Bash script as a string (standalone, without nohup wrapper).
    """
    if not dns_mappings:
        return ''

    # Build mapping pairs for the script
    mapping_pairs = ' '.join(
        [f'{dns}:{hostname}' for dns, hostname in dns_mappings])

    # Sanitize job group name for use in process identifier
    safe_job_group_name = job_group_name.replace(' ', '_')
    process_id = f'skypilot-jobgroup-dns-updater-{safe_job_group_name}'
    log_file = f'/tmp/{process_id}.log'

    script = textwrap.dedent(f"""
        #!/bin/bash
        # Kill any existing DNS updater for this job group (idempotency)
        pkill -f "{process_id}" 2>/dev/null || true
        sleep 0.5

        # Background K8s DNS to IP updater for /etc/hosts
        # Process identifier: {process_id}
        MAPPINGS="{mapping_pairs}"
        MARKER="# SkyPilot JobGroup K8s entries"

        echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Starting DNS updater for {job_group_name}"
        echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Monitoring mappings: $MAPPINGS"
        echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Log file: {log_file}"

        while true; do
          # Build new entries
          new_entries=""
          needs_update=0
          for mapping in $MAPPINGS; do
            k8s_dns="${{mapping%%:*}}"
            simple_name="${{mapping##*:}}"
            # Resolve K8s DNS to IP
            ip=$(getent hosts "$k8s_dns" 2>/dev/null | awk '{{print $1}}')
            if [ -n "$ip" ]; then
              new_entries="${{new_entries}}$ip $simple_name  $MARKER
"
              # Check if current IP differs from /etc/hosts
              current_ip=$(getent hosts "$simple_name" 2>/dev/null | awk '{{print $1}}')
              if [ "$ip" != "$current_ip" ]; then
                needs_update=1
                echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] IP changed for $simple_name: $current_ip -> $ip"
              fi
            else
              echo "$(date '+%Y-%m-%d %H:%M:%S') [DEBUG] Waiting to resolve $k8s_dns"
            fi
          done

          # Only update /etc/hosts if IPs have changed
          if [ -n "$new_entries" ] && [ $needs_update -eq 1 ]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Updating /etc/hosts"
            # In K8s, /etc/hosts is mounted by kubelet and cannot be replaced (mv).
            # Instead, we filter and rewrite in-place using tee.
            # 1. Read existing content without our markers
            existing=$(sudo grep -v "$MARKER" /etc/hosts 2>/dev/null || true)
            # 2. Write back existing + new entries using tee
            if echo -e "$existing\\n$new_entries" | sudo tee /etc/hosts > /dev/null; then
              echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Successfully updated /etc/hosts"
            else
              echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Failed to update /etc/hosts"
            fi
          fi
          sleep 5
        done
    """)
    return script.strip()


async def _start_k8s_dns_updater_on_node(
    runner: 'command_runner.CommandRunner',
    dns_mappings: List[Tuple[str, str]],
    job_group_name: str,
) -> bool:
    """Start background DNS updater on a K8s node.

    The updater resolves K8s service DNS names to IPs and keeps
    /etc/hosts updated.

    Args:
        runner: CommandRunner for the target node.
        dns_mappings: List of (k8s_dns, simple_hostname) tuples.
        job_group_name: Name of the job group (for process identification).

    Returns:
        True if successful, False otherwise.
    """
    if not dns_mappings:
        return True

    updater_script = generate_k8s_dns_updater_script(dns_mappings,
                                                     job_group_name)
    if not updater_script:
        return True

    # Sanitize job group name for file paths
    safe_job_group_name = job_group_name.replace(' ', '_')
    process_id = f'skypilot-jobgroup-dns-updater-{safe_job_group_name}'
    script_path = f'/tmp/{process_id}.sh'
    log_path = f'/tmp/{process_id}.log'

    loop = asyncio.get_running_loop()

    try:
        # Upload script via rsync (DNS updater script is always long)
        with tempfile.NamedTemporaryFile('w',
                                         prefix='sky_dns_updater_',
                                         delete=False) as f:
            f.write(updater_script)
            f.flush()
            local_script_path = f.name

        try:
            logger.info(f'Uploading DNS updater script for {job_group_name}...')
            await loop.run_in_executor(
                None, lambda: runner.rsync(source=local_script_path,
                                           target=script_path,
                                           up=True,
                                           stream_logs=False))
            logger.info(f'DNS updater script uploaded to {script_path}')
        finally:
            os.remove(local_script_path)

        # Make executable and run in background (same as pre-skylet version)
        run_cmd = (f'chmod +x {script_path} && '
                   f'(nohup {script_path} < /dev/null > {log_path} 2>&1 &) && '
                   f'sleep 0.1')
        logger.info(f'Starting DNS updater in background (log: {log_path})...')
        returncode, _, stderr = await loop.run_in_executor(
            None,
            lambda: runner.run(run_cmd, stream_logs=False, require_outputs=True)
        )

        # Exit code 143 (SIGTERM) is expected from kubectl exec closing connection
        # The background process continues running despite this
        if returncode != 0 and returncode != 143:
            logger.error(
                f'Failed to start DNS updater: returncode={returncode}, stderr={stderr}'
            )
            return False

        # Verify the process started (separate command)
        check_cmd = f'pgrep -f "{process_id}" > /dev/null && echo "running" || echo "not running"'
        returncode, stdout, _ = await loop.run_in_executor(
            None, lambda: runner.run(
                check_cmd, stream_logs=False, require_outputs=True))

        status = stdout.strip() if stdout else 'unknown'
        logger.info(f'DNS updater status: {status}')
        return True
    except Exception as e:  # pylint: disable=broad-except
        logger.error(f'Exception while starting DNS updater: {e}')
        logger.error(traceback.format_exc())
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
        # This maps the unified hostname format:
        # {job_name}-{node_idx}.{job_group_name}
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
        - K8s: Write DNS mappings file for skylet's HostUpdater
        - SSH: Inject static internal IPs

        Args:
            job_group_name: Name of the JobGroup.
            tasks_handles: List of (Task, ResourceHandle) tuples for all jobs.

        Returns:
            True if all injections succeeded, False otherwise.
        """
        logger.info(f'Setting up networking on all {len(tasks_handles)} jobs')

        # Generate static hosts content for SSH nodes
        ssh_hosts_content = _generate_hosts_entries(job_group_name,
                                                    tasks_handles)

        # Generate K8s DNS mappings for background updater
        k8s_dns_mappings = _generate_k8s_dns_mappings(job_group_name,
                                                      tasks_handles)

        # Collect all injection tasks (for all nodes: K8s and SSH)
        inject_tasks = []
        for task, handle in tasks_handles:
            if handle is None:
                continue

            is_k8s = _is_kubernetes(handle)

            # Use handle.get_command_runners() (not hardcoded SSHCommandRunner)
            try:
                runners = handle.get_command_runners()
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Failed to get command runners for {task.name}: {e}')
                continue

            for node_idx, runner in enumerate(runners):
                if is_k8s:
                    # K8s: Start background DNS updater on each node
                    inject_tasks.append(
                        _start_k8s_dns_updater_on_node(runner, k8s_dns_mappings,
                                                       job_group_name))
                else:
                    # SSH: Inject static /etc/hosts
                    if ssh_hosts_content:
                        inject_tasks.append(
                            _inject_hosts_on_node(runner, ssh_hosts_content))
                logger.debug(
                    f'Queued networking setup for {task.name}-{node_idx}')

        if not inject_tasks:
            logger.warning('No nodes to set up networking')
            return True

        # Execute all injections in parallel
        logger.info(f'Setting up networking on {len(inject_tasks)} nodes...')
        logger.debug(f'Waiting for {len(inject_tasks)} async tasks to complete')
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*inject_tasks, return_exceptions=True),
                timeout=60.0  # 60 second timeout
            )
        except asyncio.TimeoutError:
            logger.error('Networking setup timed out after 60 seconds')
            return False
        logger.debug(f'All {len(inject_tasks)} async tasks completed')

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
    placement: PlacementMode = PlacementMode.SAME_INFRA,
) -> bool:
    """Set up networking for all jobs in a JobGroup.

    This is the main entry point for JobGroup networking setup.

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples for each job.
        placement: Placement mode (default: SAME_INFRA).

    Returns:
        True if setup succeeded, False otherwise.
    """
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
    return f'SKYPILOT_JOBGROUP_HOST_{safe_name}'


def generate_wait_for_networking_script(job_group_name: str,
                                        other_job_names: List[str]) -> str:
    """Generate a bash script to wait for network setup.

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

    # Wait for hostnames to be resolvable
    hostname_list = ' '.join(hostnames)
    # Sanitize job group name for file paths (same as in updater script)
    safe_job_group_name = job_group_name.replace(' ', '_')
    updater_log = (f'/tmp/skypilot-jobgroup-dns-updater-'
                   f'{safe_job_group_name}.log')
    updater_process = f'skypilot-jobgroup-dns-updater-{safe_job_group_name}'

    wait_script = textwrap.dedent(f"""
        # Wait for JobGroup networking to be ready
        echo "[SkyPilot] Waiting for network setup..."
        echo "[SkyPilot] Waiting for hostnames: {hostname_list}"
        HOSTNAMES="{hostname_list}"
        MAX_WAIT=300  # 5 minutes
        ELAPSED=0
        UPDATER_LOG="{updater_log}"
        UPDATER_PROCESS="{updater_process}"
        for hostname in $HOSTNAMES; do
          while ! getent hosts "$hostname" >/dev/null 2>&1; do
            if [ $ELAPSED -ge $MAX_WAIT ]; then
              echo "[SkyPilot] Error: Network setup timed out for \\"$hostname\\" after ${{ELAPSED}}s"
              echo "[SkyPilot] DNS updater running: $(pgrep -f "$UPDATER_PROCESS" > /dev/null && echo 'yes' || echo 'no')"
              echo "[SkyPilot] DNS updater log exists: $([ -f "$UPDATER_LOG" ] && echo 'yes' || echo 'no')"
              echo "[SkyPilot] Hosts file entries:"
              cat /etc/hosts | grep -i jobgroup || echo "(none)"
              if [ -f "$UPDATER_LOG" ]; then
                echo "[SkyPilot] DNS updater log (last 20 lines):"
                tail -20 "$UPDATER_LOG"
              fi
              exit 1
            fi
            if [ $(($ELAPSED % 30)) -eq 0 ]; then
              echo "[SkyPilot] Still waiting for $hostname (${{ELAPSED}}s elapsed)..."
              echo "[SkyPilot] DNS updater running: $(pgrep -f "$UPDATER_PROCESS" > /dev/null && echo 'yes' || echo 'no')"
            fi
            sleep 2
            ELAPSED=$((ELAPSED + 2))
          done
          echo "[SkyPilot] Hostname $hostname is now resolvable"
        done
        echo "[SkyPilot] Network is ready!"
    """)

    return wait_script.strip()
