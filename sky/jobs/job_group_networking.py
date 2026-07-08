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
import base64
import dataclasses
import os
import re
import tempfile
import textwrap
import traceback
import typing
from typing import Any, Dict, List, Optional, Tuple

from sky import clouds as sky_clouds
from sky import sky_logging
from sky.adaptors import kubernetes
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
    """Resolve the Kubernetes namespace the handle's pods live in.

    Reads ``provider.namespace`` from the cluster YAML (set at launch
    time and workspace-invariant). Resolving via ``get_namespace`` here
    would depend on the active workspace at query time, which is not
    guaranteed to match the workspace the cluster was launched under.

    Falls back to the kubeconfig context default for legacy clusters
    whose YAML pre-dates ``provider.namespace``; returns ``'default'``
    if both lookups fail.
    """
    if handle is None:
        return 'default'

    if handle.cluster_yaml:
        try:
            # pylint: disable=import-outside-toplevel
            from sky import global_user_state
            cluster_yaml_dict = global_user_state.get_cluster_yaml_dict(
                handle.cluster_yaml)
            namespace = cluster_yaml_dict.get('provider', {}).get('namespace')
            if namespace:
                return namespace
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to read namespace from cluster YAML, '
                         f'falling back: {e}')

    if handle.launched_resources and handle.launched_resources.region:
        try:
            # pylint: disable=import-outside-toplevel
            from sky.provision.kubernetes import utils as k8s_utils
            return k8s_utils.get_kube_config_context_namespace(
                handle.launched_resources.region)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to get K8s namespace from handle, '
                         f'falling back to default: {e}')

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


# Labels stamped on mirror Services/Endpoints created for cross-context
# JobGroups (see the "Cross-context DNS mirrors" section below). The job-id
# label is the cleanup key; the group label is used to garbage-collect stale
# mirrors from a previous run of a group with the same name (a relaunch gets
# a new job id, so names never collide).
_MIRROR_LABEL_GROUP = 'skypilot-jobgroup-mirror'
_MIRROR_LABEL_JOB_ID = 'skypilot-managed-job-id'
# Exported (no leading underscore) so the jobs controller can reference it
# without triggering a protected-access lint across modules.
MIRROR_RECONCILE_INTERVAL_SECONDS = 15


def _get_context_from_handle(
        handle: 'cloud_vm_ray_backend.CloudVmRayResourceHandle'
) -> Optional[str]:
    """Return the kubeconfig context name for a Kubernetes handle.

    For Kubernetes, ``Resources.region`` holds the kubeconfig context name.
    """
    if not _is_kubernetes(handle):
        return None
    return handle.launched_resources.region


def _label_value(value: str) -> str:
    """Truncate to the Kubernetes label value length limit (63 chars)."""
    return value[:63]


# ============================================================================
# Layer 3: NetworkConfigurator - Platform-specific network configuration
# ============================================================================


def _generate_k8s_dns_mappings(
    job_group_name: str,
    tasks_handles: List[Tuple['task_lib.Task',
                              'cloud_vm_ray_backend.CloudVmRayResourceHandle']],
    consumer_context: Optional[str] = None,
    consumer_namespace: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """Generate K8s DNS to hostname mappings for background updater.

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples.
        consumer_context: Kubeconfig context of the consumer these mappings
            are being generated for (i.e. the cluster whose /etc/hosts will
            be updated). When set and a peer's own context differs from
            this one, the peer's constructed-name entry is pointed at the
            consumer's own namespace instead of the peer's — that is where
            the cross-context DNS mirror Service for this peer lives.
            Same-context callers (or callers that don't care, e.g.
            single-context groups) should leave this unset, which preserves
            today's behavior exactly.
        consumer_namespace: Namespace to use for cross-context peers when
            ``consumer_context`` is set. Required (and only meaningful) in
            that case.

    Returns:
        List of (k8s_dns, simple_hostname) tuples.
    """
    # pylint: disable-next=import-outside-toplevel
    from sky.jobs import runtime as managed_job_runtime

    mappings: List[Tuple[str, str]] = []
    for task, handle in tasks_handles:
        if handle is None or not _is_kubernetes(handle):
            continue
        addresses = None
        if managed_job_runtime.is_registered():
            addresses = managed_job_runtime.k8s_dns_addresses_for_handle(handle)
        if addresses is None:
            cluster_name_on_cloud = handle.cluster_name_on_cloud
            peer_context = _get_context_from_handle(handle)
            namespace = _get_k8s_namespace_from_handle(handle)
            if (consumer_context is not None and
                    peer_context != consumer_context):
                assert consumer_namespace is not None, (
                    'consumer_namespace is required when consumer_context '
                    'is set')
                namespace = consumer_namespace
            num_nodes = (len(handle.stable_internal_external_ips)
                         if handle.stable_internal_external_ips else 1)
            addresses = [
                _construct_k8s_internal_svc(cluster_name_on_cloud, namespace,
                                            node_idx)
                for node_idx in range(num_nodes)
            ]
        job_name = task.name
        for node_idx, dns_name in enumerate(addresses):
            hostname = f'{job_name}-{node_idx}.{job_group_name}'
            mappings.append((dns_name, hostname))
            logger.debug(f'K8s DNS mapping (node {node_idx}): '
                         f'{dns_name} -> {hostname}')
    return mappings


def dns_addresses_for_task(
    task: 'task_lib.Task',
    job_id: int,
) -> Optional[List[str]]:
    """K8s DNS addresses for this task, or ``None``."""
    # pylint: disable-next=import-outside-toplevel
    from sky.jobs import runtime as managed_job_runtime

    if not managed_job_runtime.is_registered():
        return None
    return managed_job_runtime.k8s_dns_addresses_for_task(task, job_id)


def _generate_k8s_dns_mappings_from_runtime(
    job_group_name: str,
    tasks: List['task_lib.Task'],
    job_id: int,
) -> List[Tuple[str, str]]:
    """Build K8s DNS mappings from runtime-supplied addresses."""
    mappings: List[Tuple[str, str]] = []
    for task in tasks:
        addresses = dns_addresses_for_task(task, job_id)
        if addresses is None:
            continue
        for node_idx, dns_name in enumerate(addresses):
            hostname = f'{task.name}-{node_idx}.{job_group_name}'
            mappings.append((dns_name, hostname))
            logger.debug(f'K8s DNS mapping from runtime (node {node_idx}): '
                         f'{dns_name} -> {hostname}')
    return mappings


def generate_inline_networking_setup_script(
    job_group_name: str,
    tasks: List['task_lib.Task'],
    job_id: int,
) -> str:
    """Bash to prepend to task.run that starts the JobGroup DNS updater
    from there, or empty if the task does not inline the DNS mapping."""
    dns_mappings = _generate_k8s_dns_mappings_from_runtime(
        job_group_name, tasks, job_id)
    if not dns_mappings:
        return ''

    updater_script = generate_k8s_dns_updater_script(dns_mappings,
                                                     job_group_name)
    encoded_script = base64.b64encode(updater_script.encode()).decode()
    process_id = f'skypilot-jobgroup-dns-updater-{job_group_name}'
    script_path = f'/tmp/{process_id}.sh'
    log_path = f'/tmp/{process_id}.log'
    marker_file = get_network_ready_marker_path(job_group_name)
    return textwrap.dedent(f"""\
        # Start JobGroup DNS updater inside the task runtime.
        echo '{encoded_script}' | base64 -d > {script_path}
        chmod +x {script_path}
        (nohup {script_path} < /dev/null > {log_path} 2>&1 &) || true
        touch {marker_file}
        """).strip()


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
            # Resolve K8s DNS to IP. Query the absolute name (trailing dot)
            # so resolv.conf search-path expansion (ndots:5) can't append a
            # search domain and let a wildcard DNS record silently answer
            # this lookup instead of NXDOMAIN. $simple_name below must NOT
            # get a trailing dot: it needs to keep resolving via the
            # 'files' backend against /etc/hosts itself.
            ip=$(getent hosts "$k8s_dns." 2>/dev/null | awk '{{print $1}}')
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

        # Cross-context classic mappings must embed the CONSUMING cluster's
        # namespace (where the controller creates that peer's DNS mirror
        # Service), not the peer's own namespace — see
        # `_generate_k8s_dns_mappings`. Only pay for per-consumer mapping
        # generation when the group actually spans multiple K8s contexts;
        # single-context groups keep today's single global mapping list
        # (byte-identical behavior, no per-task recomputation).
        k8s_contexts = {
            _get_context_from_handle(handle)
            for _, handle in tasks_handles
            if handle is not None and _is_kubernetes(handle)
        }
        spans_multiple_contexts = len(k8s_contexts) > 1
        global_k8s_dns_mappings: List[Tuple[str, str]] = []
        if not spans_multiple_contexts:
            global_k8s_dns_mappings = _generate_k8s_dns_mappings(
                job_group_name, tasks_handles)

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

            if is_k8s:
                # Computed once per task, not per node.
                if spans_multiple_contexts:
                    consumer_context = _get_context_from_handle(handle)
                    consumer_namespace = _get_k8s_namespace_from_handle(handle)
                    k8s_dns_mappings = _generate_k8s_dns_mappings(
                        job_group_name,
                        tasks_handles,
                        consumer_context=consumer_context,
                        consumer_namespace=consumer_namespace)
                else:
                    k8s_dns_mappings = global_k8s_dns_mappings

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
# Cross-context DNS mirrors
#
# When a JobGroup's Kubernetes tasks span more than one kubeconfig context,
# each task's real headless Service only resolves inside its own cluster.
# The functions below let the controller create selectorless "mirror"
# Services + manually-managed core/v1 Endpoints for each peer task in every
# OTHER member (context, namespace), so the on-pod DNS updater's mapping
# strings resolve verbatim regardless of which cluster the consumer is in.
#
# Single-context groups never call any Kubernetes API here: everything below
# gates on there being more than one distinct context among the group's
# tasks.
# ============================================================================

# (task, handle) pairs as the controller tracks them: a handle may be None
# for tasks that are terminal, never launched, or already torn down.
_TasksHandles = List[Tuple[
    'task_lib.Task', Optional['cloud_vm_ray_backend.CloudVmRayResourceHandle']]]


@dataclasses.dataclass
class _TaskNetworkInfo:
    """Per-task Kubernetes network info used for cross-context mirroring."""
    task_name: str
    cluster_name_on_cloud: str
    context: Optional[str]
    namespace: str
    num_nodes: int
    # Whether the task's DNS addresses are runtime-supplied (single headless
    # Service, per-pod hostname records) rather than the classic per-node
    # Services SkyPilot creates itself.
    is_v1: bool


def _fallback_task_network_info(
        task: 'task_lib.Task') -> Optional[_TaskNetworkInfo]:
    """Best-effort network info for a task whose handle is already gone.

    Used only by `cleanup_cross_context_mirrors`: derives (context,
    namespace) from the task's pinned resources so stale mirrors can still
    be found and deleted after the cluster itself has been torn down (and
    thus has no handle in `global_user_state` any more). `cluster_name_on_
    cloud`/`num_nodes`/`is_v1` are irrelevant for cleanup (which only keys
    off (context, namespace) and the job-id label) and are filled in
    best-effort.
    """
    if task.name is None:
        return None
    context = None
    for resource in task.resources:
        if (isinstance(resource.cloud, sky_clouds.Kubernetes) and
                resource.region is not None):
            context = resource.region
            break
    if context is None:
        return None
    # pylint: disable-next=import-outside-toplevel
    from sky.provision.kubernetes import utils as k8s_utils
    try:
        namespace = k8s_utils.get_kube_config_context_namespace(context)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to resolve namespace for context {context} '
                     f'during JobGroup mirror cleanup, falling back to '
                     f'default: {e}')
        namespace = 'default'
    return _TaskNetworkInfo(task_name=task.name,
                            cluster_name_on_cloud=task.name,
                            context=context,
                            namespace=namespace,
                            num_nodes=1,
                            is_v1=False)


def _task_network_infos(
    tasks_handles: _TasksHandles,
    job_id: int,
    include_fallback: bool = False,
) -> List[_TaskNetworkInfo]:
    """Collect per-task Kubernetes network info for cross-context mirroring.

    Args:
        tasks_handles: (Task, ResourceHandle) tuples for each task.
        job_id: Managed job ID (used to detect runtime-supplied addresses).
        include_fallback: If True, also emit a best-effort info entry (see
            `_fallback_task_network_info`) for tasks whose handle is None.
            Only meaningful for cleanup — `setup_cross_context_mirrors`
            needs live handles and leaves this False.

    Returns:
        List of `_TaskNetworkInfo`, one per Kubernetes task with usable
        info. Non-Kubernetes tasks and (unless `include_fallback`) tasks
        with no handle are omitted.
    """
    infos: List[_TaskNetworkInfo] = []
    for task, handle in tasks_handles:
        if handle is not None and _is_kubernetes(handle):
            assert task.name is not None, task
            infos.append(
                _TaskNetworkInfo(
                    task_name=task.name,
                    cluster_name_on_cloud=handle.cluster_name_on_cloud,
                    context=_get_context_from_handle(handle),
                    namespace=_get_k8s_namespace_from_handle(handle),
                    num_nodes=(len(handle.stable_internal_external_ips)
                               if handle.stable_internal_external_ips else 1),
                    is_v1=dns_addresses_for_task(task, job_id) is not None))
        elif include_fallback and handle is None:
            fallback = _fallback_task_network_info(task)
            if fallback is not None:
                infos.append(fallback)
    return infos


def group_spans_multiple_contexts(
    tasks_handles: _TasksHandles,
    job_id: int,
) -> bool:
    """True iff the group's Kubernetes tasks span more than one context."""
    infos = _task_network_infos(tasks_handles, job_id)
    return len({info.context for info in infos}) > 1


def _list_peer_pod_ips(context: Optional[str], namespace: str,
                       cluster_name_on_cloud: str) -> Dict[int, str]:
    """List a peer task's pod IPs, keyed by node index.

    Synchronous (intended to be run via `asyncio.to_thread`). Exceptions
    propagate to the caller.
    """
    core_api = kubernetes.core_api(context)
    # Classic clusters label pods with the SkyPilot cluster name; fall back
    # to the (runtime-managed) job name label if that selector comes up
    # empty.
    selectors = [
        f'skypilot-cluster-name={cluster_name_on_cloud}',
        f'skypilot-managed-job-name={cluster_name_on_cloud}',
    ]
    pods: List[Any] = []
    for selector in selectors:
        pods = core_api.list_namespaced_pod(namespace,
                                            label_selector=selector,
                                            _request_timeout=10).items
        if pods:
            break

    worker_prefix = f'{cluster_name_on_cloud}-worker'
    pod_ips: Dict[int, str] = {}
    for pod in pods:
        if pod.metadata.deletion_timestamp is not None:
            continue
        if not pod.status.pod_ip:
            continue
        name = pod.metadata.name
        node_idx: Optional[int] = None
        if name == f'{cluster_name_on_cloud}-head':
            node_idx = 0
        elif name.startswith(worker_prefix):
            suffix = name[len(worker_prefix):]
            if suffix.isdigit():
                node_idx = int(suffix)
        elif pod.spec.hostname is not None:
            match = re.match(r'node-(\d+)', pod.spec.hostname)
            if match is not None:
                node_idx = int(match.group(1))
        if node_idx is None:
            logger.debug(f'Could not determine node index for pod {name} '
                         f'(peer cluster {cluster_name_on_cloud}); '
                         'skipping.')
            continue
        pod_ips[node_idx] = pod.status.pod_ip
    return pod_ips


def _mirror_specs(
        peer: _TaskNetworkInfo,
        pod_ips: Dict[int, str]) -> List[Tuple[str, List[Dict[str, str]]]]:
    """Build (service_name, endpoint_addresses) pairs to mirror a peer task.

    `endpoint_addresses` is the `addresses` list for a single Endpoints
    subset (empty when the corresponding pod IP is not yet known).
    """
    if peer.is_v1:
        # V1-runtime mirrors expose node 0 only — this matches the
        # in-cluster contract, where the runtime's own headless Service +
        # pod hostname/subdomain only ever resolves node 0 for inter-task
        # discovery; intra-task workers use their own discovery mechanism.
        addresses = []
        if 0 in pod_ips:
            addresses = [{'ip': pod_ips[0], 'hostname': 'node-0'}]
        return [(peer.cluster_name_on_cloud, addresses)]

    # Classic peer: one headless Service per node, name-identical to the
    # real per-node Services the peer's own cluster creates. No 'hostname'
    # field needed — the lookup name is the mirror Service's own A record.
    specs: List[Tuple[str, List[Dict[str, str]]]] = []
    for node_idx in range(peer.num_nodes):
        name = (f'{peer.cluster_name_on_cloud}-head' if node_idx == 0 else
                f'{peer.cluster_name_on_cloud}-worker{node_idx}')
        addresses = [{'ip': pod_ips[node_idx]}] if node_idx in pod_ips else []
        specs.append((name, addresses))
    return specs


def _mirror_service_manifest(name: str, job_group_name: str,
                             job_id: int) -> Dict[str, Any]:
    """Selectorless headless Service manifest for a JobGroup DNS mirror.

    Deliberately no selector and no ports: DNS A records come from the
    manually-managed Endpoints (`_mirror_endpoints_manifest`) below, and
    ports are irrelevant for A-record resolution (the real Services being
    mirrored are portless too).
    """
    return {
        'apiVersion': 'v1',
        'kind': 'Service',
        'metadata': {
            'name': name,
            'labels': {
                'parent': 'skypilot',
                _MIRROR_LABEL_GROUP: _label_value(job_group_name),
                _MIRROR_LABEL_JOB_ID: str(job_id),
            },
        },
        'spec': {
            'clusterIP': 'None',
        },
    }


def _mirror_endpoints_manifest(name: str, addresses: List[Dict[str, str]],
                               job_group_name: str,
                               job_id: int) -> Dict[str, Any]:
    """Endpoints manifest for a JobGroup DNS mirror Service.

    Uses legacy core/v1 Endpoints rather than discovery.k8s.io EndpointSlice
    deliberately: kube-dns (GKE Standard's default) only reads Endpoints,
    while the endpointslice-mirroring controller (GA, default-on since k8s
    1.19) mirrors selectorless-Service Endpoints into EndpointSlices for
    clusters running CoreDNS / Cloud DNS. Writing legacy Endpoints therefore
    serves both without needing to know which the member cluster runs.
    """
    return {
        'apiVersion': 'v1',
        'kind': 'Endpoints',
        'metadata': {
            'name': name,
            'labels': {
                'parent': 'skypilot',
                _MIRROR_LABEL_GROUP: _label_value(job_group_name),
                _MIRROR_LABEL_JOB_ID: str(job_id),
            },
        },
        'subsets': [{
            'addresses': addresses
        }] if addresses else [],
    }


def _apply_mirrors_in_cluster(context: Optional[str], namespace: str,
                              job_group_name: str, job_id: int,
                              desired: Dict[str, List[Dict[str, str]]],
                              quiet: bool) -> None:
    """Create/update JobGroup DNS mirrors in one member cluster.

    Synchronous (intended to be run via `asyncio.to_thread`). Raises on
    hard API errors so the caller can aggregate per-member failures; does
    not catch anything itself.
    """
    core_api = kubernetes.core_api(context)

    # GC mirrors left behind by a previous run of a group with the same
    # name. A relaunch gets a new job id, so the current run's mirror names
    # never collide with stale ones — safe to delete unconditionally.
    group_label = _label_value(job_group_name)
    stale = core_api.list_namespaced_service(
        namespace,
        label_selector=f'{_MIRROR_LABEL_GROUP}={group_label}',
        _request_timeout=10)
    for svc in stale.items:
        if svc.metadata.labels.get(_MIRROR_LABEL_JOB_ID) == str(job_id):
            continue
        name = svc.metadata.name
        for delete_fn in (core_api.delete_namespaced_service,
                          core_api.delete_namespaced_endpoints):
            try:
                delete_fn(name, namespace, _request_timeout=10)
            except kubernetes.api_exception() as e:
                if e.status != 404:
                    raise

    for name, addresses in desired.items():
        service_manifest = _mirror_service_manifest(name, job_group_name,
                                                    job_id)
        try:
            core_api.create_namespaced_service(namespace,
                                               service_manifest,
                                               _request_timeout=10)
        except kubernetes.api_exception() as e:
            # The mirror Service spec is static (no selector/ports), so an
            # already-existing Service needs no update.
            if e.status != 409:
                raise

        try:
            existing = core_api.read_namespaced_endpoints(name,
                                                          namespace,
                                                          _request_timeout=10)
        except kubernetes.api_exception() as e:
            if e.status != 404:
                raise
            existing = None

        desired_key = sorted((address.get('ip'), address.get('hostname'))
                             for address in addresses)
        if existing is None:
            endpoints_manifest = _mirror_endpoints_manifest(
                name, addresses, job_group_name, job_id)
            core_api.create_namespaced_endpoints(namespace,
                                                 endpoints_manifest,
                                                 _request_timeout=10)
            continue

        existing_key = sorted((address.ip, address.hostname)
                              for subset in (existing.subsets or [])
                              for address in (subset.addresses or []))
        if desired_key != existing_key:
            endpoints_manifest = _mirror_endpoints_manifest(
                name, addresses, job_group_name, job_id)
            core_api.replace_namespaced_endpoints(name,
                                                  namespace,
                                                  endpoints_manifest,
                                                  _request_timeout=10)

    level = logger.debug if quiet else logger.info
    level(f'Mirrored {len(desired)} JobGroup peer service(s) into '
          f'{context}/{namespace}')


def _delete_mirrors_in_cluster(context: Optional[str], namespace: str,
                               job_id: int) -> None:
    """Delete this job's DNS mirrors from one member cluster.

    Synchronous (intended to be run via `asyncio.to_thread`). Best-effort:
    404s are tolerated and other errors are logged, never raised.
    """
    core_api = kubernetes.core_api(context)
    try:
        services = core_api.list_namespaced_service(
            namespace,
            label_selector=f'{_MIRROR_LABEL_JOB_ID}={job_id}',
            _request_timeout=10)
    except kubernetes.api_exception() as e:
        logger.warning(f'Failed to list JobGroup mirrors in '
                       f'{context}/{namespace}: {e}')
        return

    for svc in services.items:
        name = svc.metadata.name
        for delete_fn in (core_api.delete_namespaced_service,
                          core_api.delete_namespaced_endpoints):
            try:
                delete_fn(name, namespace, _request_timeout=10)
            except kubernetes.api_exception() as e:
                if e.status != 404:
                    logger.warning(
                        f'Failed to delete JobGroup mirror {name} in '
                        f'{context}/{namespace}: {e}')


# Per-group lock so Phase 3 setup, on-recovery re-setup, and the periodic
# reconcile loop (see controller.py) never interleave mirror writes for the
# same job. Created lazily; entries are small and outlive their job for the
# life of the controller process, which is acceptable.
_mirror_locks: Dict[int, asyncio.Lock] = {}


def _get_mirror_lock(job_id: int) -> asyncio.Lock:
    return _mirror_locks.setdefault(job_id, asyncio.Lock())


async def setup_cross_context_mirrors(
    job_group_name: str,
    job_id: int,
    tasks_handles: _TasksHandles,
    quiet: bool = False,
) -> bool:
    """Mirror each K8s task's peer DNS into every other member cluster.

    No-op (returns True immediately, no Kubernetes API calls) unless the
    group's tasks span more than one Kubernetes context — single-context
    groups are completely unaffected.

    Never raises: per-member failures are logged and reflected in the
    return value so the caller can proceed (mirrors are best-effort, same
    as the rest of JobGroup networking setup).

    Args:
        job_group_name: Name of the JobGroup.
        job_id: Managed job ID.
        tasks_handles: (Task, ResourceHandle) tuples for tasks with a live
            handle (a None handle is simply skipped by `_task_network_infos`
            since there is nothing to mirror for it yet).
        quiet: If True, log at debug instead of info/warning (used by the
            periodic reconcile loop to avoid spamming the log every
            interval).

    Returns:
        True if every member cluster was reconciled successfully.
    """
    infos = _task_network_infos(tasks_handles, job_id)
    contexts = {info.context for info in infos}
    if len(contexts) <= 1:
        return True

    members = sorted({(info.context, info.namespace) for info in infos})
    if not quiet:
        logger.info(f'Setting up cross-cluster DNS mirrors for JobGroup '
                    f'{job_group_name!r} across contexts: '
                    f'{sorted(str(c) for c in contexts)}')

    async with _get_mirror_lock(job_id):
        ok = True
        for ctx, ns in members:
            desired: Dict[str, List[Dict[str, str]]] = {}
            for peer in infos:
                if peer.context == ctx:
                    continue
                try:
                    pod_ips = await asyncio.to_thread(
                        _list_peer_pod_ips, peer.context, peer.namespace,
                        peer.cluster_name_on_cloud)
                except Exception as e:  # pylint: disable=broad-except
                    if not quiet:
                        logger.warning(f'Failed to list pods for JobGroup peer '
                                       f'{peer.task_name!r} in {peer.context}/'
                                       f'{peer.namespace}: {e}')
                    pod_ips = {}
                desired.update(dict(_mirror_specs(peer, pod_ips)))

            try:
                await asyncio.to_thread(_apply_mirrors_in_cluster, ctx, ns,
                                        job_group_name, job_id, desired, quiet)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(
                    f'Failed to set up JobGroup DNS mirrors in {ctx}/{ns}: '
                    f'{e}. The SkyPilot service account needs get/list/'
                    'create/patch/update/delete on services and endpoints '
                    f'in that namespace.')
                ok = False
        return ok


async def cleanup_cross_context_mirrors(
    job_group_name: str,
    job_id: int,
    tasks_handles: _TasksHandles,
) -> None:
    """Delete this job's DNS mirrors from every member cluster.

    No-op (no Kubernetes API calls) unless the group's tasks span more than
    one Kubernetes context. Best-effort and never raises: handles in
    `tasks_handles` may be None (cluster already gone), in which case a
    fallback (context, namespace) is derived from the task's pinned
    resources so mirrors can still be found and swept.

    Args:
        job_group_name: Name of the JobGroup (used only for logging — the
            job-id label alone identifies this run's mirrors).
        job_id: Managed job ID.
        tasks_handles: (Task, ResourceHandle) tuples for all tasks.
    """
    infos = _task_network_infos(tasks_handles, job_id, include_fallback=True)
    contexts = {info.context for info in infos}
    if len(contexts) <= 1:
        return

    members = sorted({(info.context, info.namespace) for info in infos})
    for ctx, ns in members:
        try:
            await asyncio.to_thread(_delete_mirrors_in_cluster, ctx, ns, job_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'Failed to clean up JobGroup {job_group_name!r} DNS '
                f'mirrors in {ctx}/{ns}: {e}')


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
