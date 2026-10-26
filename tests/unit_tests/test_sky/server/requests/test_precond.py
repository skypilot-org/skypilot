"""Unit tests for sky.server.requests.precond module."""
import json
import unittest
from unittest import mock

import pytest

from sky import exceptions
from sky.server.requests import preconditions
from sky.server.requests import requests as api_requests
from sky.utils import status_lib


class TestClusterStartCompletePrecondition(unittest.TestCase):
    """Unit tests for ClusterStartCompletePrecondition class."""

    def setUp(self):
        """Set up test fixtures."""
        self.request_id = 'test-request'
        self.cluster_name = 'test-cluster'

    @mock.patch('sky.global_user_state.get_cluster_from_name')
    @mock.patch('sky.server.requests.requests.get_request_tasks')
    async def test_cluster_up(self, mock_get_tasks, mock_get_cluster):
        """Test when cluster is UP."""
        mock_get_cluster.return_value = {'status': status_lib.ClusterStatus.UP}
        mock_get_tasks.return_value = []

        p = preconditions.ClusterStartCompletePrecondition(
            self.request_id, self.cluster_name)
        met, msg = await p.check()

        self.assertTrue(met)
        self.assertIsNone(msg)
        # Should not check tasks when cluster is UP
        mock_get_tasks.assert_not_called()

    @mock.patch('sky.global_user_state.get_cluster_from_name')
    @mock.patch('sky.server.requests.requests.get_request_tasks')
    async def test_cluster_not_found(self, mock_get_tasks, mock_get_cluster):
        """Test when cluster is not found and no tasks are running."""
        mock_get_cluster.return_value = None
        mock_get_tasks.return_value = []

        p = preconditions.ClusterStartCompletePrecondition(
            self.request_id, self.cluster_name)
        met, msg = await p.check()

        self.assertTrue(met)
        self.assertIsNone(msg)

    @mock.patch('sky.global_user_state.get_cluster_from_name')
    @mock.patch('sky.server.requests.requests.get_request_tasks')
    async def test_cluster_starting(self, mock_get_tasks, mock_get_cluster):
        """Test when cluster is being started and there are tasks running."""
        mock_get_cluster.return_value = {
            'status': status_lib.ClusterStatus.INIT
        }
        mock_get_tasks.return_value = [mock.MagicMock()]

        p = preconditions.ClusterStartCompletePrecondition(
            self.request_id, self.cluster_name)
        met, msg = await p.check()

        self.assertFalse(met)
        self.assertIn('Waiting for cluster', msg)

    @mock.patch('sky.global_user_state.get_cluster_from_name')
    @mock.patch('sky.server.requests.requests.get_request_tasks')
    async def test_cluster_not_found_but_tasks_running(self, mock_get_tasks,
                                                       mock_get_cluster):
        """Test when cluster is not found but there are tasks running."""
        mock_get_cluster.return_value = None
        mock_get_tasks.return_value = [mock.MagicMock()]

        p = preconditions.ClusterStartCompletePrecondition(
            self.request_id, self.cluster_name)
        met, msg = await p.check()

        self.assertFalse(met)


if __name__ == '__main__':
    unittest.main()


@pytest.mark.asyncio
@mock.patch('sky.server.requests.requests.get_request_async',
            new_callable=mock.AsyncMock)
async def test_request_succeeded_precondition_met(mock_get_request):
    mock_get_request.return_value = mock.MagicMock(
        status=api_requests.RequestStatus.SUCCEEDED)
    p = preconditions.RequestSucceededPrecondition(request_id='follow-up',
                                                   awaited_request_id='awaited')
    met, msg = await p.check()
    assert met
    assert msg is None
    mock_get_request.assert_awaited_once_with('awaited', fields=['status'])


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [
    api_requests.RequestStatus.PENDING,
    api_requests.RequestStatus.WAITING,
    api_requests.RequestStatus.RUNNING,
])
@mock.patch('sky.server.requests.requests.get_request_async',
            new_callable=mock.AsyncMock)
async def test_request_succeeded_precondition_waits(mock_get_request, status):
    mock_get_request.return_value = mock.MagicMock(status=status)
    p = preconditions.RequestSucceededPrecondition(request_id='follow-up',
                                                   awaited_request_id='awaited')
    met, msg = await p.check()
    assert not met
    assert 'awaited' in msg


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [
    api_requests.RequestStatus.FAILED,
    api_requests.RequestStatus.CANCELLED,
])
@mock.patch('sky.server.requests.requests.get_request_async',
            new_callable=mock.AsyncMock)
async def test_request_succeeded_precondition_raises_on_terminal_failure(
        mock_get_request, status):
    mock_get_request.return_value = mock.MagicMock(status=status)
    p = preconditions.RequestSucceededPrecondition(request_id='follow-up',
                                                   awaited_request_id='awaited')
    with pytest.raises(exceptions.RequestCancelled) as exc_info:
        await p.check()
    assert status.value in str(exc_info.value)


@pytest.mark.asyncio
@mock.patch('sky.server.requests.requests.get_request_async',
            new_callable=mock.AsyncMock)
async def test_request_succeeded_precondition_raises_when_missing(
        mock_get_request):
    mock_get_request.return_value = None
    p = preconditions.RequestSucceededPrecondition(request_id='follow-up',
                                                   awaited_request_id='awaited')
    with pytest.raises(exceptions.RequestCancelled):
        await p.check()


@pytest.mark.asyncio
@mock.patch('sky.server.requests.requests.set_request_failed_async',
            new_callable=mock.AsyncMock)
@mock.patch('sky.server.requests.requests.get_request_async',
            new_callable=mock.AsyncMock)
async def test_request_succeeded_precondition_wait_marks_follow_up_failed(
        mock_get_request, mock_set_failed):
    """End to end through _wait: the awaited request FAILED, so the follow-up
    request is marked FAILED and the wait returns False."""

    def _lookup(request_id, fields=None):
        del fields
        if request_id == 'follow-up':
            return mock.MagicMock(status=api_requests.RequestStatus.PENDING)
        return mock.MagicMock(status=api_requests.RequestStatus.FAILED)

    mock_get_request.side_effect = _lookup
    p = preconditions.RequestSucceededPrecondition(request_id='follow-up',
                                                   awaited_request_id='awaited')
    result = await p
    assert result is False
    mock_set_failed.assert_awaited_once()
    failed_id, error = mock_set_failed.await_args.args
    assert failed_id == 'follow-up'
    assert isinstance(error, exceptions.RequestCancelled)


@pytest.mark.parametrize('precondition', [
    preconditions.RequestSucceededPrecondition(request_id='follow-up',
                                               awaited_request_id='awaited'),
    preconditions.ClusterStartCompletePrecondition(request_id='exec-1',
                                                   cluster_name='c1'),
])
def test_stored_precondition_rebuilds_the_same_precondition(precondition):
    """The kind and params stored with a request rebuild its precondition."""
    stored_params = json.loads(json.dumps(precondition.params()))
    rebuilt = preconditions.build_precondition(precondition.KIND,
                                               precondition.request_id,
                                               stored_params)
    assert type(rebuilt) is type(precondition)
    assert rebuilt.request_id == precondition.request_id
    assert rebuilt.params() == precondition.params()


def test_unknown_precondition_kind_is_rejected():
    with pytest.raises(ValueError, match='Unknown precondition kind'):
        preconditions.build_precondition('no_such_kind', 'req', {})


# --- Deprecated entry points keep working and warn when used ---


class _MetAfter(preconditions.Precondition):
    """A custom precondition with no KIND: it is met on the nth check."""

    def __init__(self, request_id, checks_until_met=2, **kwargs):
        super().__init__(request_id, **kwargs)
        self.checks_until_met = checks_until_met
        self.checks = 0

    async def check(self):
        self.checks += 1
        return self.checks >= self.checks_until_met, 'Waiting'


@pytest.fixture()
def deprecation_warnings():
    """The warnings that get logged."""
    with mock.patch.object(preconditions, 'logger') as fake_logger, \
         mock.patch.object(api_requests, 'get_request_async',
                           new=mock.AsyncMock(return_value=mock.MagicMock(
                               status=api_requests.RequestStatus.PENDING))), \
         mock.patch.object(api_requests, 'update_status_msg_async',
                           new=mock.AsyncMock()):
        yield lambda text: [
            call for call in fake_logger.warning.call_args_list
            if text in call.args[0]
        ]


@pytest.mark.asyncio
async def test_wait_async_still_waits_and_warns_when_used(deprecation_warnings):
    callback = mock.Mock()
    for _ in range(2):
        precondition = _MetAfter('req', check_interval=0.001)
        await precondition.wait_async(on_condition_met=callback)
        assert precondition.checks == 2
    assert callback.call_count == 2
    assert len(deprecation_warnings('Precondition.wait_async')) == 2


@pytest.mark.asyncio
async def test_awaiting_a_precondition_still_waits_and_warns_when_used(
        deprecation_warnings):
    assert await _MetAfter('req', check_interval=0.001) is True
    assert await _MetAfter('req', check_interval=0.001) is True
    assert len(deprecation_warnings('Awaiting a Precondition')) == 2


@pytest.mark.asyncio
async def test_awaiting_a_precondition_marks_the_request_failed_on_error(
        deprecation_warnings):

    class Errored(preconditions.Precondition):

        async def check(self):
            raise RuntimeError('Test error')

    with mock.patch.object(api_requests,
                           'set_request_failed_async',
                           new=mock.AsyncMock()) as set_failed:
        assert await Errored('req') is False
    set_failed.assert_awaited_once()
    assert deprecation_warnings('Awaiting a Precondition')


class _NeverMet(preconditions.Precondition):

    async def check(self):
        return False, 'Still checking'


class _RaisesOnCheck(preconditions.Precondition):

    async def check(self):
        raise RuntimeError('Test error')


@pytest.mark.asyncio
@mock.patch('sky.server.requests.requests.update_status_msg_async',
            new_callable=mock.AsyncMock)
@mock.patch('sky.server.requests.requests.set_request_failed_async',
            new_callable=mock.AsyncMock)
@mock.patch('sky.server.requests.requests.get_request_async',
            new_callable=mock.AsyncMock)
async def test_wait_in_process_timeout_marks_the_request_failed(
        mock_get_request, mock_set_failed, mock_update_status_msg):
    del mock_update_status_msg
    mock_get_request.return_value = mock.MagicMock(
        status=api_requests.RequestStatus.PENDING)
    callback = mock.Mock()

    await _NeverMet('req', check_interval=0.01,
                    timeout=0.05).wait_in_process(on_condition_met=callback)

    callback.assert_not_called()
    mock_set_failed.assert_awaited_once()
    failed_id, error = mock_set_failed.await_args.args
    assert failed_id == 'req'
    assert isinstance(error, exceptions.RequestCancelled)
    assert 'timed out after 0.05s' in str(error)


@pytest.mark.asyncio
@mock.patch('sky.server.requests.requests.set_request_failed_async',
            new_callable=mock.AsyncMock)
@mock.patch('sky.server.requests.requests.get_request_async',
            new_callable=mock.AsyncMock)
async def test_wait_in_process_stops_when_the_request_is_cancelled(
        mock_get_request, mock_set_failed):
    mock_get_request.return_value = mock.MagicMock(
        status=api_requests.RequestStatus.CANCELLED)
    callback = mock.Mock()
    precondition = _MetAfter('req', checks_until_met=1)

    await precondition.wait_in_process(on_condition_met=callback)

    # The request stays CANCELLED: nothing runs the check, fails it or queues it.
    assert precondition.checks == 0
    callback.assert_not_called()
    mock_set_failed.assert_not_awaited()


@pytest.mark.asyncio
@mock.patch('sky.server.requests.requests.set_request_failed_async',
            new_callable=mock.AsyncMock)
@mock.patch('sky.server.requests.requests.get_request_async',
            new_callable=mock.AsyncMock)
async def test_wait_in_process_check_exception_marks_the_request_failed(
        mock_get_request, mock_set_failed):
    mock_get_request.return_value = mock.MagicMock(
        status=api_requests.RequestStatus.PENDING)
    callback = mock.Mock()

    await _RaisesOnCheck('req').wait_in_process(on_condition_met=callback)

    callback.assert_not_called()
    mock_set_failed.assert_awaited_once()
    failed_id, error = mock_set_failed.await_args.args
    assert failed_id == 'req'
    assert isinstance(error, RuntimeError)
    assert str(error) == 'Test error'


def test_check_interval_argument_still_sets_the_interval_and_warns_when_used(
        deprecation_warnings):
    assert _MetAfter('req', check_interval=0.25).check_interval == 0.25
    assert _MetAfter('req', check_interval=0.5).check_interval == 0.5
    assert len(deprecation_warnings('check_interval')) == 2


def test_default_check_interval_does_not_warn(deprecation_warnings):
    assert _MetAfter('req').check_interval == 1
    assert deprecation_warnings('check_interval') == []


@pytest.mark.parametrize('precondition, storable', [
    (preconditions.ClusterStartCompletePrecondition('req', 'c1'), True),
    (preconditions.RequestSucceededPrecondition('req', 'other'), True),
    (_MetAfter('req'), False),
])
def test_only_registered_preconditions_are_storable(precondition, storable):
    assert preconditions.is_storable(precondition) is storable


def test_subclass_that_overrides_check_is_not_storable():

    class Custom(preconditions.ClusterStartCompletePrecondition):

        async def check(self):
            return True, None

    assert not preconditions.is_storable(Custom('req', 'c1'))


def test_subclass_without_params_can_be_created():
    """Outside subclasses written before `params` existed still work."""
    precondition = _MetAfter('req')
    assert not preconditions.is_storable(precondition)
    with pytest.raises(NotImplementedError):
        precondition.params()


def test_subclass_with_an_unregistered_kind_is_not_storable():

    class Outside(_MetAfter):
        KIND = 'registered_by_nobody'

    assert not preconditions.is_storable(Outside('req'))
