"""Golden snapshot tests for sky/templates/kubernetes-ray.yml.j2.

``kubernetes-ray.yml.j2`` is a large, load-bearing template: it renders the
full Ray autoscaler config and Kubernetes pod spec for every cluster launched
on Kubernetes. It has many Jinja guards (``is defined`` / ``is not none``) whose
combinations decide which node selectors, affinities, tolerations, sidecars,
volumes and resource requests appear in the rendered manifest.

These tests pin the *rendered output* for a representative set of input
permutations so that a future refactor of the template — or of the variable
plumbing that feeds it — can be checked against a golden baseline. A refactor
that changes the rendered manifest for any covered input shows up as a readable
diff instead of a silent behavior change.

How it renders
--------------
Each case is rendered through the same helper production uses,
``common_utils.fill_template``, so the tests exercise the real Jinja
environment (undefined-variable and whitespace semantics), not a hand-rolled
one.

The fixture
-----------
``base_variables()`` returns the variables dict for a minimal CPU-only,
single-node launch. Its keys are derived from the two places that build the
template variables in production:

* ``Kubernetes.make_deploy_resources_variables`` (sky/clouds/kubernetes.py) —
  the ``deploy_vars`` dict and its conditionally-added keys
  (``k8s_cpu_limit``, ``k8s_ephemeral_storage``, the ``k8s_enable_*`` docker /
  gpudirect flags, ...).
* ``backend_utils.write_cluster_config`` — the extra keys merged on top
  (``cluster_name_on_cloud``, ``num_nodes``, ``credentials``, ``labels``,
  ``volume_mounts``, the install-command blobs, ...), plus
  ``initial_setup_commands`` from ``resources.py``.

Opaque command blobs (e.g. ``ray_installation_commands``) are set to short
placeholder strings rather than the real multi-hundred-line constants: the
goal is to pin the *template structure*, not to re-pin an unrelated constant
every time it changes. Machine- or time-specific values (usernames, wheel
hashes, local paths) are pinned to fixed literals so the goldens are
deterministic. Volume-mount entries are plain dicts with the same fields as the
``VolumeInfo`` objects production passes (Jinja attribute access falls back to
dict lookup), keeping the fixture self-contained and JSON-serializable.

To extend the fixture when the template starts consuming a new variable, add it
to ``base_variables()`` (and, if it is only set on some paths, to the relevant
cases below).

Golden format
-------------
The refactor-safety goal is *semantic* identity of the rendered manifest, so
each golden is the parsed YAML normalized to canonical JSON (``yaml.safe_load``
-> ``json.dumps`` with sorted keys). This ignores benign whitespace/key-order
churn and fails only on a real structural change. Goldens live in
``testdata/kubernetes_ray_template/<case>.json``.

The goldens pin the manifest's structural and scheduling-relevant fields
(node selectors, affinities, tolerations, resource requests/limits, volumes,
sidecars, annotations, ...). The large generated shell blobs -- the container
``command``/``args`` and the Ray ``setup_commands`` -- are *not* pinned: they
are multi-kilobyte bash scripts that churn with unrelated constant changes and
would make golden diffs unreviewable. ``_OMITTED_FIELDS`` lists them; each is
replaced by a fixed sentinel during normalization so the field's *presence*
(and its place in the structure) is still pinned, but its content is not.

To update the goldens after an intentional template change, run this file with
``UPDATE_SNAPSHOT=1`` set, e.g.::

    UPDATE_SNAPSHOT=1 pytest <path to this test file>

Review the resulting diff before committing.
"""
import copy
import difflib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict

import pytest
import yaml

from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import common_utils

TEMPLATE_NAME = 'kubernetes-ray.yml.j2'
TESTDATA_DIR = (Path(__file__).parent / 'testdata' / 'kubernetes_ray_template')


def base_variables() -> Dict[str, Any]:
    """Variables for a minimal CPU-only, single-node Kubernetes launch.

    See the module docstring for how the key set is derived and how to extend
    it. Conditionally-added keys that production only sets on some paths (the
    ``is defined`` guards: ``k8s_ephemeral_storage``, ``k8s_cpu_limit``,
    ``k8s_memory_limit``, ``k8s_ephemeral_storage_limit``,
    ``preemption_hook_timeout``) are intentionally absent from the base so those
    template branches stay off unless a case adds them.
    """
    return {
        # --- Kubernetes.make_deploy_resources_variables ---
        'instance_type': '4CPU--16GB',
        'custom_resources': None,
        'cpus': '4',
        'memory': '16',
        'accelerator_count': '0',
        'timeout': '600',
        'k8s_efa_count': None,
        'k8s_efa_same_az': False,
        'k8s_port_mode': 'portforward',
        'k8s_acc_label_key': None,
        'k8s_acc_label_values': None,
        'k8s_service_account_name': 'skypilot-service-account',
        'k8s_automount_sa_token': 'true',
        'k8s_fuse_device_required': False,
        'k8s_kueue_local_queue_name': None,
        'k8s_skypilot_system_namespace': 'skypilot-system',
        'k8s_fusermount_shared_dir': '/var/run/fusermount',
        'k8s_fusermount_setup_command': 'FUSERMOUNT_SETUP_COMMAND',
        'k8s_spot_label_key': None,
        'k8s_spot_label_value': None,
        'tpu_requested': False,
        'k8s_topology_label_key': None,
        'k8s_topology_label_value': None,
        'k8s_resource_key': None,
        'k8s_env_vars': {
            'SKYPILOT_IN_CLUSTER_CONTEXT_NAME': 'my-k8s-context',
            'NVIDIA_VISIBLE_DEVICES': 'none',
        },
        'image_id': 'us-docker.pkg.dev/skypilot-oss/skypilot/skypilot:latest',
        'ray_installation_commands': 'RAY_INSTALLATION_COMMANDS',
        'ray_head_start_command': 'RAY_HEAD_START_COMMAND',
        'skypilot_ray_port': 6380,
        'ray_worker_start_command': 'RAY_WORKER_START_COMMAND',
        'k8s_high_availability_deployment_volume_mount_name': 'sky-persistent',
        'k8s_high_availability_deployment_volume_mount_path': '/sky-persistent',
        'k8s_high_availability_deployment_setup_script_path': '/sky-setup.sh',
        'k8s_high_availability_deployment_run_script_dir': '/sky-run',
        'k8s_high_availability_restarting_signal_file': '/sky-restart',
        'ha_recovery_log_path': '/sky-recovery.log',
        'sky_python_cmd': 'python3',
        'sky_unset_pythonpath_and_set_cwd': 'unset PYTHONPATH && cd ~',
        'k8s_high_availability_storage_class_name': None,
        'avoid_label_keys': None,
        'k8s_enable_flex_start': False,
        'k8s_max_run_duration_seconds': None,
        'k8s_network_type': 'none',
        'k8s_context': 'my-k8s-context',
        'k8s_namespace': 'default',
        'k8s_host_network': False,
        'k8s_enable_gpudirect_tcpx': False,
        'k8s_enable_gpudirect_tcpxo': False,
        'k8s_enable_gpudirect_rdma': False,
        'k8s_enable_gpudirect_rdma_a4': False,
        'k8s_ipc_lock_capability': False,
        'k8s_enable_oci_roce': False,
        'k8s_apt_mirrors': None,
        'k8s_enable_docker_all': False,
        'k8s_enable_docker_build': False,
        'k8s_docker_config_dict': None,
        # --- backend_utils.write_cluster_config ---
        'cluster_name_on_cloud': 'sky-cluster-abc123',
        'num_nodes': 1,
        'disk_size': 256,
        'user': 'testuser',
        'workspace': 'default',
        'original_user': 'testuser',
        'labels': {},
        'conda_installation_commands': 'CONDA_INSTALLATION_COMMANDS',
        'uv_installation_commands': 'UV_INSTALLATION_COMMANDS',
        'skypilot_wheel_installation_commands': 'WHEEL_INSTALLATION_COMMANDS',
        'copy_skypilot_templates_commands': 'COPY_TEMPLATES_COMMANDS',
        'ray_port': 6379,
        'ray_dashboard_port': 8265,
        'credentials': {},
        'sky_remote_path': '/tmp/sky_wheels',
        'sky_local_path': '/tmp/local_wheel.whl',
        'sky_ray_yaml_remote_path': '~/.sky/sky_ray.yml',
        'sky_ray_yaml_local_path': '/tmp/sky_ray.yml',
        'sky_wheel_hash': 'WHEELHASH',
        'ssh_max_sessions_config': 'SSH_MAX_SESSIONS_CONFIG',
        'ssh_private_key': '~/.ssh/sky-key',
        'high_availability': False,
        'volume_mounts': [],
        'ephemeral_volume_mounts': [],
        'volume_mount_rw_paths': [],
        'runcmd': [],
        'priority_class': None,
        # --- resources.py extra_template_variables ---
        'initial_setup_commands': [],
        'k8s_networking_mode': 'portforward',
    }


# GPU node selector labels reused across the accelerator cases.
_GPU_LABEL_KEY = 'skypilot.co/accelerator'
_GPU_ENV_VARS = {'SKYPILOT_IN_CLUSTER_CONTEXT_NAME': 'my-k8s-context'}

# A ReadWriteMany PVC mount and a hostPath mount, shaped like the VolumeInfo
# objects write_cluster_config passes to the template.
_VOLUME_MOUNTS = [
    {
        'name': 'my-pvc-volume',
        'path': '/mnt/pvc',
        'volume_name_on_cloud': 'sky-pvc-abc123',
        'volume_id_on_cloud': None,
        'sub_path': 'subdir',
        'volume_type': 'k8s-pvc',
        'host_path': None,
    },
    {
        'name': 'my-hostpath-volume',
        'path': '/mnt/host',
        'volume_name_on_cloud': None,
        'volume_id_on_cloud': None,
        'sub_path': None,
        'volume_type': 'k8s-hostpath',
        'host_path': '/data/on/host',
    },
]
_EPHEMERAL_VOLUME_MOUNTS = [{
    'name': 'sky-cluster-abc123-ephemeral',
    'path': '/mnt/scratch',
    'volume_type': 'k8s-pvc',
    'size': '100',
}]

# Each case is a set of overrides merged onto base_variables(). Cases are
# one-dimension-at-a-time from the base unless named "everything_*".
CASES: Dict[str, Dict[str, Any]] = {
    'base_cpu': {},
    'gpu_single_label_value': {
        'accelerator_count': '1',
        'k8s_acc_label_key': _GPU_LABEL_KEY,
        'k8s_acc_label_values': ['H100'],
        'k8s_resource_key': 'nvidia.com/gpu',
        'k8s_env_vars': _GPU_ENV_VARS,
    },
    'gpu_multiple_label_values': {
        'accelerator_count': '1',
        'k8s_acc_label_key': _GPU_LABEL_KEY,
        'k8s_acc_label_values': ['A100', 'A100-80GB', 'A100-SXM'],
        'k8s_resource_key': 'nvidia.com/gpu',
        'k8s_env_vars': _GPU_ENV_VARS,
    },
    'tpu': {
        'accelerator_count': '4',
        'tpu_requested': True,
        'k8s_acc_label_key': 'cloud.google.com/gke-tpu-accelerator',
        'k8s_acc_label_values': ['tpu-v5-lite-podslice'],
        'k8s_topology_label_key': 'cloud.google.com/gke-tpu-topology',
        'k8s_topology_label_value': '2x2',
        'k8s_resource_key': 'google.com/tpu',
        'k8s_env_vars': _GPU_ENV_VARS,
    },
    'cpu_avoid_label_keys': {
        'avoid_label_keys': [
            'nvidia.com/gpu.present', 'cloud.google.com/gke-tpu-accelerator'
        ],
    },
    'spot': {
        'k8s_spot_label_key': 'cloud.google.com/gke-spot',
        'k8s_spot_label_value': 'true',
    },
    'ephemeral_storage': {
        'k8s_ephemeral_storage': '256',
    },
    'pod_resource_limits': {
        'k8s_cpu_limit': 4.0,
        'k8s_memory_limit': 16.0,
        'k8s_ephemeral_storage_limit': 256.0,
    },
    'custom_service_account': {
        'k8s_service_account_name': 'my-custom-sa',
        'k8s_automount_sa_token': 'false',
    },
    'user_labels': {
        'labels': {
            'team': 'research',
            'cost-center': 'ml-platform',
            'env': 'staging',
        },
    },
    'fuse_enabled': {
        'k8s_fuse_device_required': True,
    },
    'high_availability': {
        'high_availability': True,
        'k8s_high_availability_storage_class_name': 'standard-rwo',
    },
    'multinode': {
        'num_nodes': 3,
    },
    'kueue': {
        'k8s_kueue_local_queue_name': 'user-queue',
        'k8s_max_run_duration_seconds': 3600,
    },
    'docker_all': {
        'k8s_enable_docker_all': True,
        'k8s_docker_dind_image': 'docker:29.3-dind',
        'k8s_docker_buildkit_image': 'moby/buildkit:v0.28.0-rootless',
        'k8s_docker_config_dict': {
            'mode': 'all',
            'cache_volume': None
        },
    },
    'docker_build': {
        'k8s_enable_docker_build': True,
        'k8s_docker_dind_image': 'docker:29.3-dind',
        'k8s_docker_buildkit_image': 'moby/buildkit:v0.28.0-rootless',
        'k8s_docker_config_dict': {
            'mode': 'build',
            'cache_volume': 'buildkit-cache'
        },
    },
    'gpudirect_tcpx': {
        'accelerator_count': '8',
        'k8s_acc_label_key': _GPU_LABEL_KEY,
        'k8s_acc_label_values': ['H100'],
        'k8s_resource_key': 'nvidia.com/gpu',
        'k8s_network_type': 'gcp_tcpx',
        'k8s_enable_gpudirect_tcpx': True,
        'k8s_ipc_lock_capability': True,
        'k8s_env_vars': _GPU_ENV_VARS,
    },
    'efa_same_az_multinode': {
        'num_nodes': 2,
        'accelerator_count': '8',
        'k8s_acc_label_key': _GPU_LABEL_KEY,
        'k8s_acc_label_values': ['H100'],
        'k8s_resource_key': 'nvidia.com/gpu',
        'k8s_network_type': 'aws_efa',
        'k8s_efa_count': '4',
        'k8s_efa_same_az': True,
        'k8s_env_vars': _GPU_ENV_VARS,
    },
    'host_network': {
        'k8s_host_network': True,
        'k8s_env_vars': {
            'SKYPILOT_IN_CLUSTER_CONTEXT_NAME': 'my-k8s-context',
            'NVIDIA_VISIBLE_DEVICES': 'none',
            'SKYPILOT_HOST_NETWORK': '1',
            'SKYPILOT_RAY_PORTS_CONFIGMAP_NAME': 'sky-cluster-abc123-ray-ports',
            'SKYPILOT_RAY_PORTS_CONFIGMAP_NAMESPACE': 'default',
        },
    },
    'oci_roce': {
        'accelerator_count': '8',
        'k8s_acc_label_key': _GPU_LABEL_KEY,
        'k8s_acc_label_values': ['H100'],
        'k8s_resource_key': 'nvidia.com/gpu',
        'k8s_network_type': 'oci_roce',
        'k8s_enable_oci_roce': True,
        'k8s_ipc_lock_capability': True,
        'k8s_host_network': True,
        'k8s_env_vars': _GPU_ENV_VARS,
    },
    'volume_mounts': {
        'volume_mounts': _VOLUME_MOUNTS,
        'volume_mount_rw_paths': ['/mnt/pvc', '/mnt/host'],
        'ephemeral_volume_mounts': _EPHEMERAL_VOLUME_MOUNTS,
    },
    'flex_start_priority_preemption': {
        'k8s_enable_flex_start': True,
        'priority_class': 'high-priority',
        'preemption_hook_timeout': 120,
    },
    'everything_on_gpu_singlenode': {
        'accelerator_count': '8',
        'k8s_acc_label_key': _GPU_LABEL_KEY,
        'k8s_acc_label_values': ['H100', 'H100-80GB'],
        'k8s_resource_key': 'nvidia.com/gpu',
        'k8s_network_type': 'together',
        'k8s_ipc_lock_capability': True,
        'k8s_env_vars': _GPU_ENV_VARS,
        'k8s_spot_label_key': 'cloud.google.com/gke-spot',
        'k8s_spot_label_value': 'true',
        'k8s_fuse_device_required': True,
        'k8s_kueue_local_queue_name': 'user-queue',
        'k8s_max_run_duration_seconds': 7200,
        'k8s_service_account_name': 'my-custom-sa',
        'priority_class': 'high-priority',
        'preemption_hook_timeout': 120,
        'labels': {
            'team': 'research'
        },
        'k8s_ephemeral_storage': '512',
        'k8s_cpu_limit': 8.0,
        'k8s_memory_limit': 64.0,
        'k8s_ephemeral_storage_limit': 512.0,
        'volume_mounts': _VOLUME_MOUNTS,
        'volume_mount_rw_paths': ['/mnt/pvc'],
        'k8s_enable_docker_all': True,
        'k8s_docker_dind_image': 'docker:29.3-dind',
        'k8s_docker_buildkit_image': 'moby/buildkit:v0.28.0-rootless',
        'k8s_docker_config_dict': {
            'mode': 'all',
            'cache_volume': None
        },
        'initial_setup_commands': ['echo hello', 'echo world'],
        'runcmd': ['echo "runcmd first"'],
    },
    'everything_on_multinode_ha': {
        'num_nodes': 4,
        'accelerator_count': '8',
        'k8s_acc_label_key': _GPU_LABEL_KEY,
        'k8s_acc_label_values': ['H100'],
        'k8s_resource_key': 'nvidia.com/gpu',
        'high_availability': True,
        'k8s_high_availability_storage_class_name': 'standard-rwo',
        'k8s_host_network': True,
        'k8s_network_type': 'gcp_gpudirect_rdma',
        'k8s_enable_gpudirect_rdma': True,
        'k8s_enable_gpudirect_rdma_a4': True,
        'k8s_ipc_lock_capability': True,
        'k8s_env_vars': {
            'SKYPILOT_IN_CLUSTER_CONTEXT_NAME': 'my-k8s-context',
            'SKYPILOT_HOST_NETWORK': '1',
            'SKYPILOT_RAY_PORTS_CONFIGMAP_NAME': 'sky-cluster-abc123-ray-ports',
            'SKYPILOT_RAY_PORTS_CONFIGMAP_NAMESPACE': 'default',
        },
        'k8s_enable_docker_build': True,
        'k8s_docker_dind_image': 'docker:29.3-dind',
        'k8s_docker_buildkit_image': 'moby/buildkit:v0.28.0-rootless',
        'k8s_docker_config_dict': {
            'mode': 'build',
            'cache_volume': 'buildkit-cache'
        },
        'preemption_hook_timeout': 300,
        'k8s_apt_mirrors': ['mirror.example.com', 'mirror2.example.com'],
    },
}


def _build_variables(case_name: str) -> Dict[str, Any]:
    """Merges a case onto the base and derives the computed template vars.

    ``k8s_node_affinity`` is built by calling the same production helper
    (``kubernetes_utils.get_node_affinity``) that
    ``make_deploy_resources_variables`` uses, from the raw accelerator-label
    vars the case carries. Deriving it here rather than hard-coding it is what
    makes the goldens a semantic-identity proof for the Python lift.
    """
    variables = base_variables()
    variables.update(CASES[case_name])
    variables['k8s_node_affinity'] = kubernetes_utils.get_node_affinity(
        variables['k8s_acc_label_key'],
        variables['k8s_acc_label_values'],
        variables['avoid_label_keys'],
    )
    return variables


def _render(variables: Dict[str, Any]) -> str:
    """Render the template through the production fill_template helper."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, 'rendered.yml')
        common_utils.fill_template(TEMPLATE_NAME, variables, output_path)
        with open(output_path, 'r', encoding='utf-8') as f:
            return f.read()


# Fields whose values are large generated shell blobs, not structural or
# scheduling-relevant manifest content. They are multi-kilobyte bash scripts
# (the main container args is ~23 KB) that churn with unrelated constant
# changes and would drown out the meaningful diff. Each is replaced by a
# sentinel during normalization so its presence is still pinned but its content
# is not. This is a deliberate list, not a size heuristic; extend it if a new
# opaque command/script field appears in the manifest.
#   - args, command: every container's entrypoint/args (main pod, DinD /
#     BuildKit sidecars, init containers, the HA deployment).
#   - setup_commands: the Ray autoscaler's top-level setup script.
_OMITTED_FIELDS = frozenset({'args', 'command', 'setup_commands'})
_OMITTED_SENTINEL = '<omitted>'


def _omit_opaque_fields(obj: Any) -> Any:
    """Recursively replace _OMITTED_FIELDS values with the sentinel."""
    if isinstance(obj, dict):
        return {
            k: (_OMITTED_SENTINEL
                if k in _OMITTED_FIELDS else _omit_opaque_fields(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_omit_opaque_fields(v) for v in obj]
    return obj


def _normalize(rendered: str) -> str:
    """Normalize rendered YAML to canonical, deterministic JSON.

    Parsing then re-serializing with sorted keys collapses benign
    whitespace/key-order differences so the golden fails only on a real
    structural change to the manifest. Opaque command/script fields
    (``_OMITTED_FIELDS``) are replaced by a sentinel first.
    """
    parsed = _omit_opaque_fields(yaml.safe_load(rendered))
    return json.dumps(parsed, indent=2, sort_keys=True) + '\n'


def _assert_matches_snapshot(case_name: str, normalized: str) -> None:
    snapshot_path = TESTDATA_DIR / f'{case_name}.json'

    if os.environ.get('UPDATE_SNAPSHOT') == '1':
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(normalized, encoding='utf-8')
        return

    if not snapshot_path.exists():
        pytest.fail(f'Snapshot file not found: {snapshot_path}\n'
                    f'Run with UPDATE_SNAPSHOT=1 to create it.')

    expected = snapshot_path.read_text(encoding='utf-8')
    if normalized != expected:
        diff = difflib.unified_diff(
            expected.splitlines(keepends=True),
            normalized.splitlines(keepends=True),
            fromfile=f'{case_name}.json (expected)',
            tofile=f'{case_name}.json (actual)',
        )
        pytest.fail(
            f'Rendered manifest does not match snapshot: {snapshot_path}\n\n'
            f'Diff:\n{"".join(diff)}\n\n'
            f'Run with UPDATE_SNAPSHOT=1 to update the snapshot.')


@pytest.mark.parametrize('case_name', list(CASES.keys()))
def test_kubernetes_ray_template_snapshot(case_name: str) -> None:
    """Rendered manifest matches the golden for each input permutation."""
    variables = _build_variables(case_name)
    rendered = _render(variables)
    _assert_matches_snapshot(case_name, _normalize(rendered))


@pytest.mark.parametrize('case_name', list(CASES.keys()))
def test_kubernetes_ray_template_render_is_deterministic(
        case_name: str) -> None:
    """Rendering the same variables twice yields byte-identical output.

    Guards against nondeterminism (unordered iteration, time/random values)
    leaking into the goldens.
    """
    variables = _build_variables(case_name)
    first = _normalize(_render(copy.deepcopy(variables)))
    second = _normalize(_render(copy.deepcopy(variables)))
    assert first == second
