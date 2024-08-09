"""Test exception serialization and deserialization."""

from sky import exceptions
from sky.utils import status_lib


def _serialize_deserialize(e: Exception) -> Exception:
    serialized = exceptions.serialize_exception(e)
    return exceptions.deserialize_exception(serialized)


def test_value_error():
    """Test that exceptions can be serialized and deserialized."""
    e = ValueError('test')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, ValueError)
    assert str(deserialized) == 'test'


def test_resources_unavailable_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.ResourcesUnavailableError('test', failover_history=[ValueError('test1'), exceptions.ResourcesUnavailableError('test2')])
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.ResourcesUnavailableError)
    assert str(deserialized) == 'test'
    assert str(deserialized.failover_history[0]) == 'test1'
    assert str(deserialized.failover_history[1]) == 'test2'


def test_invalid_cloud_configs():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.InvalidCloudConfigs('test')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.InvalidCloudConfigs)
    assert str(deserialized) == 'test'


def test_provision_prechecks_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.ProvisionPrechecksError(reasons=[ValueError('test1'), exceptions.ResourcesUnavailableError('test2')])
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.ProvisionPrechecksError)
    assert str(deserialized) == ''
    assert str(deserialized.reasons[0]) == 'test1'
    assert str(deserialized.reasons[1]) == 'test2'



def test_command_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.CommandError(1, 'test_command', 'test_error_msg', 'test_detailed_reason')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.CommandError)
    assert str(deserialized).startswith('Command test_command failed with return code 1.')
    assert deserialized.returncode == 1
    assert deserialized.command == 'test_command'
    assert deserialized.error_msg == 'test_error_msg'
    assert deserialized.detailed_reason == 'test_detailed_reason'


def test_cluster_not_up_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.ClusterNotUpError('test', cluster_status=status_lib.ClusterStatus.UP)
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.ClusterNotUpError)
    assert str(deserialized) == 'test'
    assert deserialized.cluster_status == status_lib.ClusterStatus.UP
    assert deserialized.handle is None


def test_fetch_cluster_info_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.FetchClusterInfoError(exceptions.FetchClusterInfoError.Reason.HEAD)
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.FetchClusterInfoError)
    assert str(deserialized) == ''
    assert deserialized.reason == exceptions.FetchClusterInfoError.Reason.HEAD


def test_aws_az_fetching_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.AWSAzFetchingError(region='us-east-1', reason=exceptions.AWSAzFetchingError.Reason.AUTH_FAILURE)
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.AWSAzFetchingError)
    assert str(deserialized).startswith('Failed to access AWS services. Please check your AWS credentials.')
    assert deserialized.region == 'us-east-1'
    assert deserialized.reason == exceptions.AWSAzFetchingError.Reason.AUTH_FAILURE
