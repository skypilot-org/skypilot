"""Networking utilities for JobGroups.

This module provides functions to set up networking between tasks in a JobGroup.

Architecture:
    Layer 1: User Interface (environment variables)
        - SKYPILOT_JOBGROUP_NAME = <job_group_name>

    Layer 2: JobAddressResolver
        - Resolves task addresses for internal networking
        - All tasks run on same infrastructure (cloud + region or K8s cluster)

    Layer 3: NetworkConfigurator
        - Configures network infrastructure (e.g., /etc/hosts injection)
        - Handles platform-specific differences (K8s vs SSH clouds)

Design Goals:
    - Unified interface: All tasks access addresses via environment variables
    - Platform abstraction: K8s uses native DNS, SSH clouds use /etc/hosts
"""
import asyncio
import os
import tempfile
import textwrap
import traceback
import typing
from typing import List, Tuple

from sky import clouds as sky_clouds
from sky import sky_logging
from sky.utils import command_runner

if typing.TYPE_CHECKING:
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)

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
        return f'{cluster_name_on_cloud}-head.{namespace}.svc.cluster.local'
    return (f'{cluster_name_on_cloud}-worker{node_idx}.'
            f'{namespace}.svc.cluster.local')


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
        tasks_handles: List of (Task, ResourceHandle) tuples for each task.

    Returns:
        String containing /etc/hosts entries, one per line.
    """
    entries = [f'# JobGroup: {job_group_name}']

    for task, handle in tasks_handles:
        if handle is None:
            logger.warning(f'Skipping task {task.name}: no handle')
            continue

        if _is_kubernetes(handle):
            continue

        if handle.stable_internal_external_ips is None:
            logger.warning(f'Skipping task {task.name}: no IP information')
            continue

        task_name = task.name
        for node_idx, (internal_ip,
                       _) in enumerate(handle.stable_internal_external_ips):
            hostname = f'{task_name}-{node_idx}.{job_group_name}'
            entries.append(f'{internal_ip} {hostname}')
            logger.debug(f'Host entry (SSH): {internal_ip} -> {hostname}')

    return '\n'.join(entries)


async def _inject_hosts_on_node(
    runner: 'command_runner.CommandRunner',
    hosts_content: str,
    job_group_name: str,
) -> bool:
    """Inject /etc/hosts entries on a single node.

    Also creates a marker file to signal that networking setup is complete.

    Args:
        runner: CommandRunner for the target node.
        hosts_content: Content to append to /etc/hosts.
        job_group_name: Name of the JobGroup (for marker file).

    Returns:
        True if successful, False otherwise.
    """
    # pylint: disable=invalid-string-quote
    escaped_content = hosts_content.replace("'", "'\\''")  # noqa: Q000
    marker_file = get_network_ready_marker_path(job_group_name)
    # Use ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD to handle containers without sudo
    # but running as root (e.g., pytorch/pytorch images)
    cmd = (
        f'{command_runner.ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD} && '
        f"echo '{escaped_content}' | "  # noqa: Q000
        f'sudo tee -a /etc/hosts > /dev/null && touch {marker_file}')
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
        logger.error(traceback.format_exc())
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

    mapping_pairs = ' '.join(
        f'{dns}:{hostname}' for dns, hostname in dns_mappings)

    # Note: job_group_name is validated at YAML load time to be shell-safe
    # Use ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD to handle containers without sudo
    # but running as root (e.g., pytorch/pytorch images)
    script = textwrap.dedent(f"""\
        #!/bin/bash
        # Background K8s DNS to IP updater for /etc/hosts

        # Disable sudo for root user - handles containers without sudo installed
        {command_runner.ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD}

        MAPPINGS="{mapping_pairs}"
        MARKER="# SkyPilot JobGroup K8s entries"

        echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Starting DNS updater for {job_group_name}"
        echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Monitoring mappings: $MAPPINGS"

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
              # Note: On first run, current_ip will be empty, triggering update
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

    # Note: job_group_name is validated at YAML load time to be shell-safe
    # (alphanumeric, hyphens, underscores only - see dag_utils.py:477-485)
    # This ensures the process_id is safe for use in pgrep patterns and paths
    process_id = f'skypilot-jobgroup-dns-updater-{job_group_name}'
    script_path = f'/tmp/{process_id}.sh'
    log_path = f'/tmp/{process_id}.log'

    loop = asyncio.get_running_loop()

    try:
        # Upload script via rsync
        with tempfile.NamedTemporaryFile('w',
                                         prefix='sky_dns_updater_',
                                         suffix='.sh',
                                         delete=False) as f:
            f.write(updater_script)
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

        # Make executable and run in background, then verify it started.
        # Uses nohup with a subshell to fully detach from kubectl exec.
        # After a brief sleep, pgrep confirms the process is running.
        # Use 0.5s sleep to ensure process is visible on loaded systems.
        # Also create the marker file to signal networking setup is initiated.
        marker_file = get_network_ready_marker_path(job_group_name)
        run_cmd = (f'chmod +x {script_path} && '
                   f'(nohup {script_path} < /dev/null > {log_path} 2>&1 &) && '
                   f'sleep 0.5 && '
                   f'pgrep -f "{process_id}" > /dev/null && '
                   f'touch {marker_file}')
        logger.info(f'Starting DNS updater in background (log: {log_path})...')
        returncode, _, stderr = await loop.run_in_executor(
            None,
            lambda: runner.run(run_cmd, stream_logs=False, require_outputs=True)
        )

        # Exit code 143 (SIGTERM) is expected when kubectl exec closes the
        # connection. The background process continues running despite this.
        if returncode not in (0, 143):
            logger.error(f'Failed to start DNS updater: '
                         f'returncode={returncode}, stderr={stderr}')
            return False

        logger.info('DNS updater started successfully')
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
    ) -> bool:
        """Set up network configuration for JobGroup.

        Args:
            job_group_name: Name of the JobGroup.
            tasks_handles: List of (Task, ResourceHandle) tuples.

        Returns:
            True if all configuration succeeded, False otherwise.
        """
        return await NetworkConfigurator._inject_etc_hosts(
            job_group_name, tasks_handles)

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

        ssh_hosts_content = _generate_hosts_entries(job_group_name,
                                                    tasks_handles)
        k8s_dns_mappings = _generate_k8s_dns_mappings(job_group_name,
                                                      tasks_handles)

        # Each entry: (coroutine, task_name, node_idx, is_k8s)
        setup_tasks: List[Tuple] = []
        for task, handle in tasks_handles:
            if handle is None:
                continue

            is_k8s = _is_kubernetes(handle)
            try:
                runners = handle.get_command_runners()
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Failed to get command runners for {task.name}: {e}')
                continue

            for node_idx, runner in enumerate(runners):
                if is_k8s:
                    coro = _start_k8s_dns_updater_on_node(
                        runner, k8s_dns_mappings, job_group_name)
                    setup_tasks.append((coro, task.name, node_idx, True))
                else:
                    # ssh_hosts_content is always truthy (has header comment)
                    assert ssh_hosts_content, 'unreachable'
                    coro = _inject_hosts_on_node(runner, ssh_hosts_content,
                                                 job_group_name)
                    setup_tasks.append((coro, task.name, node_idx, False))
                logger.debug(
                    f'Queued networking setup for {task.name}-{node_idx}')

        if not setup_tasks:
            logger.warning('No nodes to set up networking')
            return True

        coroutines = [entry[0] for entry in setup_tasks]
        logger.info(f'Setting up networking on {len(coroutines)} nodes...')
        try:
            results = await asyncio.wait_for(asyncio.gather(
                *coroutines, return_exceptions=True),
                                             timeout=60.0)
        except asyncio.TimeoutError:
            logger.error('Networking setup timed out after 60 seconds')
            return False

        success_count = 0
        for i, result in enumerate(results):
            if result is True:
                success_count += 1
                continue

            # Log error details for failed tasks
            _, task_name, node_idx, is_k8s = setup_tasks[i]
            setup_type = 'K8s DNS updater' if is_k8s else '/etc/hosts'
            node_label = f'{task_name}-{node_idx}'

            if isinstance(result, Exception):
                tb_str = ''.join(
                    traceback.format_exception(type(result), result,
                                               result.__traceback__))
                logger.error(
                    f'{setup_type} failed on {node_label}: {result}\n{tb_str}')
            else:
                logger.error(f'{setup_type} failed on {node_label}')

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
) -> bool:
    """Set up networking for all tasks in a JobGroup.

    This is the main entry point for JobGroup networking setup.

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples for each task.

    Returns:
        True if setup succeeded, False otherwise.
    """
    logger.info(f'Setting up networking for JobGroup: {job_group_name}')
    return await NetworkConfigurator.setup(job_group_name, tasks_handles)


def get_network_ready_marker_path(job_group_name: str) -> str:
    """Get the path to the networking ready marker file.

    This marker file is created by Phase 3 (setup_job_group_networking)
    after /etc/hosts entries are set up. The wait script checks for this
    file before starting the hostname resolution timeout.

    Args:
        job_group_name: Name of the JobGroup.

    Returns:
        Path to the marker file.
    """
    return f'/tmp/skypilot-jobgroup-network-ready-{job_group_name}'


def generate_wait_for_networking_script(job_group_name: str,
                                        other_job_names: List[str]) -> str:
    """Generate a bash script to wait for network setup.

    This script should be prepended to task.setup to ensure networking
    is ready before the task starts.

    The script has two phases:
    1. Wait for the networking ready marker file (created by Phase 3)
    2. Wait for all hostnames to be resolvable

    Args:
        job_group_name: Name of the JobGroup.
        other_job_names: List of other task names in the group to wait for.

    Returns:
        Bash script as a string.
    """
    # Generate hostnames to wait for
    hostnames = [
        f'{task_name}-0.{job_group_name}' for task_name in other_job_names
    ]

    if not hostnames:
        return ''

    hostname_list = ' '.join(hostnames)
    # Note: job_group_name is validated at YAML load time to be shell-safe
    marker_file = get_network_ready_marker_path(job_group_name)
    updater_log = (f'/tmp/skypilot-jobgroup-dns-updater-'
                   f'{job_group_name}.log')
    updater_process = f'skypilot-jobgroup-dns-updater-{job_group_name}'

    # TODO(zhwu): The current handling is not robust against the case where
    # network setup fails. The job will continue but may get stuck if it
    # depends on networking. We should make the job group automatically
    # recover (e.g., re-trigger network setup or restart the job) if the
    # network fails to initialize properly.
    wait_script = textwrap.dedent(f"""
        # Wait for JobGroup networking to be ready (best-effort, non-blocking)
        # If networking fails, we continue anyway to allow job group recovery
        echo "[SkyPilot] Waiting for network setup..."
        NETWORK_READY=true

        # Phase 1: Wait for networking setup to be initiated by controller
        # This marker file is created after Phase 3 sets up /etc/hosts
        MARKER_FILE="{marker_file}"
        MARKER_WAIT=600  # 10 minutes to wait for Phase 3 to start
        MARKER_ELAPSED=0
        echo "[SkyPilot] Waiting for networking initialization marker..."
        while [ ! -f "$MARKER_FILE" ]; do
          if [ $MARKER_ELAPSED -ge $MARKER_WAIT ]; then
            echo "[SkyPilot] Warning: Networking setup not initiated after ${{MARKER_ELAPSED}}s"
            echo "[SkyPilot] Continuing without full network setup (job group may recover later)"
            NETWORK_READY=false
            break
          fi
          if [ $(($MARKER_ELAPSED % 60)) -eq 0 ] && [ $MARKER_ELAPSED -gt 0 ]; then
            echo "[SkyPilot] Still waiting for networking initialization (${{MARKER_ELAPSED}}s elapsed)..."
          fi
          sleep 5
          MARKER_ELAPSED=$((MARKER_ELAPSED + 5))
        done

        if [ "$NETWORK_READY" = "true" ]; then
          echo "[SkyPilot] Networking setup initiated, waiting for hostnames..."

          # Phase 2: Wait for all hostnames to be resolvable
          echo "[SkyPilot] Waiting for hostnames: {hostname_list}"
          HOSTNAMES="{hostname_list}"
          MAX_WAIT=300  # 5 minutes
          ELAPSED=0
          UPDATER_LOG="{updater_log}"
          UPDATER_PROCESS="{updater_process}"
          for hostname in $HOSTNAMES; do
            while ! getent hosts "$hostname" >/dev/null 2>&1; do
              if [ $ELAPSED -ge $MAX_WAIT ]; then
                echo "[SkyPilot] Warning: Network setup timed out for \\"$hostname\\" after ${{ELAPSED}}s"
                echo "[SkyPilot] DNS updater running: $(pgrep -f "$UPDATER_PROCESS" > /dev/null && echo 'yes' || echo 'no')"
                echo "[SkyPilot] Continuing without full network setup (job group may recover later)"
                NETWORK_READY=false
                break 2  # Break out of both loops
              fi
              if [ $(($ELAPSED % 30)) -eq 0 ]; then
                echo "[SkyPilot] Still waiting for $hostname (${{ELAPSED}}s elapsed)..."
              fi
              sleep 2
              ELAPSED=$((ELAPSED + 2))
            done
            if [ "$NETWORK_READY" = "true" ]; then
              echo "[SkyPilot] Hostname $hostname is now resolvable"
            fi
          done
        fi

        if [ "$NETWORK_READY" = "true" ]; then
          echo "[SkyPilot] Network is ready!"
        fi
    """)

    return wait_script.strip()
