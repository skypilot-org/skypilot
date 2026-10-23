"""Readiness uses the provider's state and operation reconciliation together."""
# pylint: disable=protected-access
from types import SimpleNamespace
from unittest import mock

import pytest

from sky.provision.nebius import instance
from sky.provision.nebius import utils
from sky.utils import status_lib


def _instance(state, reconciling, name='cluster-head'):
    # Shape of nebius.compute.v1.Instance; no SDK credentials or RPCs.
    return SimpleNamespace(metadata=SimpleNamespace(id=name, name=name),
                           status=SimpleNamespace(
                               state=SimpleNamespace(name=state),
                               reconciling=reconciling,
                               network_interfaces=[]),
                           spec=SimpleNamespace(network_interfaces=[]))


@pytest.fixture(name='provider')
def mock_provider(monkeypatch):
    service = mock.Mock()
    compute = SimpleNamespace(InstanceServiceClient=lambda _: service,
                              ListInstancesRequest=SimpleNamespace)
    monkeypatch.setattr(utils.nebius, 'compute', lambda: compute)
    monkeypatch.setattr(utils.nebius, 'sdk', object)
    monkeypatch.setattr(utils.nebius, 'sync_call', lambda result: result)
    monkeypatch.setattr(utils, 'get_project_by_region', lambda _: 'project')
    sleep = mock.Mock()
    monkeypatch.setattr(instance.time, 'sleep', sleep)
    return service, sleep


def _states(provider, *states):
    service, _ = provider
    service.list.side_effect = [
        SimpleNamespace(items=[_instance(*state)], next_page_token='')
        for state in states
    ]


def test_list_retains_reconciliation_across_pages(provider):
    service, _ = provider
    service.list.side_effect = [
        SimpleNamespace(items=[_instance('STOPPED', True)],
                        next_page_token='p2'),
        SimpleNamespace(items=[_instance('RUNNING', False, 'cluster-worker')],
                        next_page_token=''),
    ]
    result = utils.list_instances('project')
    assert result['cluster-head']['status'] == 'STOPPED'
    assert result['cluster-head']['reconciling'] is True
    assert result['cluster-worker']['reconciling'] is False
    assert service.list.call_args_list[1].args[0].page_token == 'p2'
    assert all(call.kwargs['timeout'] == utils.nebius.READ_TIMEOUT
               for call in service.list.call_args_list)


def test_sdk_status_serialization_retains_reconciliation(provider):
    compute = pytest.importorskip('nebius.api.nebius.compute.v1')
    common = pytest.importorskip('nebius.api.nebius.common.v1')
    original = compute.Instance(
        metadata=common.ResourceMetadata(id='cluster-head',
                                         name='cluster-head'),
        status=compute.InstanceStatus(
            state=compute.InstanceStatus.InstanceState.STOPPED,
            reconciling=True))
    restored = compute.Instance.FromString(original.SerializeToString())
    provider[0].list.return_value = SimpleNamespace(items=[restored],
                                                    next_page_token='')
    result = utils.list_instances('project')
    assert result['cluster-head']['status'] == 'STOPPED'
    assert result['cluster-head']['reconciling'] is True


def test_starting_stopped_reconciling_running(provider):
    _states(provider, ('STARTING', True), ('STOPPED', True), ('STOPPED', True),
            ('RUNNING', False))
    instance.wait_instances('region', 'cluster', status_lib.ClusterStatus.UP)
    service, sleep = provider
    assert service.list.call_count == 4
    assert sleep.call_args_list == [mock.call(utils.POLL_INTERVAL)] * 3


@pytest.mark.parametrize('state',
                         ['STOPPED', 'RUNNING', 'CREATING', 'UPDATING'])
def test_reconciling_is_not_a_settled_state(provider, state):
    _states(provider, (state, True), ('RUNNING', False))
    instance.wait_instances('region', 'cluster', status_lib.ClusterStatus.UP)
    assert provider[0].list.call_count == 2
    provider[1].assert_called_once_with(utils.POLL_INTERVAL)


@pytest.mark.parametrize('state', ['STARTING', 'STOPPING', 'DELETING'])
def test_existing_pending_states_still_wait(provider, state):
    _states(provider, (state, False), ('RUNNING', False))
    instance.wait_instances('region', 'cluster', status_lib.ClusterStatus.UP)
    provider[1].assert_called_once_with(utils.POLL_INTERVAL)


@pytest.mark.parametrize('wanted,settled,error', [
    (status_lib.ClusterStatus.UP, 'STOPPED', 'instances are stopped'),
    (status_lib.ClusterStatus.STOPPED, 'RUNNING', 'instances are running'),
])
def test_settled_wrong_state_still_fails(provider, wanted, settled, error):
    _states(provider, ('STOPPED', True), (settled, False))
    with pytest.raises(RuntimeError, match=error):
        instance.wait_instances('region', 'cluster', wanted)
    assert provider[0].list.call_count == 2
    provider[1].assert_called_once_with(utils.POLL_INTERVAL)


def test_stop_waits_until_operation_settles(provider):
    _states(provider, ('STOPPING', True), ('STOPPED', True), ('STOPPED', False))
    instance.wait_instances('region', 'cluster',
                            status_lib.ClusterStatus.STOPPED)
    assert provider[0].list.call_count == 3
    assert provider[1].call_count == 2


def test_validates_same_settled_snapshot(provider):
    _states(provider, ('RUNNING', False), ('STOPPED', True))
    instance.wait_instances('region', 'cluster', status_lib.ClusterStatus.UP)
    # There is no second, unvalidated state read after the readiness decision.
    assert provider[0].list.call_count == 1
    provider[1].assert_not_called()


def test_pending_budget_does_not_reset_on_state_change(provider, monkeypatch):
    monkeypatch.setattr(instance, 'MAX_RETRIES_TO_LAUNCH', 3)
    _states(provider, ('STARTING', True), ('STOPPED', True), ('RUNNING', True))
    with pytest.raises(TimeoutError, match='15 seconds'):
        instance.wait_instances('region', 'cluster',
                                status_lib.ClusterStatus.UP)
    assert provider[0].list.call_count == 3
    assert provider[1].call_args_list == [mock.call(utils.POLL_INTERVAL)] * 3
    assert instance.MAX_RETRIES_TO_LAUNCH * utils.POLL_INTERVAL == 15


@pytest.mark.parametrize(
    'error', [RuntimeError('provider read failed'),
              KeyboardInterrupt()])
def test_original_error_and_interruption_escape(provider, error):
    _states(provider, ('STOPPED', True))
    provider[1].side_effect = error
    with pytest.raises(type(error)) as caught:
        instance.wait_instances('region', 'cluster',
                                status_lib.ClusterStatus.UP)
    assert caught.value is error
    assert provider[0].list.call_count == 1


def test_empty_and_foreign_instances_remain_outside_cluster(provider):
    service, sleep = provider
    service.list.return_value = SimpleNamespace(
        items=[_instance('STOPPED', True, 'other-head')], next_page_token='')
    observed = instance._wait_until_no_pending('region', 'cluster', 'project')
    assert isinstance(observed, dict) and not observed
    sleep.assert_not_called()
    service.list.return_value = SimpleNamespace(items=[], next_page_token='')
    # Preserve existing absent-cluster semantics; this helper has no expected
    # instance IDs/count. Later cluster-info validation owns that contract.
    observed = instance._wait_until_no_pending('region', 'cluster', 'project')
    assert isinstance(observed, dict) and not observed
    sleep.assert_not_called()
