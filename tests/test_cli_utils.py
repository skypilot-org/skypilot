import time

import colorama
import pytest

import sky
from sky import backends
from sky.resources import Resources
from sky.utils import status_lib
from sky.utils.cli_utils import status_utils


def test_status_table_format():
    """Test the status table format."""
    # Test AWS case
    mock_resources = Resources(cloud=sky.CLOUD_REGISTRY.from_str('aws'),
                               instance_type='m6i.2xlarge',
                               region='us-east-1')
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
    assert resources_str == '1x(vCPUs=8, mem=32, type=m6i.2xlarge, ...)'

    # Test Kubernetes case
    mock_k8s_resources = Resources(cloud=sky.Kubernetes(),
                                   cpus='2+',
                                   memory='4+')
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
    }

    # Test K8S infra format
    k8s_infra_str = status_utils._get_infra(mock_k8s_record)
    assert k8s_infra_str == 'Kubernetes (my-ctx)'

    # Test K8S resources format
    k8s_resources_str = status_utils._get_resources(mock_k8s_record)
    assert k8s_resources_str == '2x (...)'

    # Test SSH case
    mock_ssh_resources = Resources(infra='ssh/my-tobi-box')
    mock_ssh_handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-ssh-cluster',
        cluster_name_on_cloud='test-ssh-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=mock_ssh_resources)
    mock_ssh_record = {
        'name': 'test-ssh-cluster',
        'handle': mock_ssh_handle,
        'launched_at': int(time.time()) - 3600,  # 1 hour ago
        'status': status_lib.ClusterStatus.UP,
        'autostop': -1,  # No autostop
        'to_down': False,
    }

    # Test SSH infra format
    ssh_infra_str = status_utils._get_infra(mock_ssh_record)
    assert ssh_infra_str == 'SSH/my-tobi-box'

    # Test SSH resources format
    ssh_resources_str = status_utils._get_resources(mock_ssh_record)
    assert ssh_resources_str == '1x (...)'


def test_show_status_table():
    """Test the full status table output."""
    mock_resources = Resources(cloud=sky.CLOUD_REGISTRY.from_str('aws'),
                               instance_type='m6i.2xlarge',
                               region='us-east-1')
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
            'resources_str': '1x(vCPUs=8, mem=32, type=m6i.2xlarge, ...)',
            'resources_str_full': '1x(vCPUs=8, mem=32, type=m6i.2xlarge, disk=50)',
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
    mock_resources = Resources(cloud=sky.CLOUD_REGISTRY.from_str('aws'),
                               instance_type='m6i.2xlarge',
                               region='us-east-1')
    mock_handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster',
        cluster_name_on_cloud='test-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=mock_resources)
    mock_record = {
        'handle': mock_handle,
        'resources_str': '1x(vCPUs=8, mem=32, type=m6i.2xlarge, ...)',
        'resources_str_full': '1x(vCPUs=8, mem=32, type=m6i.2xlarge, disk=50)',
    }

    # Test normal resources
    resources_str = status_utils._get_resources(mock_record)
    assert resources_str == '1x(vCPUs=8, mem=32, type=m6i.2xlarge, ...)'

    # Test full resources
    resources_str = status_utils._get_resources(mock_record, truncate=False)
    assert resources_str == '1x(vCPUs=8, mem=32, type=m6i.2xlarge, disk=50)'

    # Test no resources
    mock_record['handle'].launched_resources = None
    resources_str = status_utils._get_resources(mock_record)
    assert resources_str == '-'
