"""Tests for CLI utilities.

This module contains tests for the CLI utilities in sky.utils.cli_utils.
"""
import time

import pytest

from sky import backends
from sky.resources import Resources
from sky.utils import status_lib
from sky.utils.cli_utils import status_utils


def test_status_table_format():
    """Test the status table format."""
    # Test AWS case
    mock_resources = Resources(infra='aws/us-east-1',
                               instance_type='m6i.2xlarge')
    mock_handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster',
        cluster_name_on_cloud='test-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=mock_resources)
    mock_record = {
        'name': 'test-cluster',
        'handle': mock_handle,
        'launched_at': int(time.time()) - 3600,  # 1 hour ago
        'status': status_lib.ClusterStatus.UP,
        'autostop': 300,  # 5 minutes
        'to_down': False,
    }

    # Test the infra format
    infra_str = status_utils._get_infra(mock_record)
    assert infra_str == 'AWS (us-east-1)'

    # Test the resources format
    resources_str = status_utils._get_resources(mock_record)
    assert resources_str == '1x(cpus=8, mem=32, m6i.2xlarge, ...)'

    # Test Kubernetes case
    mock_k8s_resources = Resources(infra='k8s/my-ctx', cpus='2+', memory='4+')
    mock_k8s_handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-k8s-cluster',
        cluster_name_on_cloud='test-k8s-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=2,
        launched_resources=mock_k8s_resources)
    mock_k8s_record = {
        'name': 'test-k8s-cluster',
        'handle': mock_k8s_handle,
        'launched_at': int(time.time()) - 3600,  # 1 hour ago
        'status': status_lib.ClusterStatus.UP,
        'autostop': -1,  # No autostop
        'to_down': False,
        'resources_str': '2x (...)',
    }

    # Test K8S infra format
    k8s_infra_str = status_utils._get_infra(mock_k8s_record)
    assert k8s_infra_str == 'Kubernetes (my-ctx)'

    # Test K8S resources format
    k8s_resources_str = status_utils._get_resources(mock_k8s_record)
    assert k8s_resources_str == '2x (...)'

    # For test purposes, override _get_resources to avoid trying to call
    # resources_utils.get_readable_resources_repr on a Resources object
    orig_get_resources = status_utils._get_resources

    def mock_get_resources(cluster_record, truncate=True):
        return cluster_record.get('resources_str', '-')

    status_utils._get_resources = mock_get_resources

    try:
        # Test SSH case
        mock_ssh_handle = backends.CloudVmRayResourceHandle(
            cluster_name='test-ssh-cluster',
            cluster_name_on_cloud='test-ssh-cluster-cloud',
            cluster_yaml=None,
            launched_nodes=1,
            launched_resources=None)
        mock_ssh_record = {
            'name': 'test-ssh-cluster',
            'handle': mock_ssh_handle,
            'launched_at': int(time.time()) - 3600,  # 1 hour ago
            'status': status_lib.ClusterStatus.UP,
            'autostop': -1,  # No autostop
            'to_down': False,
            'resources_str': '1x (...)',
            'infra': 'SSH/my-tobi-box',
        }

        # Test SSH infra format
        ssh_infra_str = status_utils._get_infra(mock_ssh_record)
        assert ssh_infra_str == 'SSH/my-tobi-box'

        # Test SSH resources format
        ssh_resources_str = status_utils._get_resources(mock_ssh_record)
        assert ssh_resources_str == '1x (...)'
    finally:
        # Restore original function
        status_utils._get_resources = orig_get_resources


def test_show_status_table():
    """Test the full status table output."""
    mock_resources = Resources(infra='aws/us-east-1',
                               instance_type='m6i.2xlarge')
    mock_handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster',
        cluster_name_on_cloud='test-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=mock_resources)

    # Test different cluster statuses
    statuses = [
        status_lib.ClusterStatus.UP,
        status_lib.ClusterStatus.INIT,
        status_lib.ClusterStatus.STOPPED,
    ]

    for status in statuses:
        mock_record = {
            'name': 'test-cluster',
            'handle': mock_handle,
            'launched_at': int(time.time()) - 3600,  # 1 hour ago
            'status': status,
            'autostop': 300,  # 5 minutes
            'to_down': False,
            'last_use': 'sky launch test.yaml',
            'user_name': 'test_user',
            'user_hash': 'abc123',
            'head_ip': '1.2.3.4',
            'resources_str': '1x(cpus=8, mem=32, m6i.2xlarge, ...)',
            'resources_str_full': ('1x(cpus=8, mem=32, m6i.2xlarge, '
                                   'disk=50)'),
            'workspace': 'test-workspace',
            'last_event': 'Test event.',
        }

        # Test basic table
        num_pending = status_utils.show_status_table([mock_record],
                                                     show_all=False,
                                                     show_user=False)
        assert num_pending == (1 if status != status_lib.ClusterStatus.STOPPED
                               else 0)

        # Test with user info
        num_pending = status_utils.show_status_table([mock_record],
                                                     show_all=False,
                                                     show_user=True)
        assert num_pending == (1 if status != status_lib.ClusterStatus.STOPPED
                               else 0)

        # Test with show_all
        num_pending = status_utils.show_status_table([mock_record],
                                                     show_all=True,
                                                     show_user=True)
        assert num_pending == (1 if status != status_lib.ClusterStatus.STOPPED
                               else 0)

        # Test with query_clusters
        num_pending = status_utils.show_status_table(
            [mock_record],
            show_all=False,
            show_user=False,
            query_clusters=['test-cluster'])
        assert num_pending == (1 if status != status_lib.ClusterStatus.STOPPED
                               else 0)

        # Test with non-existent query_clusters
        num_pending = status_utils.show_status_table(
            [mock_record],
            show_all=False,
            show_user=False,
            query_clusters=['non-existent'])
        assert num_pending == (1 if status != status_lib.ClusterStatus.STOPPED
                               else 0)


def test_get_command():
    """Test command display in status table."""
    mock_record = {
        'last_use': 'sky launch test.yaml --env FOO=bar',
    }

    # Test normal command
    cmd_str = status_utils._get_command(mock_record)
    assert cmd_str == 'sky launch test.yaml --env...'

    # Test command without truncation
    cmd_str = status_utils._get_command(mock_record, truncate=False)
    assert cmd_str == 'sky launch test.yaml --env FOO=bar'

    # Test short command
    mock_record['last_use'] = 'sky status'
    cmd_str = status_utils._get_command(mock_record)
    assert cmd_str == 'sky status'


def test_get_autostop():
    """Test autostop display in status table."""
    mock_record = {
        'autostop': 300,  # 5 minutes
        'to_down': False,
    }

    # Test normal autostop
    autostop_str = status_utils._get_autostop(mock_record)
    assert autostop_str == '300m'

    # Test autostop with to_down
    mock_record['to_down'] = True
    autostop_str = status_utils._get_autostop(mock_record)
    assert autostop_str == '300m (down)'

    # Test no autostop
    mock_record['autostop'] = -1
    autostop_str = status_utils._get_autostop(mock_record)
    assert autostop_str == '(down)'

    # Test no autostop and no to_down
    mock_record['to_down'] = False
    autostop_str = status_utils._get_autostop(mock_record)
    assert autostop_str == '-'


def test_get_resources():
    """Test resources display in status table."""
    mock_resources = Resources(infra='aws/us-east-1',
                               instance_type='m6i.2xlarge')
    mock_handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster',
        cluster_name_on_cloud='test-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=mock_resources)
    mock_record = {
        'handle': mock_handle,
        'resources_str': '1x(cpus=8, mem=32, m6i.2xlarge, ...)',
        'resources_str_full': '1x(cpus=8, mem=32, m6i.2xlarge, disk=50)',
    }

    # Test normal resources
    resources_str = status_utils._get_resources(mock_record)
    assert resources_str == '1x(cpus=8, mem=32, m6i.2xlarge, ...)'

    # Test full resources
    resources_str = status_utils._get_resources(mock_record, truncate=False)
    assert resources_str == '1x(cpus=8, mem=32, m6i.2xlarge, disk=50)'

    # Test no resources
    mock_record['handle'].launched_resources = None
    resources_str = status_utils._get_resources(mock_record)
    assert resources_str == '-'


def test_get_resources_gpu():
    """Test resources display for clusters with GPUs."""
    # Test AWS with GPU resources
    mock_resources_aws_gpu = Resources(infra='aws/us-east-1',
                                       instance_type='p3.2xlarge',
                                       accelerators='V100')
    mock_handle_aws_gpu = backends.CloudVmRayResourceHandle(
        cluster_name='test-gpu-cluster',
        cluster_name_on_cloud='test-gpu-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=mock_resources_aws_gpu)
    mock_record_aws_gpu = {
        'handle': mock_handle_aws_gpu,
        'resources_str': '1x(V100:1, cpus=8, mem=61, ...)',
        'resources_str_full': '1x(V100:1, cpus=8, mem=61, disk=50)',
    }

    # Test GPU resources
    resources_str = status_utils._get_resources(mock_record_aws_gpu)
    assert resources_str == '1x(V100:1, cpus=8, mem=61, ...)'

    # Test full GPU resources
    resources_str = status_utils._get_resources(mock_record_aws_gpu,
                                                truncate=False)
    assert resources_str == '1x(V100:1, cpus=8, mem=61, disk=50)'

    # Test GCP with multiple GPUs
    mock_resources_gcp_multi_gpu = Resources(infra='gcp/us-central1',
                                             instance_type='a2-highgpu-4g',
                                             accelerators='A100:4')
    mock_handle_gcp_multi_gpu = backends.CloudVmRayResourceHandle(
        cluster_name='test-gcp-multi-gpu',
        cluster_name_on_cloud='test-gcp-multi-gpu-cloud',
        cluster_yaml=None,
        launched_nodes=2,
        launched_resources=mock_resources_gcp_multi_gpu)
    mock_record_gcp_multi_gpu = {
        'handle': mock_handle_gcp_multi_gpu,
        'resources_str': '2x(gpus=A100:4, cpus=12, mem=85, ...)',
        'resources_str_full': '2x(gpus=A100:4, cpus=12, mem=85, disk=50)',
    }

    # Test multiple GPU resources
    resources_str = status_utils._get_resources(mock_record_gcp_multi_gpu)
    assert resources_str == '2x(gpus=A100:4, cpus=12, mem=85, ...)'


def test_get_resources_kubernetes():
    """Test resources display for Kubernetes clusters."""
    # Test Kubernetes with CPU resources
    mock_resources_k8s_cpu = Resources(infra='k8s/my-cluster-ctx',
                                       cpus=4,
                                       memory=16)
    mock_handle_k8s_cpu = backends.CloudVmRayResourceHandle(
        cluster_name='test-k8s-cluster',
        cluster_name_on_cloud='test-k8s-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=mock_resources_k8s_cpu)
    mock_record_k8s_cpu = {
        'handle': mock_handle_k8s_cpu,
        'resources_str': '1x(cpus=4, mem=16, ...)',
        'resources_str_full': '1x(cpus=4, mem=16, disk=50)',
    }

    # Test K8s CPU resources
    resources_str = status_utils._get_resources(mock_record_k8s_cpu)
    assert resources_str == '1x(cpus=4, mem=16, ...)'

    # Test Kubernetes with GPU resources
    mock_resources_k8s_gpu = Resources(infra='k8s/gpu-cluster-ctx',
                                       cpus=8,
                                       memory=32,
                                       accelerators='A100:2')
    mock_handle_k8s_gpu = backends.CloudVmRayResourceHandle(
        cluster_name='test-k8s-gpu-cluster',
        cluster_name_on_cloud='test-k8s-gpu-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=2,
        launched_resources=mock_resources_k8s_gpu)
    mock_record_k8s_gpu = {
        'handle': mock_handle_k8s_gpu,
        'resources_str': '2x(gpus=A100:2, cpus=8, mem=32, ...)',
        'resources_str_full': '2x(gpus=A100:2, cpus=8, mem=32, disk=50)',
    }

    # Test K8s GPU resources
    resources_str = status_utils._get_resources(mock_record_k8s_gpu)
    assert resources_str == '2x(gpus=A100:2, cpus=8, mem=32, ...)'

    # Test full K8s GPU resources
    resources_str = status_utils._get_resources(mock_record_k8s_gpu,
                                                truncate=False)
    assert resources_str == '2x(gpus=A100:2, cpus=8, mem=32, disk=50)'

    # Test K8s with TPU resources
    mock_resources_k8s_tpu = Resources(infra='k8s/gke-tpu-cluster',
                                       cpus=8,
                                       memory=32,
                                       accelerators='tpu-v4-8')
    mock_handle_k8s_tpu = backends.CloudVmRayResourceHandle(
        cluster_name='test-k8s-tpu-cluster',
        cluster_name_on_cloud='test-k8s-tpu-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=mock_resources_k8s_tpu)
    mock_record_k8s_tpu = {
        'handle': mock_handle_k8s_tpu,
        'resources_str': '1x(gpus=tpu-v4-8:1, cpus=8, mem=32, ...)',
        'resources_str_full': ('1x(gpus=tpu-v4-8:1, cpus=8, mem=32, '
                               'disk=50)'),
    }

    # Test K8s TPU resources
    resources_str = status_utils._get_resources(mock_record_k8s_tpu)
    assert resources_str == '1x(gpus=tpu-v4-8:1, cpus=8, mem=32, ...)'
