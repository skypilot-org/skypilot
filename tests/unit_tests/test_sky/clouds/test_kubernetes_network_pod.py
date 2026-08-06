"""Golden-snapshot tests for the high-performance-network pod render.

Renders the Kubernetes pod spec for every high-performance network type via
the real ``make_deploy_resources_variables`` + ``kubernetes-ray.yml.j2`` path
(``_detect_network_type`` mocked to the target type, everything else mocked so
no cluster is needed) and compares the rendered pod YAML against a golden
snapshot. This is a pure render regression guard: it proves the pod YAML the
template emits for each fabric is unchanged, no GPU/RDMA hardware required.

The main container's bootstrap ``command``/``args`` are dropped from the
snapshot (see ``_render_pod``): they are network-invariant, so the snapshot
captures the network-relevant surface -- annotations, env, resources,
securityContext, hostNetwork/dnsPolicy, volumes/mounts, and the fabric
sidecars (with their own command/args).

To update snapshots when the render intentionally changes:
    UPDATE_SNAPSHOT=1 pytest \
        tests/unit_tests/test_sky/clouds/test_kubernetes_network_pod.py
"""
import os
from pathlib import Path
from unittest import mock

import pytest

from sky.clouds import kubernetes as kubernetes_cloud
from sky.provision.kubernetes.utils import KubernetesHighPerformanceNetworkType
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import yaml_utils

TESTDATA_DIR = Path(__file__).parent / 'testdata' / 'k8s_network_pod' / 'legacy'

NT = KubernetesHighPerformanceNetworkType

_GPU8 = ('8CPU--64GB--H100:8', {'H100': 8})
_CPU = ('8CPU--64GB', None)

# Exhaustive fixture matrix: one case per distinct render branch in the
# high-performance-network logic (kubernetes-ray.yml.j2 + the enum). Each
# tuple is (network_type, detection_metadata, (instance_type, accelerators),
# effective_pod_config). If a branch has no case here it is unguarded.
CASES = {
    # Per-fabric device-plugin resource + env + IPC_LOCK.
    'coreweave': (NT.COREWEAVE, None, _GPU8, {}),
    'together': (NT.TOGETHER, None, _GPU8, {}),
    # acc_count==0 branch: with no GPU the resource-limits block is skipped
    # entirely, so no rdma resource is requested (get_rdma_resource_requests
    # returns {}). Guards that the fabric env/IPC_LOCK still render without a
    # GPU while the RDMA resource does not.
    'together_no_gpu': (NT.TOGETHER, None, _CPU, {}),
    'nebius': (NT.NEBIUS, None, _GPU8, {}),
    # AWS EFA: efa_count present -> vpc.amazonaws.com/efa:N; absent -> no efa
    # resource (the k8s_efa_count is none branch).
    'aws_efa': (NT.AWS_EFA, {'efa_count': 4}, _GPU8, {}),
    'aws_efa_no_count': (NT.AWS_EFA, None, _GPU8, {}),
    # GCP GPUDirect variants: distinct annotations/volumes/sidecars/env.
    'gcp_tcpx': (NT.GCP_TCPX, {'instance_type': 'a3-highgpu-8g'}, _GPU8, {}),
    'gcp_tcpxo': (NT.GCP_TCPXO, {'instance_type': 'a3-megagpu-8g'}, _GPU8, {}),
    # a4-vs-a3u tuner-config fork within GPUDirect-RDMA.
    'gcp_gpudirect_rdma': (NT.GCP_GPUDIRECT_RDMA,
                           {'instance_type': 'a3-ultragpu-8g'}, _GPU8, {}),
    'gcp_gpudirect_rdma_a4': (NT.GCP_GPUDIRECT_RDMA,
                              {'instance_type': 'a4-highgpu-8g'}, _GPU8, {}),
    # hostNetwork block via OCI RoCE (implicit) ...
    'oci_roce': (NT.OCI_ROCE, None, _GPU8, {}),
    # ... and via a user-set spec.hostNetwork on a non-high-perf cluster (the
    # other trigger of the k8s_host_network dnsPolicy/anti-affinity block).
    'hostnetwork_pod_config': (NT.NONE, None, _GPU8,
                               {'spec': {
                                   'hostNetwork': True
                               }}),
    'none': (NT.NONE, None, _GPU8, {}),
}


def _render_pod(network_type, metadata, instance, effective_pod_config):
    """Render the pod node_config for a network type, cluster-free."""
    instance_type, accelerators = instance
    resources = mock.MagicMock()
    resources.instance_type = instance_type
    resources.accelerators = accelerators
    resources.use_spot = False
    resources.region = 'test-context'
    resources.zone = None
    resources.cluster_config_overrides = {}
    resources.image_id = None
    resources.network_tier = resources_utils.NetworkTier.BEST
    resources.ephemeral_storage = None
    resources.hooks = None
    setattr(resources, 'assert_launchable', lambda: resources)

    region = mock.MagicMock()
    region.name = 'test-context'

    cfg = {
        ('kubernetes', 'remote_identity'): 'SERVICE_ACCOUNT',
        ('kubernetes', 'provision_timeout'): 10,
        ('kubernetes', 'high_availability', 'storage_class_name'): None,
    }

    patches = [
        mock.patch('sky.provision.kubernetes.utils.get_kubernetes_nodes',
                   return_value=[]),
        mock.patch(
            'sky.provision.kubernetes.utils.'
            'get_current_kube_config_context_name',
            return_value='test-context'),
        mock.patch(
            'sky.provision.kubernetes.utils.get_kube_config_context_namespace',
            return_value='default'),
        mock.patch(
            'sky.provision.kubernetes.utils.get_accelerator_label_keys',
            return_value=[]),
        mock.patch(
            'sky.provision.kubernetes.utils.get_accelerator_label_key_values',
            return_value=('skypilot.co/accelerator', ['h100'], None, None)),
        mock.patch('sky.provision.kubernetes.utils.get_gpu_resource_key',
                   return_value='nvidia.com/gpu'),
        mock.patch('sky.provision.kubernetes.utils.is_kubeconfig_exec_auth',
                   return_value=(False, None)),
        mock.patch(
            'sky.provision.kubernetes.utils.resolve_effective_pod_config',
            return_value=effective_pod_config),
        mock.patch(
            'sky.skypilot_config.get_effective_region_config',
            side_effect=lambda cloud, keys, region=None, default_value=None,
            override_configs=None: cfg.get((cloud,) + keys, default_value)),
        mock.patch('sky.skypilot_config.get_workspace_cloud',
                   return_value=mock.MagicMock(get=lambda *a, **k: None)),
        mock.patch('sky.provision.kubernetes.network_utils.get_port_mode',
                   return_value=mock.MagicMock(value='portforward')),
        mock.patch('sky.catalog.get_image_id_from_tag',
                   return_value='test-image:latest'),
        mock.patch(
            'sky.clouds.kubernetes.Kubernetes._detect_network_type',
            return_value=(network_type, metadata)),
    ]
    for patch in patches:
        patch.start()
    try:
        cloud = kubernetes_cloud.Kubernetes()
        deploy_vars = cloud.make_deploy_resources_variables(
            resources=resources,
            cluster_name=resources_utils.ClusterName(display_name='c',
                                                     name_on_cloud='c'),
            region=region,
            zones=None,
            num_nodes=2,
            dryrun=False)
    finally:
        for patch in patches:
            patch.stop()

    # Provisioning-layer scaffolding the template needs beyond deploy_vars.
    # Held constant across before/after so any diff is a network-render diff.
    deploy_vars.setdefault('cluster_name_on_cloud', 'c')
    deploy_vars.setdefault('num_nodes', 2)
    deploy_vars.setdefault('disk_size', 100)
    for key, value in dict(labels={},
                           credentials={},
                           volume_mounts=[],
                           volume_mount_rw_paths=[],
                           ephemeral_volume_mounts=[]).items():
        deploy_vars.setdefault(key, value)

    import tempfile
    out = tempfile.mktemp(suffix='.yaml')
    common_utils.fill_template('kubernetes-ray.yml.j2',
                               deploy_vars,
                               output_path=out)
    obj = yaml_utils.safe_load(open(out, encoding='utf-8').read())
    pod = obj['available_node_types']['ray_head_default']['node_config']

    # Drop the main container's command/args from the snapshot. That bootstrap
    # script is network-invariant (the same for every fabric), it is ~85% of
    # the rendered bytes, and it embeds a gzip blob whose header would otherwise
    # need mtime pinning to stay deterministic. Everything network-relevant --
    # the fabric-specific sidecars (with their own command/args), the env list,
    # resources, securityContext, annotations, hostNetwork/dnsPolicy, and the
    # volumes/volumeMounts -- is retained, so the render is still fully guarded.
    for container in pod.get('spec', {}).get('containers', []):
        if container.get('name') == 'ray-node':
            container.pop('command', None)
            container.pop('args', None)
    return yaml_utils.dump_yaml_str(pod)


def _assert_matches_snapshot(name, rendered):
    path = TESTDATA_DIR / f'{name}.yaml'
    if os.environ.get('UPDATE_SNAPSHOT') == '1':
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered)
        return
    if not path.exists():
        pytest.fail(f'Snapshot not found: {path}\n'
                    f'Run with UPDATE_SNAPSHOT=1 to create it.')
    expected = path.read_text()
    if rendered != expected:
        import difflib
        diff = ''.join(
            difflib.unified_diff(expected.splitlines(keepends=True),
                                 rendered.splitlines(keepends=True),
                                 fromfile=f'{name}.yaml (golden)',
                                 tofile=f'{name}.yaml (rendered)'))
        pytest.fail(f'Pod render does not match snapshot {path}:\n\n{diff}\n'
                    f'Run with UPDATE_SNAPSHOT=1 to update.')


@pytest.mark.parametrize('name', list(CASES))
def test_network_pod_render(name):
    network_type, metadata, instance, effective_pod_config = CASES[name]
    rendered = _render_pod(network_type, metadata, instance,
                           effective_pod_config)
    _assert_matches_snapshot(name, rendered)
