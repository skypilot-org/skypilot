"""Kubernetes-native managed job provisioning.

This module provides a fast path for managed jobs on Kubernetes that bypasses
the full SkyPilot backend pipeline (Ray, Skylet, SSH, rsync). Instead, it
creates K8s Job resources directly with user commands baked into the pod spec.

For a 1004-node cluster, this reduces launch time from ~28 min to ~30s by
eliminating: internal_file_mounts, setup_runtime_on_cluster, Ray cluster setup,
Skylet startup, SSH-based user setup, and Ray-based job submission.
"""
import base64
import copy
import enum
import logging
import os
import textwrap
import time
from typing import Any, Dict, List, Optional, Tuple

import jinja2

from sky import cloud_stores
from sky import sky_logging
from sky.adaptors import kubernetes
from sky.data import data_utils
from sky.provision import constants as provision_constants
from sky.provision.kubernetes import constants as k8s_constants
from sky.provision.kubernetes import discovery_sidecar
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.skylet import constants as skylet_constants
from sky.utils import subprocess_utils
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

# Config key to enable the K8s-native managed job fast path.
# Set in ~/.sky/config.yaml:
#   jobs:
#     use_v1: true
_CONFIG_KEY = ('jobs', 'use_v1')


def is_managed_jobs_v1_enabled() -> bool:
    """Check if the K8s-native managed job fast path is enabled."""
    from sky import skypilot_config  # pylint: disable=import-outside-toplevel
    return bool(skypilot_config.get_nested(_CONFIG_KEY, False))

# Label to identify managed job resources created by this module.
TAG_MANAGED_JOB = 'skypilot-managed-job'
TAG_MANAGED_JOB_NAME = 'skypilot-managed-job-name'
# Mount path for the PVC that holds local workdir and file mounts.
FILEMOUNT_MOUNT_PATH = '/skypilot/mounts'

# How often to poll pod status when waiting.
_POLL_INTERVAL = 2
# Timeout waiting for pods to be running.
_POD_READY_TIMEOUT = 600  # 10 minutes


class ManagedJobStatus(enum.Enum):
    """Status of a K8s managed job, mapped from pod phases."""
    PENDING = 'PENDING'
    SETTING_UP = 'SETTING_UP'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    UNKNOWN = 'UNKNOWN'


def build_job_manifest(
    cluster_name_on_cloud: str,
    namespace: str,
    pod_spec: Dict[str, Any],
    num_nodes: int,
    setup_commands: Optional[str],
    run_commands: Optional[str],
    envs: Dict[str, str],
    num_gpus_per_node: int = 0,
    workdir: Optional[Dict[str, Any]] = None,
    file_mounts: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build K8s Job manifest and headless Service for a managed job.

    Args:
        cluster_name_on_cloud: Unique name for the cluster/job.
        namespace: K8s namespace to create resources in.
        pod_spec: Base pod spec from the cluster config (contains resource
            requests, image, volumes, etc.).
        num_nodes: Number of pods to create.
        setup_commands: User's setup commands (run before the main task).
        run_commands: User's run commands (the main task).
        envs: User environment variables to set in each pod.
        num_gpus_per_node: Number of GPUs per node.
        workdir: Workdir config dict. {'git': {...}} for git repos,
            {'path': '/local/path'} for local dirs. None if no workdir.
        file_mounts: Dict of remote_path -> local_path_or_cloud_uri.

    Returns:
        Tuple of (job_manifest, service_manifest, rbac_manifests).
        service_manifest is None for single-node jobs.
    """
    job_name = cluster_name_on_cloud

    # Build the entrypoint script that runs setup + run commands
    entrypoint = _build_entrypoint_script(
        job_name=job_name,
        namespace=namespace,
        num_nodes=num_nodes,
        setup_commands=setup_commands,
        run_commands=run_commands,
        num_gpus_per_node=num_gpus_per_node,
        workdir=workdir,
        file_mounts=file_mounts,
    )

    # Deep copy pod spec and modify for managed job
    job_pod_spec = copy.deepcopy(pod_spec)

    # Strip Ray-specific metadata and labels that don't apply
    metadata = job_pod_spec.get('metadata', {})
    labels = metadata.get('labels', {})
    # Keep skypilot labels, remove ray labels
    labels.pop(k8s_constants.TAG_RAY_CLUSTER_NAME, None)
    labels[TAG_MANAGED_JOB] = 'true'
    labels[TAG_MANAGED_JOB_NAME] = job_name
    labels[provision_constants.TAG_SKYPILOT_CLUSTER_NAME] = (
        cluster_name_on_cloud)

    # Override the container command to run our entrypoint
    containers = job_pod_spec['spec']['containers']
    # Keep only one container (strip any extra sidecars from Ray spec)
    main_container = containers[0]
    job_pod_spec['spec']['containers'] = [main_container]
    main_container['command'] = ['/bin/bash', '-c']
    main_container['args'] = [entrypoint]

    # Strip Ray-specific container config that conflicts
    main_container.pop('lifecycle', None)
    main_container.pop('readinessProbe', None)
    main_container.pop('livenessProbe', None)
    main_container.pop('startupProbe', None)

    # Add environment variables
    env_list = main_container.get('env', [])
    env_list.append({
        'name': 'SKYPILOT_NUM_NODES',
        'value': str(num_nodes),
    })
    env_list.append({
        'name': 'SKYPILOT_NUM_GPUS_PER_NODE',
        'value': str(num_gpus_per_node),
    })
    for key, value in envs.items():
        env_list.append({'name': key, 'value': str(value)})
    main_container['env'] = env_list

    # Remove init containers and restart policy (SkyPilot setup not needed)
    job_pod_spec['spec'].pop('initContainers', None)
    # Ensure pods don't restart on failure (controller handles recovery)
    job_pod_spec['spec']['restartPolicy'] = 'Never'

    # For indexed jobs, set subdomain for DNS-based peer discovery
    if num_nodes > 1:
        job_pod_spec['spec']['subdomain'] = job_name

    # Build the Job manifest
    job_manifest: Dict[str, Any] = {
        'apiVersion': 'batch/v1',
        'kind': 'Job',
        'metadata': {
            'name': job_name,
            'namespace': namespace,
            'labels': {
                TAG_MANAGED_JOB: 'true',
                TAG_MANAGED_JOB_NAME: job_name,
                provision_constants.TAG_SKYPILOT_CLUSTER_NAME:
                    (cluster_name_on_cloud),
            },
        },
        'spec': {
            'completions': num_nodes,
            'parallelism': num_nodes,
            'completionMode': 'Indexed',
            'backoffLimit': 0,  # No retries - managed job controller handles
            'template': {
                'metadata': {
                    'labels': labels,
                    'annotations': metadata.get('annotations', {}),
                },
                'spec': job_pod_spec['spec'],
            },
        },
    }

    # Build headless service for multi-node peer discovery
    service_manifest = None
    if num_nodes > 1:
        service_manifest = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {
                'name': job_name,
                'namespace': namespace,
                'labels': {
                    TAG_MANAGED_JOB: 'true',
                    TAG_MANAGED_JOB_NAME: job_name,
                    provision_constants.TAG_SKYPILOT_CLUSTER_NAME:
                        (cluster_name_on_cloud),
                },
            },
            'spec': {
                'clusterIP': 'None',  # Headless service
                'selector': {
                    TAG_MANAGED_JOB_NAME: job_name,
                },
                'publishNotReadyAddresses': True,
            },
        }

    # RBAC for discovery server (phase/app_status label patching, pod listing)
    sa = job_pod_spec['spec'].get('serviceAccountName', 'default')
    rbac_manifests = _build_rbac_manifests(job_name, namespace, sa)

    return job_manifest, service_manifest, rbac_manifests


def _build_workdir_block(workdir: Optional[Dict[str, Any]]) -> str:
    """Build entrypoint commands for workdir setup.

    Reuses SKY_REMOTE_WORKDIR constant from skylet for consistency.
    For git workdirs, generates git clone commands matching the
    standard path's CommandRunner.git_clone() behavior.
    For local workdirs uploaded via PVC, just cd to the mount path.
    """
    if not workdir:
        return ''
    remote_workdir = skylet_constants.SKY_REMOTE_WORKDIR

    if 'git' in workdir:
        git_config = workdir['git']
        git_url = git_config.get('url', '')
        git_ref = git_config.get('ref', '')
        block = textwrap.dedent(f"""\
            # === Workdir: git clone ===
            mkdir -p {remote_workdir}
            git clone {git_url} {remote_workdir}
            """)
        if git_ref:
            block += f'cd {remote_workdir} && git checkout {git_ref}\n'
        block += f'cd {remote_workdir}\n'
        return block
    elif 'path' in workdir:
        # Local workdir — uploaded as ConfigMap, mounted at
        # FILEMOUNT_MOUNT_PATH. ConfigMap keys use -- as path
        # separator (workdir--subdir--file.py). Reconstruct tree.
        return textwrap.dedent(f"""\
            # === Workdir: unpack from ConfigMap ===
            mkdir -p {remote_workdir}
            for f in {FILEMOUNT_MOUNT_PATH}/workdir--*; do
                [ -f "$f" ] || continue
                rel=$(basename "$f" | sed 's/^workdir--//' | sed 's/--/\\//g')
                mkdir -p "{remote_workdir}/$(dirname "$rel")"
                cp "$f" "{remote_workdir}/$rel"
            done
            cd {remote_workdir}
            """)
    return ''


def _build_file_mounts_block(file_mounts: Optional[Dict[str, str]]) -> str:
    """Build entrypoint commands for file mounts.

    For cloud URIs: reuses cloud_stores.get_storage_from_path() and its
    make_sync_dir_command()/make_sync_file_command() methods (same as
    cloud_vm_ray_backend._execute_file_mounts()).

    For local files: creates symlinks from the mount target to the PVC
    path where files were uploaded by upload_local_files_to_configmap().
    """
    if not file_mounts:
        return ''

    commands = []
    for remote_path, source in file_mounts.items():
        if data_utils.is_cloud_store_url(source):
            try:
                storage = cloud_stores.get_storage_from_path(source)
                if storage.is_directory(source):
                    mkdir_cmd = f'mkdir -p {remote_path}'
                    sync_cmd = storage.make_sync_dir_command(
                        source=source, destination=remote_path)
                else:
                    mkdir_cmd = (f'mkdir -p $(dirname {remote_path})')
                    sync_cmd = storage.make_sync_file_command(
                        source=source, destination=remote_path)
                commands.append(f'{mkdir_cmd} && {sync_cmd}')
            except Exception:  # pylint: disable=broad-except
                logger.warning(f'V1 fast path: cannot generate sync '
                               f'command for {source} -> {remote_path}')
        else:
            # Local file — uploaded as ConfigMap. Keys use -- separator.
            basename = os.path.basename(source.rstrip('/'))
            mount = FILEMOUNT_MOUNT_PATH
            local = os.path.expanduser(source)
            if os.path.isfile(local):
                commands.append(f'mkdir -p $(dirname {remote_path}) && '
                                f'cp {mount}/mounts--{basename} {remote_path}')
            else:
                commands.append(
                    f'mkdir -p {remote_path} && '
                    f'for f in {mount}/mounts--{basename}--*; do '
                    f'[ -f "$f" ] || continue; '
                    f'rel=$(basename "$f" | '
                    f'sed "s/^mounts--{basename}--//" | '
                    f'sed "s/--/\\//g"); '
                    f'mkdir -p "{remote_path}/$(dirname \\"$rel\\")"; '
                    f'cp "$f" "{remote_path}/$rel"; done')

    if not commands:
        return ''
    return '# === File Mounts ===\n' + '\n'.join(commands) + '\n'


def _build_entrypoint_script(
    job_name: str,
    namespace: str,
    num_nodes: int,
    setup_commands: Optional[str],
    run_commands: Optional[str],
    num_gpus_per_node: int = 0,
    workdir: Optional[Dict[str, Any]] = None,
    file_mounts: Optional[Dict[str, str]] = None,
) -> str:
    """Build the shell script that runs as the pod's entrypoint.

    This script:
    1. Sets SKYPILOT_NODE_RANK from JOB_COMPLETION_INDEX
    2. Starts background discovery server on localhost:9876
    3. Patches pod labels at phase transitions (PENDING → SETTING_UP → RUNNING)
    4. For multi-node: waits for peers via /nodes endpoint
    5. Sets up workdir (git clone) and file mounts
    6. Runs user setup commands
    7. Runs user run commands
    """
    # Build workdir setup block
    workdir_block = _build_workdir_block(workdir)

    # Build file mounts download block
    file_mounts_block = _build_file_mounts_block(file_mounts)

    setup_block = ''
    if setup_commands:
        setup_block = textwrap.dedent(f"""\
            # === User Setup ===
            _skypilot_set_phase SETTING_UP
            {setup_commands}
            """)

    run_block = ''
    if run_commands:
        run_block = textwrap.dedent(f"""\
            # === User Run ===
            _skypilot_set_phase RUNNING
            {run_commands}
            """)

    if num_nodes > 1:
        peer_discovery = textwrap.dedent(f"""\
            # === Wait for all nodes via /nodes endpoint ===
            # Count nodes whose entrypoint started (status in PENDING/SETTING_UP/RUNNING)
            _skylog "Waiting for {num_nodes} initialized nodes..."
            RETRIES=0
            MAX_RETRIES=120
            while true; do
                NODE_COUNT=$(skypilot_nodes 2>/dev/null | python3 -c "
import sys,json; d=json.load(sys.stdin)
# Handle both single-task ('nodes' key) and job-group (task-name keys) formats
nodes = d.get('nodes', [])
if not nodes:
    for v in d.values():
        if isinstance(v, list): nodes.extend(v)
ok = ('PENDING','SETTING_UP','RUNNING')
print(len([n for n in nodes if n.get('status') in ok]))
" 2>/dev/null || echo 0)
                if [ "$NODE_COUNT" -ge {num_nodes} ] 2>/dev/null; then
                    break
                fi
                RETRIES=$((RETRIES + 1))
                if [ $RETRIES -ge $MAX_RETRIES ]; then
                    _skylog "WARNING: Only found $NODE_COUNT/{num_nodes} nodes after $MAX_RETRIES retries"
                    break
                fi
                sleep 1
            done
            # Export IPs for compatibility
            PEER_IPS=$(skypilot_nodes 2>/dev/null | python3 -c "
            import sys,json
            nodes=json.load(sys.stdin).get('nodes',[])
            print('\\n'.join(n['ip'] for n in nodes if n.get('ip')))" 2>/dev/null || true)
            export SKYPILOT_NODE_IPS="$PEER_IPS"
            _skylog "Discovered $(echo "$SKYPILOT_NODE_IPS" | wc -l) nodes"
            """)
    else:
        peer_discovery = textwrap.dedent("""\
            # Single node - no peer discovery needed
            export SKYPILOT_NODE_IPS="$(hostname -i)"
            """)

    # The discovery server script is embedded in the entrypoint as a
    # background process. We base64-encode it to avoid shell quoting issues.
    discovery_script_b64 = discovery_sidecar.DISCOVERY_SERVER_SCRIPT_B64
    discovery_port = discovery_sidecar.DISCOVERY_SERVER_PORT

    script = textwrap.dedent(f"""\
        #!/bin/bash
        set -eo pipefail

        # === SkyPilot Managed Job Entrypoint ===
        export SKYPILOT_NODE_RANK=${{JOB_COMPLETION_INDEX:-0}}
        export SKYPILOT_NUM_NODES={num_nodes}
        export SKYPILOT_NUM_GPUS_PER_NODE={num_gpus_per_node}
        export SKYPILOT_SERVICE_NAME={job_name}
        export SKYPILOT_NAMESPACE={namespace}
        export SKYPILOT_DISCOVERY_PORT={discovery_port}
        export SKYPILOT_APP_STATUS=/tmp/skypilot_app_status

        # SkyPilot internal messages go to a separate file (hidden from log streaming)
        _skylog() {{ echo "$@" >> /tmp/skypilot_internal.log; }}

        # Phase label: patch pod label to track entrypoint progress
        _skypilot_set_phase() {{
          python3 -c "
        import urllib.request, json, ssl, os
        token = open('/var/run/secrets/kubernetes.io/serviceaccount/token').read().strip()
        ns = os.environ.get('SKYPILOT_NAMESPACE', 'default')
        pod = os.environ.get('HOSTNAME', '')
        url = f'https://kubernetes.default.svc/api/v1/namespaces/{{ns}}/pods/{{pod}}'
        data = json.dumps({{'metadata':{{'labels':{{'skypilot-node-phase':'$1'}}}}}}).encode()
        req = urllib.request.Request(url, data=data, method='PATCH')
        req.add_header('Authorization', f'Bearer {{token}}')
        req.add_header('Content-Type', 'application/strategic-merge-patch+json')
        ctx = ssl.create_default_context(cafile='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
        urllib.request.urlopen(req, context=ctx)
        " 2>/dev/null || true
        }}

        # Helper: query discovery server for node list
        skypilot_nodes() {{
          python3 -c "import urllib.request,sys;print(urllib.request.urlopen('http://localhost:{discovery_port}/nodes').read().decode())" 2>/dev/null \\
            || curl -s "http://localhost:{discovery_port}/nodes" 2>/dev/null \\
            || echo '{{"error":"discovery server not ready"}}'
        }}
        export -f skypilot_nodes

        # Start background discovery server
        _skylog "Starting discovery server on port {discovery_port}..."
        echo '{discovery_script_b64}' | base64 -d > /tmp/skypilot_discovery.py
        python3 /tmp/skypilot_discovery.py &
        DISCOVERY_PID=$!
        sleep 1

        _skypilot_set_phase PENDING
        _skylog "SkyPilot managed job starting on node rank $SKYPILOT_NODE_RANK / $SKYPILOT_NUM_NODES"

        {peer_discovery}
        {workdir_block}
        {file_mounts_block}
        {setup_block}
        {run_block}
        """)
    return script


def apply_managed_job(
    namespace: str,
    context: Optional[str],
    job_manifest: Dict[str, Any],
    service_manifest: Optional[Dict[str, Any]],
    rbac_manifests: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Apply K8s Job and Service manifests.

    Returns:
        The name of the created Job.
    """
    # Create RBAC first (needed for discovery server label patching)
    for manifest in (rbac_manifests or []):
        kind = manifest['kind']
        name = manifest['metadata']['name']
        try:
            if kind == 'Role':
                kubernetes.auth_api(context).create_namespaced_role(
                    namespace, manifest)
            elif kind == 'RoleBinding':
                kubernetes.auth_api(context).create_namespaced_role_binding(
                    namespace, manifest)
            logger.debug(f'Created {kind} {name}')
        except kubernetes.api_exception() as e:
            if e.status == 409:
                logger.debug(f'{kind} {name} already exists.')
            else:
                raise

    # Create headless service first (needed for DNS before pods start)
    if service_manifest is not None:
        try:
            kubernetes.core_api(context).create_namespaced_service(
                namespace, service_manifest)
            svc_name = service_manifest['metadata']['name']
            logger.debug(f'Created headless service {svc_name}')
        except kubernetes.api_exception() as e:
            if e.status == 409:  # Already exists
                logger.debug('Headless service already exists, continuing.')
            else:
                raise

    # Create the Job
    job_name = job_manifest['metadata']['name']
    try:
        kubernetes.batch_api(context).create_namespaced_job(
            namespace, job_manifest)
        logger.info(f'Created K8s Job {job_name}')
    except kubernetes.api_exception() as e:
        if e.status == 409:  # Already exists
            logger.debug(f'Job {job_name} already exists, continuing.')
        else:
            raise

    return job_name


def upload_local_files_to_configmap(
    job_name: str,
    namespace: str,
    context: Optional[str],
    workdir: Optional[Dict[str, Any]],
    file_mounts: Optional[Dict[str, str]],
) -> Optional[str]:
    """Upload local workdir and file_mounts to a K8s ConfigMap.

    Creates a ConfigMap containing all local files. The ConfigMap is
    mounted read-only in job pods at FILEMOUNT_MOUNT_PATH.
    Works on any K8s cluster (no storage class requirements).

    Limitation: ConfigMap data is limited to ~1MB total. For larger
    workdirs, use git workdir or cloud storage file_mounts instead.

    Returns the ConfigMap name if created, None if no local files.
    """
    # Collect local files to upload
    files_data: Dict[str, str] = {}
    binary_data: Dict[str, bytes] = {}

    if workdir and 'path' in workdir:
        local_workdir = os.path.expanduser(workdir['path'])
        if os.path.isdir(local_workdir):
            for root, _, filenames in os.walk(local_workdir):
                for fname in filenames:
                    full_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(full_path, local_workdir)
                    # ConfigMap keys use -- as path separator
                    key = f'workdir--{rel_path.replace("/", "--")}'
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            files_data[key] = f.read()
                    except (UnicodeDecodeError, IsADirectoryError):
                        with open(full_path, 'rb') as f:
                            binary_data[key] = f.read()

    if file_mounts:
        for _, source in file_mounts.items():
            if data_utils.is_cloud_store_url(source):
                continue
            local_path = os.path.expanduser(source)
            if os.path.isfile(local_path):
                basename = os.path.basename(local_path)
                key = f'mounts--{basename}'
                try:
                    with open(local_path, 'r', encoding='utf-8') as f:
                        files_data[key] = f.read()
                except UnicodeDecodeError:
                    with open(local_path, 'rb') as f:
                        binary_data[key] = f.read()
            elif os.path.isdir(local_path):
                for root, _, filenames in os.walk(local_path):
                    for fname in filenames:
                        full_path = os.path.join(root, fname)
                        rel = os.path.relpath(full_path, local_path)
                        basename = os.path.basename(local_path.rstrip('/'))
                        key = f'mounts--{basename}--{rel.replace("/", "--")}'
                        try:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                files_data[key] = f.read()
                        except (UnicodeDecodeError, IsADirectoryError):
                            with open(full_path, 'rb') as f:
                                binary_data[key] = f.read()

    if not files_data and not binary_data:
        return None

    # ConfigMap limit is ~1MB. Check total size before creating.
    total_size = (sum(len(v.encode()) for v in files_data.values()) +
                  sum(len(v) for v in binary_data.values()))
    if total_size > 900_000:  # Leave margin below 1MB etcd limit
        raise ValueError(
            f'Local workdir/file_mounts total size ({total_size} bytes) '
            f'exceeds K8s ConfigMap limit (~1MB). Use a git workdir or '
            f'cloud storage file_mounts for larger files.')

    cm_name = f'{job_name}-files'
    cm_manifest = {
        'apiVersion': 'v1',
        'kind': 'ConfigMap',
        'metadata': {
            'name': cm_name,
            'namespace': namespace,
            'labels': {
                TAG_MANAGED_JOB: 'true',
                TAG_MANAGED_JOB_NAME: job_name,
            },
        },
        'data': files_data,
    }
    if binary_data:
        cm_manifest['binaryData'] = {
            k: base64.b64encode(v).decode() for k, v in binary_data.items()
        }

    try:
        kubernetes.core_api(context).create_namespaced_config_map(
            namespace, cm_manifest)
        logger.debug(f'Created ConfigMap {cm_name} with '
                     f'{len(files_data) + len(binary_data)} files')
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.debug(f'ConfigMap {cm_name} already exists')
        else:
            raise

    return cm_name


def wait_for_pods_running(
    job_name: str,
    namespace: str,
    context: Optional[str],
    num_nodes: int,
    timeout: int = _POD_READY_TIMEOUT,
) -> List[str]:
    """Wait for all pods in the Job to be in Running phase.

    Returns:
        List of pod names.
    """
    label_selector = f'{TAG_MANAGED_JOB_NAME}={job_name}'
    start = time.time()
    while time.time() - start < timeout:
        pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace, label_selector=label_selector)
        running_count = 0
        pod_names = []
        for pod in pods.items:
            pod_names.append(pod.metadata.name)
            if pod.status.phase in ('Running', 'Succeeded'):
                running_count += 1
            elif pod.status.phase == 'Failed':
                raise RuntimeError(f'Pod {pod.metadata.name} failed: '
                                   f'{_get_pod_failure_reason(pod)}')
        if running_count >= num_nodes:
            logger.info(f'All {num_nodes} pods are running for job {job_name}')
            return pod_names
        logger.debug(
            f'Waiting for pods: {running_count}/{num_nodes} running. '
            f'Pods: {[(p.metadata.name, p.status.phase) for p in pods.items]}')
        time.sleep(_POLL_INTERVAL)

    raise TimeoutError(f'Timed out waiting for {num_nodes} pods to be running '
                       f'for job {job_name} (timeout={timeout}s)')


def get_job_status(
    job_name: str,
    namespace: str,
    context: Optional[str],
) -> ManagedJobStatus:
    """Get the status of a K8s managed job by checking pod phases.

    Returns:
        ManagedJobStatus representing the aggregate status.
    """
    label_selector = f'{TAG_MANAGED_JOB_NAME}={job_name}'
    try:
        pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace, label_selector=label_selector)
    except (kubernetes.api_exception(), kubernetes.max_retry_error()) as e:
        logger.warning(f'Failed to get pods for job {job_name}: {e}')
        return ManagedJobStatus.UNKNOWN

    if not pods.items:
        return ManagedJobStatus.UNKNOWN

    phases = [pod.status.phase for pod in pods.items]

    # If any pod failed, the job failed
    if 'Failed' in phases:
        return ManagedJobStatus.FAILED

    # If all pods succeeded, the job succeeded
    if all(p == 'Succeeded' for p in phases):
        return ManagedJobStatus.SUCCEEDED

    # If all pods are running or succeeded, the job is running
    if all(p in ('Running', 'Succeeded') for p in phases):
        return ManagedJobStatus.RUNNING

    # If any pods are pending, still setting up
    if 'Pending' in phases:
        return ManagedJobStatus.SETTING_UP

    return ManagedJobStatus.RUNNING


def get_pod_logs(
    job_name: str,
    namespace: str,
    context: Optional[str],
    pod_index: Optional[int] = None,
    follow: bool = False,
    tail_lines: Optional[int] = None,
) -> str:
    """Get logs from pods in a managed job.

    Args:
        job_name: Name of the K8s Job.
        namespace: K8s namespace.
        context: K8s context.
        pod_index: If set, get logs from a specific pod index.
            If None, get logs from all pods.
        follow: Whether to follow (stream) logs.
        tail_lines: Number of tail lines to return.

    Returns:
        Log content as a string.
    """
    label_selector = f'{TAG_MANAGED_JOB_NAME}={job_name}'
    pods = kubernetes.core_api(context).list_namespaced_pod(
        namespace, label_selector=label_selector)

    if not pods.items:
        return f'No pods found for job {job_name}'

    # Sort pods by their index (from job-completion-index annotation)
    sorted_pods = sorted(
        pods.items,
        key=lambda p: int(
            p.metadata.labels.get('batch.kubernetes.io/job-completion-index',
                                  '0')))

    if pod_index is not None:
        if pod_index >= len(sorted_pods):
            return (f'Pod index {pod_index} not found '
                    f'(total: {len(sorted_pods)})')
        sorted_pods = [sorted_pods[pod_index]]

    all_logs = []
    for pod in sorted_pods:
        pod_name = pod.metadata.name
        idx = pod.metadata.labels.get(
            'batch.kubernetes.io/job-completion-index', '?')
        try:
            kwargs = {}
            if tail_lines is not None:
                kwargs['tail_lines'] = tail_lines
            log = kubernetes.core_api(context).read_namespaced_pod_log(
                pod_name, namespace, follow=follow, **kwargs)
            all_logs.append(f'--- Node {idx} ({pod_name}) ---\n{log}')
        except kubernetes.api_exception() as e:
            all_logs.append(f'--- Node {idx} ({pod_name}) ---\n'
                            f'[Error getting logs: {e.reason}]')

    return '\n'.join(all_logs)


def delete_managed_job(
    job_name: str,
    namespace: str,
    context: Optional[str],
) -> None:
    """Delete a K8s managed job and its associated resources.

    This deletes the Job (with cascading pod deletion) and the headless
    Service used for peer discovery.
    """
    # Delete the Job with cascading deletion (kills all pods)
    try:
        kubernetes.batch_api(context).delete_namespaced_job(
            job_name,
            namespace,
            body=kubernetes.kubernetes.client.V1DeleteOptions(
                propagation_policy='Foreground'),
        )
        logger.info(f'Deleted K8s Job {job_name}')
    except kubernetes.api_exception() as e:
        if e.status == 404:
            logger.debug(f'Job {job_name} already deleted.')
        else:
            logger.warning(f'Failed to delete job {job_name}: {e}')

    # Delete the headless service
    try:
        kubernetes.core_api(context).delete_namespaced_service(
            job_name, namespace)
        logger.debug(f'Deleted headless service {job_name}')
    except kubernetes.api_exception() as e:
        if e.status == 404:
            logger.debug(f'Service {job_name} already deleted.')
        else:
            logger.warning(f'Failed to delete service {job_name}: {e}')

    # Delete RBAC resources
    rbac_name = f'{job_name}-discovery'
    try:
        kubernetes.auth_api(context).delete_namespaced_role_binding(
            rbac_name, namespace)
        logger.debug(f'Deleted RoleBinding {rbac_name}')
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.warning(f'Failed to delete RoleBinding {rbac_name}: {e}')
    try:
        kubernetes.auth_api(context).delete_namespaced_role(
            rbac_name, namespace)
        logger.debug(f'Deleted Role {rbac_name}')
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.warning(f'Failed to delete Role {rbac_name}: {e}')

    # Delete file mount ConfigMap (if it exists)
    cm_name = f'{job_name}-files'
    try:
        kubernetes.core_api(context).delete_namespaced_config_map(
            cm_name, namespace)
        logger.debug(f'Deleted ConfigMap {cm_name}')
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.warning(f'Failed to delete {cm_name}: {e}')


def get_pod_exit_codes(
    job_name: str,
    namespace: str,
    context: Optional[str],
) -> Dict[int, Optional[int]]:
    """Get exit codes for all pods in a managed job.

    Returns:
        Dict mapping pod index to exit code (None if not terminated).
    """
    label_selector = f'{TAG_MANAGED_JOB_NAME}={job_name}'
    pods = kubernetes.core_api(context).list_namespaced_pod(
        namespace, label_selector=label_selector)

    exit_codes = {}
    for pod in pods.items:
        idx = int(
            pod.metadata.labels.get('batch.kubernetes.io/job-completion-index',
                                    '0'))
        if (pod.status.container_statuses and
                pod.status.container_statuses[0].state.terminated):
            exit_codes[idx] = (
                pod.status.container_statuses[0].state.terminated.exit_code)
        else:
            exit_codes[idx] = None
    return exit_codes


def _get_pod_failure_reason(pod) -> str:
    """Extract failure reason from a pod."""
    if pod.status.container_statuses:
        for cs in pod.status.container_statuses:
            if cs.state.terminated:
                return (f'exit_code={cs.state.terminated.exit_code}, '
                        f'reason={cs.state.terminated.reason}, '
                        f'message={cs.state.terminated.message}')
            if cs.state.waiting:
                return (f'waiting: reason={cs.state.waiting.reason}, '
                        f'message={cs.state.waiting.message}')
    if pod.status.conditions:
        for cond in pod.status.conditions:
            if cond.type == 'Ready' and cond.status == 'False':
                return f'condition: {cond.reason}: {cond.message}'
    return f'phase={pod.status.phase}'


def is_managed_job_cluster(cluster_name_on_cloud: str, namespace: str,
                           context: Optional[str]) -> bool:
    """Check if a cluster is a K8s-native managed job.

    Returns True if there is a K8s Job or managed job pods with this name.
    """
    # Check for K8s Job resource
    try:
        kubernetes.batch_api(context).read_namespaced_job(
            cluster_name_on_cloud, namespace)
        return True
    except kubernetes.api_exception() as e:
        if e.status != 404:
            raise
    # Check for elastic job pods (individual pods, not a Job)
    label_selector = f'{TAG_MANAGED_JOB_NAME}={cluster_name_on_cloud}'
    try:
        pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace, label_selector=label_selector)
        return len(pods.items) > 0
    except kubernetes.api_exception():
        return False


# ---------------------------------------------------------------------------
# Elastic / DYNAMIC_NODE_SET support
# ---------------------------------------------------------------------------

TAG_NODE_INDEX = 'skypilot-node-index'

# Template file for elastic pod manifests
_ELASTIC_POD_TEMPLATE = 'kubernetes-managed-job.yaml.j2'

# Thread count for parallel pod creation (matches K8s provisioner)
_NUM_THREADS = None  # Lazily initialized


def _get_num_threads() -> int:
    global _NUM_THREADS
    if _NUM_THREADS is None:
        _NUM_THREADS = subprocess_utils.get_parallel_threads('kubernetes')
    return _NUM_THREADS


def render_elastic_pod(
    job_name: str,
    namespace: str,
    index: int,
    num_nodes: int,
    min_nodes: int,
    image: str,
    setup_commands: Optional[str],
    run_commands: Optional[str],
    envs: Dict[str, str],
    num_gpus_per_node: int = 0,
    cpu_request: Optional[str] = None,
    memory_request: Optional[str] = None,
    gpu_request: Optional[str] = None,
    gpu_resource_key: str = 'nvidia.com/gpu',
    service_account: Optional[str] = None,
    collocate: bool = False,
    node_selector: Optional[Dict[str, str]] = None,
    tolerations: Optional[List[Dict[str, str]]] = None,
    volumes: Optional[List[Dict[str, Any]]] = None,
    volume_mounts: Optional[List[Dict[str, Any]]] = None,
    jobgroup_tasks: Optional[str] = None,
    workdir: Optional[Dict[str, Any]] = None,
    file_mounts: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Render a single Pod manifest from the Jinja2 template.

    Uses sky/templates/kubernetes-managed-job.yaml.j2 for readability.
    """
    # __file__ is sky/provision/kubernetes/managed_job.py
    # sky root is 3 levels up: sky/provision/kubernetes -> sky/provision -> sky
    sky_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    template_path = os.path.join(sky_root, 'templates', _ELASTIC_POD_TEMPLATE)
    with open(template_path, 'r', encoding='utf-8') as f:
        template_str = f.read()

    j2_template = jinja2.Template(template_str)
    rendered = j2_template.render(
        job_name=job_name,
        namespace=namespace,
        index=index,
        num_nodes=num_nodes,
        min_nodes=min_nodes,
        num_gpus_per_node=num_gpus_per_node,
        image=image,
        setup_commands=setup_commands,
        run_commands=run_commands,
        user_envs=envs,
        cpu_request=cpu_request,
        memory_request=memory_request,
        gpu_request=gpu_request,
        gpu_resource_key=gpu_resource_key,
        discovery_port=discovery_sidecar.DISCOVERY_SERVER_PORT,
        discovery_script_b64=discovery_sidecar.DISCOVERY_SERVER_SCRIPT_B64,
        restart_policy='Never',
        service_account=service_account,
        collocate=collocate,
        anti_affinity=not collocate,
        node_selector=node_selector,
        tolerations=tolerations,
        volumes=volumes,
        volume_mounts=volume_mounts,
        jobgroup_tasks=jobgroup_tasks,
        workdir_block=_build_workdir_block(workdir),
        file_mounts_block=_build_file_mounts_block(file_mounts),
    )
    return yaml_utils.safe_load(rendered)


def build_elastic_job_manifests(
    cluster_name: str,
    namespace: str,
    pod_spec: Dict[str, Any],
    num_nodes: int,
    min_nodes: int,
    setup_commands: Optional[str],
    run_commands: Optional[str],
    envs: Dict[str, str],
    num_gpus_per_node: int = 0,
    service_account: Optional[str] = None,
    collocate: bool = False,
    tolerations: Optional[List[Dict[str, str]]] = None,
    node_selector: Optional[Dict[str, str]] = None,
    workdir: Optional[Dict[str, Any]] = None,
    file_mounts: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
    """Build all K8s manifests for an elastic managed job.

    Args:
        collocate: If True, add pod affinity to schedule pods together
            (network_best). If False, add anti-affinity to spread.

    Returns:
        (pod_manifests, service_manifest, rbac_manifests)
    """
    # Extract image and resource requests from pod_spec
    containers = pod_spec.get('spec', {}).get('containers', [{}])
    main_container = containers[0] if containers else {}
    image = main_container.get('image', 'python:3.11-slim')
    resources = main_container.get('resources', {})
    requests = resources.get('requests', {})
    limits = resources.get('limits', {})

    cpu_request = requests.get('cpu')
    memory_request = requests.get('memory')
    # Find GPU request from either requests or limits
    gpu_request = None
    gpu_key = 'nvidia.com/gpu'
    for key in list(requests.keys()) + list(limits.keys()):
        if 'gpu' in key.lower():
            gpu_request = str(requests.get(key) or limits.get(key))
            gpu_key = key
            break

    # Check if a PVC was created for local files — if so, add volume mount
    volumes_extra = None
    volume_mounts_extra = None
    cm_name = f'{cluster_name}-files'
    has_local_files = ((workdir and 'path' in workdir) or (file_mounts and any(
        not data_utils.is_cloud_store_url(s) for s in file_mounts.values())))
    if has_local_files:
        volumes_extra = [{
            'name': 'skypilot-files',
            'configMap': {
                'name': cm_name
            },
        }]
        volume_mounts_extra = [{
            'name': 'skypilot-files',
            'mountPath': FILEMOUNT_MOUNT_PATH,
        }]

    # Build pod manifests
    pod_manifests = []
    for i in range(num_nodes):
        pod = render_elastic_pod(
            job_name=cluster_name,
            namespace=namespace,
            index=i,
            num_nodes=num_nodes,
            min_nodes=min_nodes,
            image=image,
            setup_commands=setup_commands,
            run_commands=run_commands,
            envs=envs,
            num_gpus_per_node=num_gpus_per_node,
            cpu_request=cpu_request,
            memory_request=memory_request,
            gpu_request=gpu_request,
            gpu_resource_key=gpu_key,
            service_account=service_account,
            collocate=collocate,
            tolerations=tolerations,
            node_selector=node_selector,
            volumes=volumes_extra,
            volume_mounts=volume_mounts_extra,
            workdir=workdir,
            file_mounts=file_mounts,
        )
        pod_manifests.append(pod)

    # Headless service
    service_manifest = {
        'apiVersion': 'v1',
        'kind': 'Service',
        'metadata': {
            'name': cluster_name,
            'namespace': namespace,
            'labels': {
                TAG_MANAGED_JOB: 'true',
                TAG_MANAGED_JOB_NAME: cluster_name,
            },
        },
        'spec': {
            'clusterIP': 'None',
            'selector': {
                TAG_MANAGED_JOB_NAME: cluster_name
            },
            'publishNotReadyAddresses': True,
        },
    }

    # RBAC so pods can read Endpoints for peer discovery
    sa = service_account or 'default'
    rbac_manifests = _build_rbac_manifests(cluster_name, namespace, sa)

    return pod_manifests, service_manifest, rbac_manifests


def _build_rbac_manifests(job_name: str, namespace: str,
                          service_account: str) -> List[Dict[str, Any]]:
    """Build Role + RoleBinding for Endpoints read and Pods read/patch access."""
    role = {
        'apiVersion': 'rbac.authorization.k8s.io/v1',
        'kind': 'Role',
        'metadata': {
            'name': f'{job_name}-discovery',
            'namespace': namespace,
            'labels': {
                TAG_MANAGED_JOB: 'true',
                TAG_MANAGED_JOB_NAME: job_name,
            },
        },
        'rules': [{
            'apiGroups': [''],
            'resources': ['endpoints'],
            'verbs': ['get', 'list', 'watch'],
        }, {
            'apiGroups': [''],
            'resources': ['pods'],
            'verbs': ['get', 'list', 'patch'],
        }],
    }
    role_binding = {
        'apiVersion': 'rbac.authorization.k8s.io/v1',
        'kind': 'RoleBinding',
        'metadata': {
            'name': f'{job_name}-discovery',
            'namespace': namespace,
            'labels': {
                TAG_MANAGED_JOB: 'true',
                TAG_MANAGED_JOB_NAME: job_name,
            },
        },
        'subjects': [{
            'kind': 'ServiceAccount',
            'name': service_account,
            'namespace': namespace,
        }],
        'roleRef': {
            'kind': 'Role',
            'name': f'{job_name}-discovery',
            'apiGroup': 'rbac.authorization.k8s.io',
        },
    }
    return [role, role_binding]


def apply_elastic_job(
    namespace: str,
    context: Optional[str],
    pod_manifests: List[Dict[str, Any]],
    service_manifest: Dict[str, Any],
    rbac_manifests: List[Dict[str, Any]],
) -> List[str]:
    """Apply all manifests for an elastic job.

    Creates pods in parallel using a thread pool for fast large-scale
    creation (e.g. 1000+ pods).

    Returns list of created pod names.
    """
    # Create RBAC first
    for manifest in rbac_manifests:
        kind = manifest['kind']
        name = manifest['metadata']['name']
        try:
            if kind == 'Role':
                kubernetes.auth_api(context).create_namespaced_role(
                    namespace, manifest)
            elif kind == 'RoleBinding':
                kubernetes.auth_api(context).create_namespaced_role_binding(
                    namespace, manifest)
            logger.debug(f'Created {kind} {name}')
        except kubernetes.api_exception() as e:
            if e.status == 409:
                logger.debug(f'{kind} {name} already exists.')
            else:
                raise

    # Create headless service
    try:
        kubernetes.core_api(context).create_namespaced_service(
            namespace, service_manifest)
        logger.debug(
            f'Created headless service {service_manifest["metadata"]["name"]}')
    except kubernetes.api_exception() as e:
        if e.status == 409:
            logger.debug('Headless service already exists.')
        else:
            raise

    # Create pods in parallel with bounded concurrency to avoid
    # overwhelming the K8s API server.
    max_concurrent = min(50, _get_num_threads())

    def _create_pod(pod_manifest: Dict[str, Any]) -> Optional[str]:
        pod_name = pod_manifest['metadata']['name']
        for attempt in range(3):
            try:
                kubernetes.core_api(context).create_namespaced_pod(
                    namespace, pod_manifest)
                return pod_name
            except kubernetes.api_exception() as e:
                if e.status == 409:
                    return pod_name  # Already exists
                if attempt < 2:
                    time.sleep(1)
                    continue
                logger.warning(f'Failed to create pod {pod_name}: {e}')
                return None
            except Exception as e:  # pylint: disable=broad-except
                if attempt < 2:
                    time.sleep(1)
                    continue
                logger.warning(f'Failed to create pod {pod_name}: {e}')
                return None
        return None

    results = subprocess_utils.run_in_parallel(_create_pod, pod_manifests,
                                               max_concurrent)
    pod_names = [r for r in results if r is not None]
    failed_count = len(pod_manifests) - len(pod_names)

    if failed_count > 0:
        logger.warning(f'Failed to create {failed_count}/{len(pod_manifests)} '
                       f'elastic job pods')
    logger.info(f'Created {len(pod_names)} elastic job pods')
    return pod_names


def create_replacement_pod(
    job_name: str,
    namespace: str,
    context: Optional[str],
    index: int,
    num_nodes: int,
    min_nodes: int,
    image: str,
    setup_commands: Optional[str],
    run_commands: Optional[str],
    envs: Dict[str, str],
    num_gpus_per_node: int = 0,
    collocate: bool = False,
    service_account: Optional[str] = None,
    gpu_resource_key: str = 'nvidia.com/gpu',
    tolerations: Optional[List[Dict[str, str]]] = None,
    node_selector: Optional[Dict[str, str]] = None,
) -> str:
    """Create a single replacement pod for a failed index.

    First deletes any existing pod with this index, then creates a new one.
    Returns the pod name.
    """
    pod_name = f'{job_name}-{index}'

    # Delete existing pod if any, then wait for it to be fully removed
    try:
        kubernetes.core_api(context).delete_namespaced_pod(
            pod_name, namespace, grace_period_seconds=0)
        logger.debug(f'Deleted existing pod {pod_name}')
        # Wait for the old pod to be fully removed from etcd to avoid
        # 409 Conflict when creating the replacement with the same name.
        for _ in range(30):
            try:
                kubernetes.core_api(context).read_namespaced_pod(
                    pod_name, namespace)
                time.sleep(1)
            except kubernetes.api_exception() as poll_e:
                if poll_e.status == 404:
                    break
                raise
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.warning(f'Failed to delete pod {pod_name}: {e}')

    # Create new pod from template
    gpu_request = str(num_gpus_per_node) if num_gpus_per_node > 0 else None
    pod_manifest = render_elastic_pod(
        job_name=job_name,
        namespace=namespace,
        index=index,
        num_nodes=num_nodes,
        min_nodes=min_nodes,
        image=image,
        setup_commands=setup_commands,
        run_commands=run_commands,
        envs=envs,
        num_gpus_per_node=num_gpus_per_node,
        gpu_request=gpu_request,
        gpu_resource_key=gpu_resource_key,
        collocate=collocate,
        service_account=service_account,
        tolerations=tolerations,
        node_selector=node_selector,
    )
    kubernetes.core_api(context).create_namespaced_pod(namespace, pod_manifest)
    logger.info(f'Created replacement pod {pod_name}')
    return pod_name


def _get_main_container_state(pod) -> str:
    """Get the state of the 'main' container in a pod.

    With a sidecar, pod phase stays 'Running' even after the main container
    exits. We check the main container directly for accurate status.

    Returns: 'running', 'succeeded', 'failed', or 'pending'.
    """
    if not pod.status or not pod.status.container_statuses:
        return 'pending'
    for cs in pod.status.container_statuses:
        if cs.name == 'main':
            if cs.state.terminated:
                return ('succeeded'
                        if cs.state.terminated.exit_code == 0 else 'failed')
            if cs.state.running:
                return 'running'
            if cs.state.waiting:
                return 'pending'
    # Fallback to pod phase
    phase = pod.status.phase if pod.status else 'Unknown'
    if phase == 'Succeeded':
        return 'succeeded'
    if phase == 'Failed':
        return 'failed'
    if phase == 'Running':
        return 'running'
    return 'pending'


def get_elastic_job_status(
    job_name: str,
    namespace: str,
    context: Optional[str],
    min_nodes: int,
) -> Tuple[ManagedJobStatus, int, int, int]:
    """Get status of an elastic job with per-pod tracking.

    Checks the 'main' container state (not pod phase) because the
    discovery sidecar keeps pods in Running phase after the user
    command completes.

    Returns:
        (status, running_count, succeeded_count, failed_count)
    """
    label_selector = f'{TAG_MANAGED_JOB_NAME}={job_name}'
    try:
        pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace, label_selector=label_selector)
    except (kubernetes.api_exception(), kubernetes.max_retry_error()) as e:
        logger.warning(f'Failed to list pods for {job_name}: {e}')
        return ManagedJobStatus.UNKNOWN, 0, 0, 0

    running = 0
    succeeded = 0
    failed = 0
    pending = 0
    for pod in pods.items:
        state = _get_main_container_state(pod)
        if state == 'running':
            running += 1
        elif state == 'succeeded':
            succeeded += 1
        elif state == 'failed':
            failed += 1
        else:
            pending += 1

    total = running + succeeded + failed + pending

    # All pods' main containers have succeeded (none pending/running/failed)
    if succeeded == total and total > 0:
        return ManagedJobStatus.SUCCEEDED, running, succeeded, failed

    # All non-succeeded pods have failed — terminal failure
    if failed > 0 and running == 0 and pending == 0:
        return ManagedJobStatus.FAILED, running, succeeded, failed

    # Below min_nodes with main container still running
    if running < min_nodes:
        return ManagedJobStatus.SETTING_UP, running, succeeded, failed

    # Enough pods running
    if running >= min_nodes:
        return ManagedJobStatus.RUNNING, running, succeeded, failed

    return ManagedJobStatus.PENDING, running, succeeded, failed


def get_failed_pod_indices(
    job_name: str,
    namespace: str,
    context: Optional[str],
) -> List[int]:
    """Get indices of failed pods that need replacement."""
    label_selector = f'{TAG_MANAGED_JOB_NAME}={job_name}'
    try:
        pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace, label_selector=label_selector)
    except Exception as e:
        logger.warning(f'Failed to list pods for {job_name}: {e}')
        return []

    failed_indices = []
    for pod in pods.items:
        state = _get_main_container_state(pod)
        if state == 'failed':
            idx = int(pod.metadata.labels.get(TAG_NODE_INDEX, '-1'))
            if idx >= 0:
                failed_indices.append(idx)
    return failed_indices


def get_pods_to_replace(
    job_name: str,
    namespace: str,
    context: Optional[str],
    num_nodes: int,
) -> List[int]:
    """Get indices of pods that need replacement.

    This includes both:
    - Failed pods (main container exited with non-zero)
    - Missing pods (expected index doesn't exist at all, e.g. force-deleted)

    Does NOT replace pods whose main container succeeded (exit 0).
    """
    label_selector = f'{TAG_MANAGED_JOB_NAME}={job_name}'
    try:
        pods = kubernetes.core_api(context).list_namespaced_pod(
            namespace, label_selector=label_selector)
    except Exception as e:
        logger.warning(f'Failed to list pods for {job_name}: {e}')
        return []

    # Track which indices exist and their states
    existing: Dict[int, str] = {}
    for pod in pods.items:
        idx = int(pod.metadata.labels.get(TAG_NODE_INDEX, '-1'))
        if idx >= 0:
            existing[idx] = _get_main_container_state(pod)

    to_replace = []
    for i in range(num_nodes):
        state = existing.get(i)
        if state is None:
            # Pod missing entirely (force-deleted or never created)
            to_replace.append(i)
        elif state == 'failed':
            # Pod exists but main container failed
            to_replace.append(i)
        # 'running' and 'succeeded' pods are fine, don't replace
    return to_replace


def delete_elastic_job(
    job_name: str,
    namespace: str,
    context: Optional[str],
) -> None:
    """Delete all resources for an elastic job."""
    # Delete all pods
    label_selector = f'{TAG_MANAGED_JOB_NAME}={job_name}'
    try:
        kubernetes.core_api(context).delete_collection_namespaced_pod(
            namespace, label_selector=label_selector)
        logger.info(f'Deleted pods for elastic job {job_name}')
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.warning(f'Failed to delete pods: {e}')

    # Delete headless service
    try:
        kubernetes.core_api(context).delete_namespaced_service(
            job_name, namespace)
        logger.debug(f'Deleted service {job_name}')
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.warning(f'Failed to delete service: {e}')

    # Delete RBAC
    rbac_name = f'{job_name}-discovery'
    try:
        kubernetes.auth_api(context).delete_namespaced_role(
            rbac_name, namespace)
        logger.debug(f'Deleted role {rbac_name}')
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.warning(f'Failed to delete role {rbac_name}: {e}')
    try:
        kubernetes.auth_api(context).delete_namespaced_role_binding(
            rbac_name, namespace)
        logger.debug(f'Deleted role binding {rbac_name}')
    except kubernetes.api_exception() as e:
        if e.status != 404:
            logger.warning(f'Failed to delete role binding {rbac_name}: {e}')
