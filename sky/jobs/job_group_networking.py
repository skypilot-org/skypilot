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
import functools
import os
import tempfile
import textwrap
import traceback
import typing
from typing import Awaitable, Callable, List, Optional, Tuple

from sky import clouds as sky_clouds
from sky import sky_logging
from sky.utils import command_runner
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    from sky import task as task_lib
    from sky.backends import cloud_vm_ray_backend

logger = sky_logging.init_logger(__name__)

# Per-node networking setup runs with its own retry budget: each node
# retries independently (no barrier between nodes), so one flaky SSH
# connection or slow node never blocks -- or fails -- the whole group.
_SETUP_MAX_ATTEMPTS = 3
_SETUP_ATTEMPT_TIMEOUT_SECONDS = 60.0
_SETUP_RETRY_INITIAL_BACKOFF_SECONDS = 5.0


@dataclasses.dataclass(frozen=True)
class SetupFailure:
    """A node whose networking setup failed after its retry budget.

    task_name is carried separately from node_label so callers can tell
    failures on a specific task's own nodes from failures on its peers.
    """
    task_name: str
    node_label: str
    reason: str


@dataclasses.dataclass(frozen=True)
class _NodeSetupSpec:
    """One node's networking setup work.

    make_attempt is a factory rather than a coroutine, so each retry can
    build a fresh attempt (a coroutine cannot be awaited twice).
    """
    make_attempt: Callable[[], Awaitable[bool]]
    task_name: str
    node_label: str
    setup_type: str


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
            namespace = _get_k8s_namespace_from_handle(handle)
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
    updater_process_name = f'skypilot-jobgroup-dns-updater-{job_group_name}'
    script_path = f'/tmp/{updater_process_name}.sh'
    log_path = f'/tmp/{updater_process_name}.log'
    # Must match the PID file path written by the updater script in
    # generate_k8s_dns_updater_script.
    pid_file = f'/tmp/{updater_process_name}.pid'
    marker_file = get_network_ready_marker_path(job_group_name)
    # The start is guarded by the updater's PID file: task restarts
    # (max_restarts_on_errors) re-run task.run on the same cluster, and an
    # unconditional start would stack a duplicate updater per restart.
    return textwrap.dedent(f"""\
        # Start JobGroup DNS updater inside the task runtime (skipped if
        # one is already running).
        if ! ([ -f {pid_file} ] &&
              kill -0 "$(cat {pid_file})" 2> /dev/null); then
          echo '{encoded_script}' | base64 -d > {script_path}
          chmod +x {script_path}
          (nohup {script_path} < /dev/null > {log_path} 2>&1 &) || true
        fi
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

        # Record our PID so the controller can check liveness (and skip
        # starting a duplicate updater) without pgrep, whose -f pattern
        # would match the checking shell's own command line.
        echo $$ > "/tmp/skypilot-jobgroup-dns-updater-{job_group_name}.pid"

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
    # (alphanumeric, hyphens, underscores only - see dag_utils.py:477-485),
    # so updater_process_name is safe to embed in shell commands and paths.
    updater_process_name = f'skypilot-jobgroup-dns-updater-{job_group_name}'
    script_path = f'/tmp/{updater_process_name}.sh'
    log_path = f'/tmp/{updater_process_name}.log'

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

        # Start the updater only if one is not already alive. This makes
        # re-pushes (post-recovery refreshes) safe on healthy surviving
        # nodes: their running updater is left untouched (restarting it
        # would open a window with no updater at all if the restart
        # failed), and repeated pushes never accumulate duplicate updater
        # processes. Liveness is checked via the PID file the updater
        # writes on startup -- NOT pgrep -f, whose pattern would match
        # this very command line (it contains the script path) and always
        # report "running".
        # Uses nohup with a subshell to fully detach from kubectl exec.
        # The 0.5s sleep gives a newly started updater time to write its
        # PID file on loaded systems.
        # Also create the marker file to signal networking setup is
        # initiated.
        marker_file = get_network_ready_marker_path(job_group_name)
        # Must match the PID file path written by the updater script in
        # generate_k8s_dns_updater_script.
        pid_file = f'/tmp/{updater_process_name}.pid'
        # Edge case: if the updater died and the OS recycled its PID for an
        # unrelated process, this reports alive and we skip a needed restart.
        alive_check = (f'[ -f {pid_file} ] && '
                       f'kill -0 "$(cat {pid_file})" 2> /dev/null')
        run_cmd = (f'if {alive_check}; then touch {marker_file}; else '
                   f'chmod +x {script_path} && '
                   f'(nohup {script_path} < /dev/null > {log_path} 2>&1 &) && '
                   f'sleep 0.5 && '
                   f'{alive_check} && '
                   f'touch {marker_file}; fi')
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


async def _setup_node_with_retries(
    make_attempt: Callable[[], Awaitable[bool]],
    node_label: str,
    setup_type: str,
) -> Optional[str]:
    """Run one node's networking setup with its own retry budget.

    Each node retries independently -- there is no barrier between nodes,
    so a slow or flaky node never delays the others' retries. A fresh
    attempt coroutine is built per try (a coroutine cannot be awaited
    twice), with a per-attempt timeout and jittered exponential backoff
    between tries.

    Args:
        make_attempt: Zero-arg factory building one attempt coroutine
            that resolves to True on success.
        node_label: '{task_name}-{node_idx}', for logs and failure
            reporting.
        setup_type: Human-readable setup kind ('K8s DNS updater' or
            '/etc/hosts').

    Returns:
        None on success; a short failure reason after the retry budget
        is exhausted. Never raises.
    """
    backoff = common_utils.Backoff(
        initial_backoff=_SETUP_RETRY_INITIAL_BACKOFF_SECONDS)
    reason = f'{setup_type} failed'
    for attempt in range(1, _SETUP_MAX_ATTEMPTS + 1):
        try:
            ok = await asyncio.wait_for(make_attempt(),
                                        timeout=_SETUP_ATTEMPT_TIMEOUT_SECONDS)
            if ok:
                if attempt > 1:
                    logger.info(f'{setup_type} succeeded on {node_label} '
                                f'(attempt {attempt}/{_SETUP_MAX_ATTEMPTS})')
                return None
            # The attempt logged its own error details.
            reason = f'{setup_type} failed'
        except asyncio.TimeoutError:
            reason = (f'{setup_type} timed out after '
                      f'{_SETUP_ATTEMPT_TIMEOUT_SECONDS:.0f}s')
        except Exception as e:  # pylint: disable=broad-except
            reason = (f'{setup_type} raised: '
                      f'{common_utils.format_exception(e)}')
        if attempt < _SETUP_MAX_ATTEMPTS:
            delay = backoff.current_backoff()
            logger.warning(f'{reason} on {node_label} (attempt '
                           f'{attempt}/{_SETUP_MAX_ATTEMPTS}); retrying in '
                           f'{delay:.1f}s')
            await asyncio.sleep(delay)
    logger.error(f'{reason} on {node_label}; giving up after '
                 f'{_SETUP_MAX_ATTEMPTS} attempts')
    return reason


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
    ) -> List[SetupFailure]:
        """Set up network configuration for JobGroup.

        Args:
            job_group_name: Name of the JobGroup.
            tasks_handles: List of (Task, ResourceHandle) tuples.

        Returns:
            Empty list when every node succeeded; otherwise one
            SetupFailure per node that failed after its retry budget.
        """
        return await NetworkConfigurator._inject_etc_hosts(
            job_group_name, tasks_handles)

    @staticmethod
    async def _inject_etc_hosts(
        job_group_name: str,
        tasks_handles: List[Tuple[
            'task_lib.Task', 'cloud_vm_ray_backend.CloudVmRayResourceHandle']],
    ) -> List[SetupFailure]:
        """Inject /etc/hosts entries for all clusters in the JobGroup.

        This maps the unified hostname format to actual addresses:
        - K8s: Write DNS mappings file for skylet's HostUpdater
        - SSH: Inject static internal IPs

        Every node runs with its own retry budget (see
        _setup_node_with_retries); a node that still fails is reported
        in the result rather than aborting the other nodes.

        Args:
            job_group_name: Name of the JobGroup.
            tasks_handles: List of (Task, ResourceHandle) tuples for all jobs.

        Returns:
            Empty list when every node succeeded; otherwise one
            SetupFailure per node that failed after its retry budget.
        """
        logger.info(f'Setting up networking on all {len(tasks_handles)} jobs')

        ssh_hosts_content = _generate_hosts_entries(job_group_name,
                                                    tasks_handles)
        k8s_dns_mappings = _generate_k8s_dns_mappings(job_group_name,
                                                      tasks_handles)

        setup_specs: List[_NodeSetupSpec] = []
        failures: List[SetupFailure] = []
        for task, handle in tasks_handles:
            if handle is None:
                # The cluster is in transition (e.g., a peer that is
                # itself mid-recovery). Not a failure: its own recovery
                # re-runs networking setup once it is back up.
                logger.warning(f'No handle for {task.name}; skipping its '
                               'networking setup')
                continue

            task_name = str(task.name)
            is_k8s = _is_kubernetes(handle)
            try:
                runners = handle.get_command_runners()
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Failed to get command runners for {task_name}: {e}')
                failures.append(
                    SetupFailure(task_name=task_name,
                                 node_label=task_name,
                                 reason=f'failed to get command runners: {e}'))
                continue

            for node_idx, runner in enumerate(runners):
                node_label = f'{task_name}-{node_idx}'
                if is_k8s:
                    setup_specs.append(
                        _NodeSetupSpec(make_attempt=functools.partial(
                            _start_k8s_dns_updater_on_node, runner,
                            k8s_dns_mappings, job_group_name),
                                       task_name=task_name,
                                       node_label=node_label,
                                       setup_type='K8s DNS updater'))
                else:
                    # ssh_hosts_content is always truthy (has header comment)
                    assert ssh_hosts_content, 'unreachable'
                    setup_specs.append(
                        _NodeSetupSpec(make_attempt=functools.partial(
                            _inject_hosts_on_node, runner, ssh_hosts_content,
                            job_group_name),
                                       task_name=task_name,
                                       node_label=node_label,
                                       setup_type='/etc/hosts'))
                logger.debug(f'Queued networking setup for {node_label}')

        if not setup_specs and not failures:
            logger.warning('No nodes to set up networking')
            return []

        logger.info(f'Setting up networking on {len(setup_specs)} nodes...')
        # _setup_node_with_retries never raises, so a plain gather is safe.
        results = await asyncio.gather(*[
            _setup_node_with_retries(spec.make_attempt, spec.node_label,
                                     spec.setup_type) for spec in setup_specs
        ])
        node_failures: List[SetupFailure] = [
            SetupFailure(task_name=spec.task_name,
                         node_label=spec.node_label,
                         reason=reason)
            for spec, reason in zip(setup_specs, results)
            if reason is not None
        ]
        failures.extend(node_failures)

        logger.info(f'Networking setup: {len(results) - len(node_failures)}/'
                    f'{len(results)} nodes succeeded')
        return failures


# ============================================================================
# Layer 4: Public API
# ============================================================================


async def setup_job_group_networking(
    job_group_name: str,
    tasks_handles: List[Tuple['task_lib.Task',
                              'cloud_vm_ray_backend.CloudVmRayResourceHandle']],
) -> List[SetupFailure]:
    """Set up networking for all tasks in a JobGroup.

    This is the main entry point for JobGroup networking setup. Each
    node is set up with its own retry budget; nodes that still fail are
    reported in the result so callers can decide how to surface them.

    Args:
        job_group_name: Name of the JobGroup.
        tasks_handles: List of (Task, ResourceHandle) tuples for each task.

    Returns:
        Empty list when every node succeeded; otherwise one SetupFailure
        per node that failed after its retry budget.
    """
    logger.info(f'Setting up networking for Job Group: {job_group_name}')
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

    If networking is not ready after the wait, the script fails the job
    (exit 1). It is only injected when the job group requires in-group
    networking (``inter_connection`` enabled, the default), so continuing
    without networking is never correct here.

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
    # Must match the PID file path written by the updater script in
    # generate_k8s_dns_updater_script.
    updater_pid_file = (f'/tmp/skypilot-jobgroup-dns-updater-'
                        f'{job_group_name}.pid')

    wait_script = textwrap.dedent(f"""
        # Wait for JobGroup networking to be ready. This job group requires
        # in-group networking (inter_connection), so failure to initialize
        # networking fails the job.
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
            echo "[SkyPilot] Error: Networking setup not initiated after ${{MARKER_ELAPSED}}s"
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
          UPDATER_PID_FILE="{updater_pid_file}"
          for hostname in $HOSTNAMES; do
            while ! getent hosts "$hostname" >/dev/null 2>&1; do
              if [ $ELAPSED -ge $MAX_WAIT ]; then
                echo "[SkyPilot] Error: Network setup timed out for \\"$hostname\\" after ${{ELAPSED}}s"
                echo "[SkyPilot] DNS updater running: $([ -f "$UPDATER_PID_FILE" ] && kill -0 "$(cat "$UPDATER_PID_FILE")" 2>/dev/null && echo 'yes' || echo 'no')"
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
        else
          echo "[SkyPilot] Error: this job group requires in-group networking (inter_connection is enabled) but networking failed to initialize; failing the job."
          echo "[SkyPilot] If tasks in this job group do not need to reach each other by hostname, set 'inter_connection: false' in the job group header."
          exit 1
        fi
    """)

    return wait_script.strip()
