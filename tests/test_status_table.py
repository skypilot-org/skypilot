"""Unit tests for status table functionality."""
import time

import colorama
import pytest

import sky
from sky import backends
from sky.resources import Resources
from sky.utils import status_lib
from sky.utils.cli_utils import status_utils


@pytest.fixture
def mock_aws_resources():
    """Create mock AWS resources."""
    return Resources(cloud=sky.CLOUD_REGISTRY.from_str('aws'),
                    instance_type='m6i.2xlarge',
                    region='us-east-1')


@pytest.fixture
def mock_k8s_resources():
    """Create mock Kubernetes resources."""
    return Resources(cloud=sky.Kubernetes(),
                    cpus='2+',
                    memory='4+')


@pytest.fixture
def mock_ssh_resources():
    """Create mock SSH resources."""
    return Resources(infra='ssh/my-tobi-box')


@pytest.fixture
def mock_cluster_record(mock_aws_resources):
    """Create a mock cluster record."""
    mock_handle = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster',
        cluster_name_on_cloud='test-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=mock_aws_resources)
    return {
        'name': 'test-cluster',
        'handle': mock_handle,
        'launched_at': int(time.time()) - 3600,  # 1 hour ago
        'status': status_lib.ClusterStatus.UP,
        'autostop': 300,  # 5 minutes
        'to_down': False,
        'last_use': 'sky launch test.yaml',
        'user_name': 'test_user',
        'user_hash': 'abc123',
        'head_ip': '1.2.3.4',
        'resources_str': '1x(vCPUs=8, mem=32, type=m6i.2xlarge, ...)',
        'resources_str_full': '1x(vCPUs=8, mem=32, type=m6i.2xlarge, disk=50)',
    }


def test_empty_table():
    """Test empty status table."""
    num_pending = status_utils.show_status_table([], show_all=False, show_user=False)
    assert num_pending == 0


def test_multiple_clusters(mock_cluster_record):
    """Test status table with multiple clusters."""
    records = []
    for i in range(3):
        record = mock_cluster_record.copy()
        record['name'] = f'test-cluster-{i}'
        records.append(record)

    num_pending = status_utils.show_status_table(records, show_all=False, show_user=False)
    assert num_pending == 3  # All clusters are UP


def test_long_cluster_names(mock_cluster_record):
    """Test status table with long cluster names."""
    record = mock_cluster_record.copy()
    record['name'] = 'very-long-cluster-name-that-should-be-truncated' * 3
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=False)
    assert num_pending == 1


def test_special_characters(mock_cluster_record):
    """Test status table with special characters in names."""
    record = mock_cluster_record.copy()
    record['name'] = 'test!@#$%^&*()_+-=[]{}|;:,.<>?'
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=False)
    assert num_pending == 1


def test_status_colors():
    """Test status colors in table."""
    for status in [
        status_lib.ClusterStatus.INIT,
        status_lib.ClusterStatus.UP,
        status_lib.ClusterStatus.STOPPED,
    ]:
        expected_color = status_lib._STATUS_TO_COLOR[status]
        colored_str = status.colored_str()
        assert expected_color in colored_str
        assert status.value in colored_str
        assert colorama.Style.RESET_ALL in colored_str


def test_invalid_status(mock_cluster_record):
    """Test status table with invalid status."""
    record = mock_cluster_record.copy()
    # Create an invalid status using the enum
    record['status'] = None
    with pytest.raises(AttributeError):
        status_utils.show_status_table([record], show_all=False, show_user=False)


def test_invalid_resources(mock_cluster_record):
    """Test status table with invalid resources."""
    record = mock_cluster_record.copy()
    # Create a new handle without resources
    record['handle'] = backends.CloudVmRayResourceHandle(
        cluster_name='test-cluster',
        cluster_name_on_cloud='test-cluster-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=None)
    record['resources_str'] = None
    record['resources_str_full'] = None
    record['infra'] = 'AWS (us-east-1)'  # Set infra directly for display
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=False)
    assert num_pending == 1


def test_table_sorting(mock_cluster_record):
    """Test status table sorting."""
    records = []
    for status in [
        status_lib.ClusterStatus.STOPPED,
        status_lib.ClusterStatus.UP,
        status_lib.ClusterStatus.INIT,
    ]:
        record = mock_cluster_record.copy()
        record['name'] = f'test-cluster-{status.value}'
        record['status'] = status
        records.append(record)

    num_pending = status_utils.show_status_table(records, show_all=False, show_user=False)
    assert num_pending == 2  # INIT and UP are pending


def test_table_column_alignment(mock_cluster_record):
    """Test status table column alignment with varying content lengths."""
    records = []
    for i in range(3):
        record = mock_cluster_record.copy()
        record['name'] = 'a' * (i + 1)  # Varying name lengths
        record['resources_str'] = 'r' * (i + 1)  # Varying resource lengths
        records.append(record)

    num_pending = status_utils.show_status_table(records, show_all=False, show_user=False)
    assert num_pending == 3


def test_user_info_display(mock_cluster_record):
    """Test user information display in status table."""
    record = mock_cluster_record.copy()
    
    # Test with user info
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=True)
    assert num_pending == 1

    # Test with missing user info
    record.pop('user_name')
    record.pop('user_hash')
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=True)
    assert num_pending == 1


def test_autostop_display(mock_cluster_record):
    """Test autostop display in status table."""
    record = mock_cluster_record.copy()
    
    # Test normal autostop
    assert record['autostop'] == 300
    record['status'] = status_lib.ClusterStatus.STOPPED  # Not pending when STOPPED
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=False)
    assert num_pending == 0

    # Test no autostop
    record['autostop'] = -1
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=False)
    assert num_pending == 0

    # Test autostop with to_down
    record['autostop'] = 300
    record['to_down'] = True
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=False)
    assert num_pending == 0


def test_show_all_columns(mock_cluster_record):
    """Test display of all columns in status table."""
    record = mock_cluster_record.copy()
    
    # Test with show_all=True
    record['last_use'] = 'sky launch test.yaml'  # Add last_use field
    record['head_ip'] = '1.2.3.4'  # Add head_ip field
    record['user_name'] = 'test_user'  # Add user_name field
    record['user_hash'] = 'abc123'  # Add user_hash field
    num_pending = status_utils.show_status_table([record], show_all=True, show_user=True)
    assert num_pending == 1

    # Test with missing optional fields
    record.pop('head_ip', None)  # Use pop with default to avoid KeyError
    record.pop('last_use', None)
    num_pending = status_utils.show_status_table([record], show_all=True, show_user=True)
    assert num_pending == 1


def test_query_clusters(mock_cluster_record):
    """Test querying specific clusters in status table."""
    records = []
    for i in range(3):
        record = mock_cluster_record.copy()
        record['name'] = f'test-cluster-{i}'
        records.append(record)

    # Test querying existing cluster
    num_pending = status_utils.show_status_table(
        records,
        show_all=False,
        show_user=False,
        query_clusters=['test-cluster-1'])
    assert num_pending == 3

    # Test querying non-existent cluster
    num_pending = status_utils.show_status_table(
        records,
        show_all=False,
        show_user=False,
        query_clusters=['non-existent-cluster'])
    assert num_pending == 3


def test_resource_string_formats(mock_cluster_record):
    """Test different resource string formats in status table."""
    record = mock_cluster_record.copy()
    
    # Test normal format
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=False)
    assert num_pending == 1

    # Test empty resources
    record['resources_str'] = ''
    record['resources_str_full'] = ''
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=False)
    assert num_pending == 1

    # Test very long resources
    record['resources_str'] = 'x' * 100
    record['resources_str_full'] = 'x' * 200
    num_pending = status_utils.show_status_table([record], show_all=False, show_user=False)
    assert num_pending == 1 
