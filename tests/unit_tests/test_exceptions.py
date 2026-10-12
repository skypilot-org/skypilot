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
    e = exceptions.ResourcesUnavailableError(
        'test',
        failover_history=[
            ValueError('test1'),
            exceptions.ResourcesUnavailableError('test2')
        ])
    setattr(e, 'stacktrace', 'test_stacktrace')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.ResourcesUnavailableError)
    assert str(deserialized) == 'test'
    assert str(deserialized.failover_history[0]) == 'test1'
    assert str(deserialized.failover_history[1]) == 'test2'
    assert deserialized.stacktrace == 'test_stacktrace'


def test_provision_unsupported_error_is_not_a_capacity_failure():
    """An unsupportable request must not read as unavailable resources.

    Callers that decide whether to keep waiting for capacity test the
    failover history for ResourcesUnavailableError -- see
    sky/jobs/recovery_strategy.py, which fails a job directly when none of
    the failures were capacity failures, rather than retrying forever.
    Making ProvisionUnsupportedError a subclass of ResourcesUnavailableError
    would silently turn every such failure back into "wait for room", so the
    separation is load-bearing rather than stylistic.
    """
    assert not issubclass(exceptions.ProvisionUnsupportedError,
                          exceptions.ResourcesUnavailableError)

    def has_capacity_failure(history):
        return any(
            isinstance(err, exceptions.ResourcesUnavailableError)
            for err in history)

    assert not has_capacity_failure(
        [exceptions.ProvisionUnsupportedError('no way to render this')])
    # Mixed history: something might still free up, so the caller should keep
    # its retry behaviour.
    assert has_capacity_failure([
        exceptions.ProvisionUnsupportedError('no way to render this'),
        exceptions.ResourcesUnavailableError('out of room'),
    ])


def test_provision_unsupported_error_wrapped_for_an_existing_cluster():
    """The failover tail is only reachable for a new or INIT cluster.

    An UP/STOPPED cluster never falls through to it -- _yield_zones marks
    those no_failover, and the tail asserts as much -- so a provisioning
    failure there has to be wrapped and raised instead of continuing. The
    wrapper is a ResourcesUnavailableError because that is what callers
    catch; what tells them this was not a capacity failure is the history it
    carries, which holds no ResourcesUnavailableError.
    """
    original = exceptions.ProvisionUnsupportedError('no way to render this')
    wrapped = exceptions.ResourcesUnavailableError('no way to render this',
                                                   no_failover=True,
                                                   failover_history=[original])

    assert wrapped.no_failover
    assert not any(
        isinstance(err, exceptions.ResourcesUnavailableError)
        for err in wrapped.failover_history)


def test_provision_unsupported_error_round_trips():
    """It crosses the client/server boundary like any other launch error."""
    e = exceptions.ProvisionUnsupportedError('no way to render this')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.ProvisionUnsupportedError)
    assert str(deserialized) == 'no way to render this'


def test_invalid_cloud_configs():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.InvalidCloudConfigs('test')
    setattr(e, 'stacktrace', 'test_stacktrace')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.InvalidCloudConfigs)
    assert str(deserialized) == 'test'
    assert deserialized.stacktrace == 'test_stacktrace'


def test_provision_prechecks_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.ProvisionPrechecksError(reasons=[
        ValueError('test1'),
        exceptions.ResourcesUnavailableError('test2')
    ])
    setattr(e, 'stacktrace', 'test_stacktrace')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.ProvisionPrechecksError)
    assert str(deserialized) == ''
    assert str(deserialized.reasons[0]) == 'test1'
    assert str(deserialized.reasons[1]) == 'test2'
    assert deserialized.stacktrace == 'test_stacktrace'


def test_command_failure_exception():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.CommandFailureException('test_command', 'test_failure',
                                           'test_error_msg',
                                           'test_detailed_reason')
    setattr(e, 'stacktrace', 'test_stacktrace')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.CommandFailureException)
    assert str(deserialized).startswith('Command test_command test_failure.')
    assert deserialized.command == 'test_command'
    assert deserialized.failure == 'test_failure'
    assert deserialized.error_msg == 'test_error_msg'
    assert deserialized.detailed_reason == 'test_detailed_reason'
    assert deserialized.stacktrace == 'test_stacktrace'


def test_command_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.CommandError(1, 'test_command', 'test_error_msg',
                                'test_detailed_reason')
    setattr(e, 'stacktrace', 'test_stacktrace')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.CommandError)
    assert str(deserialized).startswith(
        'Command test_command failed with return code 1.')
    assert deserialized.returncode == 1
    assert deserialized.command == 'test_command'
    assert deserialized.error_msg == 'test_error_msg'
    assert deserialized.detailed_reason == 'test_detailed_reason'
    assert deserialized.stacktrace == 'test_stacktrace'


def test_cluster_not_up_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.ClusterNotUpError('test',
                                     cluster_status=status_lib.ClusterStatus.UP)
    setattr(e, 'stacktrace', 'test_stacktrace')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.ClusterNotUpError)
    assert str(deserialized) == 'test'
    assert deserialized.cluster_status == status_lib.ClusterStatus.UP
    assert deserialized.handle is None
    assert deserialized.stacktrace == 'test_stacktrace'


def test_fetch_cluster_info_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.FetchClusterInfoError(
        exceptions.FetchClusterInfoError.Reason.HEAD)
    setattr(e, 'stacktrace', 'test_stacktrace')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.FetchClusterInfoError)
    assert str(deserialized) == ''
    assert deserialized.reason == exceptions.FetchClusterInfoError.Reason.HEAD
    assert deserialized.stacktrace == 'test_stacktrace'


def test_aws_az_fetching_error():
    """Test that exceptions can be serialized and deserialized."""
    e = exceptions.AWSAzFetchingError(
        region='us-east-1',
        reason=exceptions.AWSAzFetchingError.Reason.AUTH_FAILURE)
    setattr(e, 'stacktrace', 'test_stacktrace')
    deserialized = _serialize_deserialize(e)
    assert isinstance(deserialized, exceptions.AWSAzFetchingError)
    assert str(deserialized).startswith(
        'Failed to access AWS services. Please check your AWS credentials.')
    assert deserialized.region == 'us-east-1'
    assert deserialized.reason == exceptions.AWSAzFetchingError.Reason.AUTH_FAILURE
    assert deserialized.stacktrace == 'test_stacktrace'


def test_deserialize_none_input():
    """Test that None input returns RuntimeError instead of crashing."""
    e = exceptions.deserialize_exception(None)
    assert isinstance(e, RuntimeError)
    assert 'Unknown server error' in str(e)


def test_deserialize_string_input():
    """Test that string input is wrapped in RuntimeError."""
    e = exceptions.deserialize_exception('Something went wrong')
    assert isinstance(e, RuntimeError)
    assert str(e) == 'Something went wrong'

    # Empty string
    e = exceptions.deserialize_exception('')
    assert isinstance(e, RuntimeError)
    assert str(e) == ''


def test_deserialize_non_dict_input():
    """Test that non-dict inputs (list, int, bool) return RuntimeError."""
    for bad_input in [42, True, [{'loc': ['body'], 'msg': 'invalid'}]]:
        e = exceptions.deserialize_exception(bad_input)
        assert isinstance(e, RuntimeError)
        assert 'Server error' in str(e)


def test_deserialize_partial_dict():
    """Test that dicts with 'type' but missing other keys still work."""
    # Dict with only 'type' - should construct with no args
    e = exceptions.deserialize_exception({'type': 'ValueError'})
    assert isinstance(e, ValueError)

    # Dict with 'type' and 'message' but missing others
    e = exceptions.deserialize_exception({
        'type': 'ValueError',
        'message': 'test'
    })
    assert isinstance(e, ValueError)

    # Empty dict - no 'type' key, falls through to RuntimeError
    e = exceptions.deserialize_exception({})
    assert isinstance(e, RuntimeError)

    # Unknown type with message uses message in fallback
    e = exceptions.deserialize_exception({
        'type': 'NonExistent',
        'message': 'details'
    })
    assert isinstance(e, Exception)
    assert 'NonExistent' in str(e)
    assert 'details' in str(e)

    # Unknown type without message still works
    e = exceptions.deserialize_exception({'type': 'NonExistent'})
    assert isinstance(e, Exception)
    assert 'NonExistent' in str(e)


def test_wrap_unsafe_exceptions():
    """Test that non-safe exceptions are wrapped properly."""

    # Mock a cloud exception
    class MockBotoError(Exception):
        pass

    MockBotoError.__module__ = 'botocore.exceptions'

    # Create mock cloud exception
    boto_error = MockBotoError('Failed to launch instance')

    # Serialize and deserialize the exception
    wrapped = _serialize_deserialize(boto_error)

    # Verify it was converted to CloudError
    assert isinstance(wrapped, exceptions.CloudError)
    assert wrapped.cloud_provider == 'botocore'
    assert wrapped.error_type == 'MockBotoError'
    assert str(
        wrapped) == 'botocore error (MockBotoError): Failed to launch instance'

    # Verify safe exceptions pass through unchanged
    value_error = ValueError('Invalid value')
    safe_error = _serialize_deserialize(value_error)
    assert isinstance(safe_error, ValueError)
    assert str(safe_error) == 'Invalid value'

    # Verify SkyPilot exceptions pass through unchanged
    sky_error = exceptions.ClusterNotUpError('test cluster')
    sky_safe = _serialize_deserialize(sky_error)
    assert isinstance(sky_safe, exceptions.ClusterNotUpError)
    assert str(sky_safe) == 'test cluster'
