import time

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
    assert infra_str == 'AWS/us-east-1'

    # Test the resources format
    resources_str = status_utils._get_resources(mock_record)
    assert resources_str == '1x m6i.2xlarge'

    # Test Kubernetes case
    mock_k8s_resources = Resources(infra='k8s/my-ctx')
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
    assert k8s_infra_str == 'K8S/my-ctx'

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
