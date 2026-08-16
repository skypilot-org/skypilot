"""Tests for backend-produced generation-fenced durable autodown."""

import concurrent.futures
import dataclasses
import pickle
import threading
from typing import Optional
from unittest import mock

import grpc
import pytest

from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import resources as resources_lib
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.clouds.cloud import TeardownExecutionStrategy
from sky.provision import common as provision_common
from sky.schemas.generated import autostopv1_pb2
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.utils import locks
from sky.utils import status_lib
from sky.utils.db import db_utils


@pytest.fixture
def fresh_state_db(tmp_path, monkeypatch):
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setenv('SKYPILOT_ENABLE_GRPC', '1')
    monkeypatch.setattr(locks, 'SKY_LOCKS_DIR', str(tmp_path / 'locks'))
    manager = db_utils.DatabaseManager(
        'state',
        global_user_state.create_table,
        # pylint: disable=protected-access
        post_init_fn=lambda _: global_user_state._sqlite_supports_returning(),
    )
    monkeypatch.setattr(global_user_state, '_db_manager', manager)
    monkeypatch.setattr(global_user_state, 'initialize_and_get_db',
                        manager.get_engine)
    return manager.get_engine()


def _make_handle(
    strategy: TeardownExecutionStrategy = (TeardownExecutionStrategy.
                                           HEAD_WITH_SERVER_FALLBACK),
    cluster_name: str = 'cluster',
    cloud: Optional[clouds.Cloud] = None,
):
    if cloud is None:
        cloud = clouds.AWS()
    handle = cloud_vm_ray_backend.CloudVmRayResourceHandle(
        cluster_name=cluster_name,
        cluster_name_on_cloud=f'{cluster_name}-cloud',
        cluster_yaml=None,
        launched_nodes=1,
        launched_resources=resources_lib.Resources(cloud=cloud,
                                                   instance_type='m5.large'),
        teardown_execution_strategy=strategy,
    )
    handle.provision_runtime_metadata = (
        provision_common.ProvisionRuntimeMetadata(
            has_ray=True,
            has_skylet=True,
            has_job_queue=True,
            ssh_available=True,
        ))
    return handle


def _add_cluster(handle):
    global_user_state.add_or_update_cluster(
        handle.cluster_name,
        cluster_handle=handle,
        requested_resources=None,
        ready=True,
    )
    record = global_user_state.get_cluster_from_name(handle.cluster_name,
                                                     include_user_info=False,
                                                     summary_response=True)
    assert record is not None
    return record


def _durable_status(supported: bool = True, **kwargs):
    return autostopv1_pb2.IsAutostoppingResponse(
        supports_durable_autodown=supported, **kwargs)


def _patch_skylet(monkeypatch,
                  *,
                  status=None,
                  status_side_effect=None,
                  apply_side_effect=None):
    if status is None:
        status = _durable_status()
    get_status = mock.Mock(return_value=status, side_effect=status_side_effect)
    apply_autodown_intent = mock.Mock(
        return_value=autostopv1_pb2.SetAutostopResponse(
            supports_durable_autodown=True),
        side_effect=apply_side_effect)
    set_autostop = mock.Mock(return_value=autostopv1_pb2.SetAutostopResponse())
    client = mock.Mock()
    client.get_autodown_status = get_status
    client.apply_autodown_intent = apply_autodown_intent
    client.set_autostop = set_autostop
    monkeypatch.setattr(cloud_vm_ray_backend, 'SkyletClient',
                        lambda _channel: client)
    monkeypatch.setattr(
        cloud_vm_ray_backend.CloudVmRayResourceHandle,
        'get_grpc_channel',
        lambda self: 'channel',
    )
    return get_status, apply_autodown_intent, set_autostop


def test_handle_persists_deployment_strategy_across_config_drift():
    handle = _make_handle(TeardownExecutionStrategy.SERVER_ONLY)

    serialized_state = handle.__getstate__()
    pickle_payload = pickle.dumps(handle)
    restored_pickle = pickle.loads(pickle_payload)
    restored_dict = cloud_vm_ray_backend.CloudVmRayResourceHandle.from_dict(
        handle.to_dict())

    assert serialized_state['teardown_execution_strategy'] == 'server_only'
    assert b'TeardownExecutionStrategy' not in pickle_payload
    assert restored_pickle.teardown_execution_strategy is (
        TeardownExecutionStrategy.SERVER_ONLY)
    assert restored_dict.teardown_execution_strategy is (
        TeardownExecutionStrategy.SERVER_ONLY)
    assert handle.to_dict()['teardown_execution_strategy'] == 'server_only'


def test_old_handle_deserialization_defaults_to_legacy_strategy():
    handle = _make_handle(TeardownExecutionStrategy.SERVER_ONLY)
    old_state = handle.__getstate__()
    old_state['_version'] = 13
    old_state.pop('teardown_execution_strategy')
    restored = cloud_vm_ray_backend.CloudVmRayResourceHandle.__new__(
        cloud_vm_ray_backend.CloudVmRayResourceHandle)

    restored.__setstate__(old_state)
    restored_dict = cloud_vm_ray_backend.CloudVmRayResourceHandle.from_dict({
        key: value
        for key, value in handle.to_dict().items()
        if key != 'teardown_execution_strategy'
    })

    assert restored.teardown_execution_strategy is (
        TeardownExecutionStrategy.LEGACY_HEAD_CREDENTIALS)
    assert restored_dict.teardown_execution_strategy is (
        TeardownExecutionStrategy.LEGACY_HEAD_CREDENTIALS)


def test_skylet_client_durable_status_helper_returns_exact_response():
    expected = _durable_status(
        is_autostopping=True,
        cluster_hash='cluster-hash',
        generation=7,
        durable_execution_state=(
            autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED),
        error_summary='server teardown required',
    )
    client = cloud_vm_ray_backend.SkyletClient.__new__(
        cloud_vm_ray_backend.SkyletClient)
    client.is_autostopping = mock.Mock(return_value=expected)

    response = client.get_autodown_status()

    assert response is expected
    request = client.is_autostopping.call_args.args[0]
    assert isinstance(request, autostopv1_pb2.IsAutostoppingRequest)


def test_skylet_client_apply_autodown_intent_uses_dedicated_rpc():
    request = autostopv1_pb2.SetAutostopRequest(
        idle_minutes=15,
        down=True,
        cluster_hash='hash',
        generation=3,
        execution_strategy=(
            autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_SERVER_ONLY),
    )
    expected = autostopv1_pb2.SetAutostopResponse(
        supports_durable_autodown=True)
    client = cloud_vm_ray_backend.SkyletClient.__new__(
        cloud_vm_ray_backend.SkyletClient)
    client._autostop_stub = mock.Mock()
    client._autostop_stub.ApplyAutodownIntent.return_value = expected

    response = client.apply_autodown_intent(request)

    assert response is expected
    client._autostop_stub.ApplyAutodownIntent.assert_called_once_with(
        request, timeout=constants.SKYLET_GRPC_TIMEOUT_SECONDS)


def test_backend_durable_status_helper_uses_retrying_grpc_query(monkeypatch):
    """Durable status queries bypass the general gRPC migration flag."""
    monkeypatch.delenv('SKYPILOT_ENABLE_GRPC', raising=False)
    handle = _make_handle()
    expected = _durable_status(is_autostopping=True, generation=3)
    get_status, _, _ = _patch_skylet(monkeypatch, status=expected)
    invoke = mock.Mock(side_effect=lambda function: function())
    monkeypatch.setattr(backend_utils, 'invoke_skylet_with_retries', invoke)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    response = backend.get_durable_autodown_status(handle)

    assert response is expected
    invoke.assert_called_once()
    get_status.assert_called_once_with()


def test_is_definitely_autostopping_reuses_backend_status_helper(monkeypatch):
    """Strict durable teardown reads Skylet status when the flag is unset."""
    monkeypatch.delenv('SKYPILOT_ENABLE_GRPC', raising=False)
    handle = _make_handle()
    handle.stable_internal_external_ips = [('10.0.0.1', '34.1.2.3')]
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    get_status = mock.Mock(return_value=_durable_status(is_autostopping=True))
    run_on_head = mock.Mock()
    monkeypatch.setattr(backend, 'get_durable_autodown_status', get_status)
    monkeypatch.setattr(backend, 'run_on_head', run_on_head)

    assert backend.is_definitely_autostopping(handle)

    get_status.assert_called_once_with(handle)
    run_on_head.assert_not_called()


@pytest.mark.usefixtures('fresh_state_db')
def test_strict_autodown_applies_when_feature_flag_is_unset(monkeypatch):
    """Strict durable intents use their Skylet RPC without the global flag."""
    monkeypatch.delenv('SKYPILOT_ENABLE_GRPC', raising=False)
    handle = _make_handle()
    _add_cluster(handle)
    get_status, apply_autodown_intent, _ = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)

    get_status.assert_called_once_with()
    apply_autodown_intent.assert_called_once()
    assert global_user_state.get_autodown_intent('cluster') is not None


@pytest.mark.usefixtures('fresh_state_db')
def test_old_skylet_fails_before_strict_intent_mutation(monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)
    _, apply_autodown_intent, _ = _patch_skylet(
        monkeypatch, status=_durable_status(supported=False))
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    with pytest.raises(exceptions.NotSupportedError,
                       match='durable autodown support'):
        backend.set_autostop(handle,
                             15,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=True)

    assert global_user_state.get_autodown_intent('cluster') is None
    record = global_user_state.get_cluster_from_name('cluster')
    assert record is not None
    assert (record['autostop'], record['to_down']) == (-1, False)
    apply_autodown_intent.assert_not_called()


@pytest.mark.usefixtures('fresh_state_db')
def test_unimplemented_capability_probe_fails_before_mutation(monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)

    class UnimplementedRpcError(grpc.RpcError):

        def code(self):
            return grpc.StatusCode.UNIMPLEMENTED

        def details(self):
            return 'method not implemented'

    unimplemented = UnimplementedRpcError()
    _, apply_autodown_intent, _ = _patch_skylet(
        monkeypatch, status_side_effect=unimplemented)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    with pytest.raises(exceptions.NotSupportedError,
                       match='does not implement durable autodown'):
        backend.set_autostop(handle,
                             15,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=True)

    assert global_user_state.get_autodown_intent('cluster') is None
    apply_autodown_intent.assert_not_called()


@pytest.mark.usefixtures('fresh_state_db')
def test_strict_autodown_requires_grpc_before_mutation(monkeypatch):
    """Strict durable teardown rejects handles without actual gRPC support."""
    monkeypatch.delenv('SKYPILOT_ENABLE_GRPC', raising=False)
    handle = _make_handle()
    handle.is_grpc_enabled = False
    _add_cluster(handle)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    with pytest.raises(exceptions.NotSupportedError, match='requires gRPC'):
        backend.set_autostop(handle,
                             15,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=True)

    assert global_user_state.get_autodown_intent('cluster') is None


@pytest.mark.usefixtures('fresh_state_db')
def test_strict_autodown_requires_skylet_before_mutation():
    handle = _make_handle()
    handle.provision_runtime_metadata = (
        provision_common.ProvisionRuntimeMetadata(
            has_ray=True,
            has_skylet=False,
            has_job_queue=False,
            ssh_available=True,
        ))
    _add_cluster(handle)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    with pytest.raises(exceptions.NotSupportedError,
                       match='requires a compatible gRPC skylet'):
        backend.set_autostop(handle,
                             15,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=True)

    assert global_user_state.get_autodown_intent('cluster') is None


class _DeadlineExceededRpcError(grpc.RpcError):
    """Minimal deadline error used to exercise Skylet transport handling."""

    def code(self):
        return grpc.StatusCode.DEADLINE_EXCEEDED

    def details(self):
        return 'Deadline Exceeded'


@pytest.mark.usefixtures('fresh_state_db')
@pytest.mark.parametrize('terminate', [False, True])
def test_manual_teardown_handles_unavailable_skylet_by_outcome(
        monkeypatch, terminate):
    """Allow down to proceed past an unavailable Skylet only for terminate."""
    handle = _make_handle()
    _add_cluster(handle)
    get_status, _, _ = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    get_status.reset_mock()
    get_status.side_effect = _DeadlineExceededRpcError()
    close_tunnel = mock.Mock()
    monkeypatch.setattr(
        cloud_vm_ray_backend.CloudVmRayResourceHandle,
        'close_skylet_ssh_tunnel',
        lambda self: close_tunnel(),
    )

    with mock.patch.object(backend, 'teardown_no_lock') as teardown:
        if terminate:
            backend._teardown(handle, terminate=True)
            teardown.assert_called_once()
        else:
            with pytest.raises(RuntimeError,
                               match='cannot be manually stopped'):
                backend._teardown(handle, terminate=False)
            teardown.assert_not_called()

    get_status.assert_called_once_with()
    close_tunnel.assert_called_once_with()


@pytest.mark.usefixtures('fresh_state_db')
@pytest.mark.parametrize(
    'channel_error',
    [
        pytest.param(grpc.FutureTimeoutError(), id='future-timeout'),
        pytest.param(RuntimeError('tunnel open failed'), id='tunnel-error'),
    ])
@pytest.mark.parametrize('terminate', [False, True])
def test_manual_teardown_handles_tunnel_setup_failure_by_outcome(
        monkeypatch, channel_error, terminate):
    """Allow termination past Skylet tunnel setup failures, but not stop."""
    handle = _make_handle()
    _add_cluster(handle)
    get_status, _, _ = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    get_status.reset_mock()
    monkeypatch.setattr(
        cloud_vm_ray_backend.CloudVmRayResourceHandle,
        'get_grpc_channel',
        mock.Mock(side_effect=channel_error),
    )
    close_tunnel = mock.Mock()
    monkeypatch.setattr(
        cloud_vm_ray_backend.CloudVmRayResourceHandle,
        'close_skylet_ssh_tunnel',
        lambda self: close_tunnel(),
    )

    with mock.patch.object(backend, 'teardown_no_lock') as teardown:
        if terminate:
            backend._teardown(handle, terminate=True)
            teardown.assert_called_once()
        else:
            with pytest.raises(RuntimeError,
                               match='cannot be manually stopped'):
                backend._teardown(handle, terminate=False)
            teardown.assert_not_called()

    get_status.assert_not_called()
    close_tunnel.assert_called_once_with()


@pytest.mark.usefixtures('fresh_state_db')
def test_strict_arm_update_and_cancel_use_newer_generations(monkeypatch):
    handle = _make_handle(TeardownExecutionStrategy.SERVER_ONLY)
    record = _add_cluster(handle)
    _, apply_autodown_intent, set_autostop = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True,
                         hook='legacy-hook',
                         hook_timeout=17,
                         hooks=[{
                             'run': 'hook',
                             'events': ['down'],
                             'timeout': 19,
                         }])
    first = global_user_state.get_autodown_intent('cluster')
    assert first is not None
    assert first.generation == 1
    assert first.state is global_user_state.AutodownIntentState.ARMED

    backend.set_autostop(handle,
                         30,
                         autostop_lib.AutostopWaitFor.JOBS,
                         down=True)
    second = global_user_state.get_autodown_intent('cluster')
    assert second is not None
    assert second.generation == 2
    assert second.state is global_user_state.AutodownIntentState.ARMED

    backend.set_autostop(handle,
                         -1,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=False)
    cancelled = global_user_state.get_autodown_intent('cluster')
    assert cancelled is not None
    assert cancelled.generation == 3
    assert cancelled.state is global_user_state.AutodownIntentState.CANCELLED
    assert set_autostop.call_count == 0
    assert {
        call.args[0].generation for call in apply_autodown_intent.call_args_list
    } == {1, 2, 3}
    first_request = apply_autodown_intent.call_args_list[0].args[0]
    assert first_request.cluster_hash == record['cluster_hash']
    assert first_request.execution_strategy == (
        autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_SERVER_ONLY)
    assert first_request.hook == 'legacy-hook'
    assert first_request.hook_timeout == 17
    assert len(first_request.hooks) == 1
    final_record = global_user_state.get_cluster_from_name('cluster')
    assert final_record is not None
    assert (final_record['autostop'], final_record['to_down']) == (-1, False)


@pytest.mark.usefixtures('fresh_state_db')
def test_switch_from_strict_autodown_to_autostop_is_fenced(monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)
    _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)

    backend.set_autostop(handle,
                         20,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=False)

    intent = global_user_state.get_autodown_intent('cluster')
    assert intent is not None
    assert intent.generation == 2
    assert intent.state is global_user_state.AutodownIntentState.CANCELLED
    record = global_user_state.get_cluster_from_name('cluster')
    assert record is not None
    assert (record['autostop'], record['to_down']) == (20, False)


@pytest.mark.usefixtures('fresh_state_db')
def test_rpc_failure_leaves_configuring_and_retry_advances_generation(
        monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)
    _, apply_autodown_intent, _ = _patch_skylet(
        monkeypatch, apply_side_effect=RuntimeError('rpc failed'))
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    with pytest.raises(RuntimeError, match='rpc failed'):
        backend.set_autostop(handle,
                             15,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=True)

    configuring = global_user_state.get_autodown_intent('cluster')
    assert configuring is not None
    assert configuring.generation == 1
    assert configuring.state is global_user_state.AutodownIntentState.CONFIGURING
    record = global_user_state.get_cluster_from_name('cluster')
    assert record is not None
    assert (record['autostop'], record['to_down']) == (-1, False)

    apply_autodown_intent.side_effect = None
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    armed = global_user_state.get_autodown_intent('cluster')
    assert armed is not None
    assert armed.generation == 2
    assert armed.state is global_user_state.AutodownIntentState.ARMED


@pytest.mark.usefixtures('fresh_state_db')
@pytest.mark.parametrize(('durable_state', 'expected_intent_state'), [
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED,
     global_user_state.AutodownIntentState.PREPARING),
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
     global_user_state.AutodownIntentState.READY),
])
def test_rejected_post_claim_update_preserves_irreversible_teardown(
        monkeypatch, durable_state, expected_intent_state):
    handle = _make_handle()
    record = _add_cluster(handle)
    get_status, apply_autodown_intent, _ = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    armed = global_user_state.get_autodown_intent('cluster')
    assert armed is not None

    get_status.return_value = _durable_status(
        cluster_hash=armed.cluster_hash,
        generation=armed.generation,
        durable_execution_state=durable_state,
    )
    apply_autodown_intent.side_effect = RuntimeError('update rejected')

    with pytest.raises(RuntimeError, match='teardown has already begun'):
        backend.set_autostop(handle,
                             -1,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=False)

    current = global_user_state.get_autodown_intent('cluster')
    assert current is not None
    assert current.generation == armed.generation
    assert current.state is expected_intent_state
    assert current.idle_minutes == armed.idle_minutes
    assert current.to_down is armed.to_down
    assert current.execution_strategy == armed.execution_strategy
    current_record = global_user_state.get_cluster_from_name('cluster')
    assert current_record is not None
    assert current_record['cluster_hash'] == record['cluster_hash']
    assert (current_record['autostop'], current_record['to_down']) == (15, True)

    apply_autodown_intent.side_effect = None
    with pytest.raises(RuntimeError, match='teardown has already begun'):
        backend.set_autostop(handle,
                             -1,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=False)
    assert global_user_state.get_autodown_intent('cluster') == current
    assert apply_autodown_intent.call_count == 2


@pytest.mark.usefixtures('fresh_state_db')
def test_rejected_replacement_restores_predecessor_when_status_read_fails(
        monkeypatch):
    """Rejected updates restore the predecessor despite a failed status read."""
    handle = _make_handle()
    _add_cluster(handle)
    get_status, apply_autodown_intent, _ = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    predecessor = global_user_state.get_autodown_intent('cluster')
    assert predecessor is not None

    get_status.side_effect = [
        _durable_status(),
        RuntimeError('status unavailable'),
    ]
    apply_autodown_intent.side_effect = exceptions.SkyletInternalError(
        'update rejected')

    with pytest.raises(exceptions.SkyletInternalError, match='update rejected'):
        backend.set_autostop(handle,
                             30,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=True)

    restored = global_user_state.get_autodown_intent('cluster')
    assert restored is not None
    assert dataclasses.replace(restored,
                               updated_at=predecessor.updated_at) == predecessor


@pytest.mark.usefixtures('fresh_state_db')
def test_ambiguous_apply_ack_keeps_replacement_when_status_read_fails(
        monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)
    get_status, apply_autodown_intent, _ = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    predecessor = global_user_state.get_autodown_intent('cluster')
    assert predecessor is not None

    get_status.side_effect = [
        _durable_status(),
        RuntimeError('status unavailable'),
    ]
    apply_autodown_intent.side_effect = exceptions.SkyletUnavailableError(
        'commit acknowledgement lost')

    with pytest.raises(exceptions.SkyletUnavailableError,
                       match='commit acknowledgement lost'):
        backend.set_autostop(handle,
                             30,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=True)

    current = global_user_state.get_autodown_intent('cluster')
    assert current is not None
    assert current.cluster_hash == predecessor.cluster_hash
    assert current.generation == predecessor.generation + 1
    assert current.state is global_user_state.AutodownIntentState.CONFIGURING
    assert current.idle_minutes == 30


@pytest.mark.usefixtures('fresh_state_db')
def test_lost_finalization_ack_for_exact_terminal_state_is_success(monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)
    _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    real_cas = global_user_state.compare_and_swap_autodown_intent

    def transition_then_lose_ack(**kwargs):
        assert real_cas(**kwargs)
        return False

    monkeypatch.setattr(global_user_state, 'compare_and_swap_autodown_intent',
                        transition_then_lose_ack)

    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)

    intent = global_user_state.get_autodown_intent('cluster')
    assert intent is not None
    assert intent.state is global_user_state.AutodownIntentState.ARMED
    record = global_user_state.get_cluster_from_name('cluster')
    assert record is not None
    assert (record['autostop'], record['to_down']) == (15, True)


@pytest.mark.usefixtures('fresh_state_db')
def test_lost_finalization_commit_ack_is_resolved_by_exact_read(monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)
    _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    real_cas = global_user_state.compare_and_swap_autodown_intent

    def transition_then_lose_commit_ack(**kwargs):
        assert real_cas(**kwargs)
        raise RuntimeError('commit acknowledgement lost')

    monkeypatch.setattr(global_user_state, 'compare_and_swap_autodown_intent',
                        transition_then_lose_commit_ack)

    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)

    intent = global_user_state.get_autodown_intent('cluster')
    assert intent is not None
    assert intent.state is global_user_state.AutodownIntentState.ARMED
    record = global_user_state.get_cluster_from_name('cluster')
    assert record is not None
    assert (record['autostop'], record['to_down']) == (15, True)


@pytest.mark.usefixtures('fresh_state_db')
def test_lost_finalization_ack_rejects_changed_attempt_fence(monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)
    _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    real_cas = global_user_state.compare_and_swap_autodown_intent
    real_get_intent = global_user_state.get_autodown_intent

    def transition_then_lose_commit_ack(**kwargs):
        assert real_cas(**kwargs)
        raise RuntimeError('commit acknowledgement lost')

    def get_changed_attempt(cluster_name):
        current = real_get_intent(cluster_name)
        if (current is not None and
                current.state is global_user_state.AutodownIntentState.ARMED):
            return dataclasses.replace(current,
                                       attempt_count=current.attempt_count + 1)
        return current

    monkeypatch.setattr(global_user_state, 'compare_and_swap_autodown_intent',
                        transition_then_lose_commit_ack)
    monkeypatch.setattr(global_user_state, 'get_autodown_intent',
                        get_changed_attempt)

    with pytest.raises(RuntimeError, match='commit acknowledgement lost'):
        backend.set_autostop(handle,
                             15,
                             autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                             down=True)


@pytest.mark.usefixtures('fresh_state_db')
@pytest.mark.parametrize('terminate', [False, True])
def test_manual_teardown_applies_newer_skylet_cancellation(
        monkeypatch, terminate):
    handle = _make_handle()
    _add_cluster(handle)
    _, apply_autodown_intent, set_autostop = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)

    with mock.patch.object(backend, 'teardown_no_lock') as teardown:
        backend._teardown(handle, terminate=terminate)

    cancellation = global_user_state.get_autodown_intent('cluster')
    assert cancellation is not None
    assert cancellation.generation == 2
    assert cancellation.state is global_user_state.AutodownIntentState.CANCELLED
    cancel_request = apply_autodown_intent.call_args_list[-1].args[0]
    assert cancel_request.idle_minutes == -1
    assert not cancel_request.down
    assert cancel_request.generation == cancellation.generation
    assert cancel_request.cluster_hash == cancellation.cluster_hash
    teardown.assert_called_once()
    assert apply_autodown_intent.call_count == 2
    set_autostop.assert_not_called()


@pytest.mark.usefixtures('fresh_state_db')
def test_runpod_expected_hash_is_revalidated_before_provider_teardown(
        monkeypatch):
    stale_handle = _make_handle(cloud=clouds.RunPod())
    stale_handle.cluster_yaml = '/tmp/stale.yaml'
    stale_record = _add_cluster(stale_handle)
    replacement_handle = _make_handle(cloud=clouds.RunPod())
    replacement_handle.cluster_yaml = '/tmp/replacement.yaml'
    replacement_handle.provision_runtime_metadata = (
        provision_common.ProvisionRuntimeMetadata(has_ray=False))
    replacement_record = {}
    provider_teardown = mock.Mock()
    post_teardown_cleanup = mock.Mock()
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    def refresh_stale_incarnation(cluster_name, **kwargs):
        assert cluster_name == stale_handle.cluster_name
        assert kwargs['cluster_lock_already_held'] is True
        return status_lib.ClusterStatus.UP, stale_handle

    def replace_after_first_fence(*_args, **_kwargs):
        global_user_state.remove_cluster(stale_handle.cluster_name,
                                         terminate=True)
        replacement_record.update(_add_cluster(replacement_handle))

    monkeypatch.setattr(cloud_vm_ray_backend.requests_lib,
                        'kill_cluster_requests', mock.Mock())
    monkeypatch.setattr(backend_utils, 'refresh_cluster_status_handle',
                        refresh_stale_incarnation)
    monkeypatch.setattr(global_user_state, 'get_cluster_yaml_dict',
                        lambda _: {'provider': {}})
    monkeypatch.setattr(cloud_vm_ray_backend.provisioner, 'teardown_cluster',
                        provider_teardown)
    monkeypatch.setattr(backend, 'post_teardown_cleanup', post_teardown_cleanup)
    run_on_head = mock.Mock(side_effect=replace_after_first_fence)
    monkeypatch.setattr(backend, 'run_on_head', run_on_head)

    backend.teardown_no_lock(stale_handle,
                             terminate=True,
                             expected_cluster_hash=stale_record['cluster_hash'])

    current = global_user_state.get_cluster_from_name(stale_handle.cluster_name,
                                                      include_user_info=False,
                                                      summary_response=True)
    assert current is not None
    assert current['cluster_hash'] == replacement_record['cluster_hash']
    assert current['handle'].cluster_yaml == replacement_handle.cluster_yaml
    run_on_head.assert_called_once()
    provider_teardown.assert_not_called()
    post_teardown_cleanup.assert_not_called()


@pytest.mark.usefixtures('fresh_state_db')
def test_runpod_expected_hash_is_revalidated_after_provider_teardown(
        monkeypatch):
    stale_handle = _make_handle(cloud=clouds.RunPod())
    stale_handle.cluster_yaml = '/tmp/stale.yaml'
    stale_handle.provision_runtime_metadata = (
        provision_common.ProvisionRuntimeMetadata(has_ray=False))
    stale_record = _add_cluster(stale_handle)
    replacement_handle = _make_handle(cloud=clouds.RunPod())
    replacement_handle.cluster_yaml = '/tmp/replacement.yaml'
    replacement_record = {}
    post_teardown_cleanup = mock.Mock()
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    def refresh_stale_incarnation(cluster_name, **kwargs):
        assert cluster_name == stale_handle.cluster_name
        return status_lib.ClusterStatus.UP, stale_handle

    def teardown_then_replace(*_args, **_kwargs):
        global_user_state.remove_cluster(stale_handle.cluster_name,
                                         terminate=True)
        replacement_record.update(_add_cluster(replacement_handle))

    monkeypatch.setattr(cloud_vm_ray_backend.requests_lib,
                        'kill_cluster_requests', mock.Mock())
    monkeypatch.setattr(backend_utils, 'refresh_cluster_status_handle',
                        refresh_stale_incarnation)
    monkeypatch.setattr(global_user_state, 'get_cluster_yaml_dict',
                        lambda _: {'provider': {}})
    monkeypatch.setattr(cloud_vm_ray_backend.provisioner, 'teardown_cluster',
                        teardown_then_replace)
    monkeypatch.setattr(backend, 'post_teardown_cleanup', post_teardown_cleanup)

    backend.teardown_no_lock(stale_handle,
                             terminate=True,
                             expected_cluster_hash=stale_record['cluster_hash'])

    current = global_user_state.get_cluster_from_name(stale_handle.cluster_name,
                                                      include_user_info=False,
                                                      summary_response=True)
    assert current is not None
    assert current['cluster_hash'] == replacement_record['cluster_hash']
    post_teardown_cleanup.assert_not_called()


@pytest.mark.usefixtures('fresh_state_db')
def test_legacy_expected_hash_is_revalidated_before_teardown_command(
        monkeypatch):
    stale_handle = _make_handle(cloud=clouds.IBM())
    stale_handle.cluster_yaml = '/tmp/stale.yaml'
    stale_record = _add_cluster(stale_handle)
    replacement_handle = _make_handle(cloud=clouds.IBM())
    replacement_handle.cluster_yaml = '/tmp/replacement.yaml'
    replacement_record = {}
    teardown_command = mock.Mock(return_value=(0, '', ''))
    post_teardown_cleanup = mock.Mock()
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    def refresh_stale_incarnation(cluster_name, **kwargs):
        assert cluster_name == stale_handle.cluster_name
        assert kwargs['cluster_lock_already_held'] is True
        return status_lib.ClusterStatus.UP, stale_handle

    def replace_after_first_fence(*_args, **_kwargs):
        global_user_state.remove_cluster(stale_handle.cluster_name,
                                         terminate=True)
        replacement_record.update(_add_cluster(replacement_handle))

    monkeypatch.setattr(cloud_vm_ray_backend.requests_lib,
                        'kill_cluster_requests', mock.Mock())
    monkeypatch.setattr(backend_utils, 'refresh_cluster_status_handle',
                        refresh_stale_incarnation)
    monkeypatch.setattr(global_user_state, 'get_cluster_yaml_dict',
                        lambda _: {'provider': {}})
    monkeypatch.setattr(cloud_vm_ray_backend.yaml_utils, 'dump_yaml',
                        replace_after_first_fence)
    monkeypatch.setattr(cloud_vm_ray_backend.log_lib, 'run_with_log',
                        teardown_command)
    monkeypatch.setattr(backend, 'post_teardown_cleanup', post_teardown_cleanup)

    backend.teardown_no_lock(stale_handle,
                             terminate=True,
                             expected_cluster_hash=stale_record['cluster_hash'])

    current = global_user_state.get_cluster_from_name(stale_handle.cluster_name,
                                                      include_user_info=False,
                                                      summary_response=True)
    assert current is not None
    assert current['cluster_hash'] == replacement_record['cluster_hash']
    teardown_command.assert_not_called()
    post_teardown_cleanup.assert_not_called()


@pytest.mark.usefixtures('fresh_state_db')
def test_legacy_expected_hash_is_revalidated_after_teardown_command(
        monkeypatch):
    stale_handle = _make_handle(cloud=clouds.IBM())
    stale_handle.cluster_yaml = '/tmp/stale.yaml'
    stale_record = _add_cluster(stale_handle)
    replacement_handle = _make_handle(cloud=clouds.IBM())
    replacement_handle.cluster_yaml = '/tmp/replacement.yaml'
    replacement_record = {}
    post_teardown_cleanup = mock.Mock()
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    def refresh_stale_incarnation(cluster_name, **kwargs):
        assert cluster_name == stale_handle.cluster_name
        return status_lib.ClusterStatus.UP, stale_handle

    def teardown_then_replace(*_args, **_kwargs):
        global_user_state.remove_cluster(stale_handle.cluster_name,
                                         terminate=True)
        replacement_record.update(_add_cluster(replacement_handle))
        return 0, '', ''

    monkeypatch.setattr(cloud_vm_ray_backend.requests_lib,
                        'kill_cluster_requests', mock.Mock())
    monkeypatch.setattr(backend_utils, 'refresh_cluster_status_handle',
                        refresh_stale_incarnation)
    monkeypatch.setattr(global_user_state, 'get_cluster_yaml_dict',
                        lambda _: {'provider': {}})
    monkeypatch.setattr(cloud_vm_ray_backend.yaml_utils, 'dump_yaml',
                        mock.Mock())
    monkeypatch.setattr(cloud_vm_ray_backend.log_lib, 'run_with_log',
                        teardown_then_replace)
    monkeypatch.setattr(backend, 'post_teardown_cleanup', post_teardown_cleanup)

    backend.teardown_no_lock(stale_handle,
                             terminate=True,
                             expected_cluster_hash=stale_record['cluster_hash'])

    current = global_user_state.get_cluster_from_name(stale_handle.cluster_name,
                                                      include_user_info=False,
                                                      summary_response=True)
    assert current is not None
    assert current['cluster_hash'] == replacement_record['cluster_hash']
    post_teardown_cleanup.assert_not_called()


@pytest.mark.usefixtures('fresh_state_db')
def test_delayed_arm_cannot_overwrite_manual_down_cancellation(monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)
    rpc_started = threading.Event()
    release_rpc = threading.Event()

    def block_arm_but_apply_cancellation(request):
        if request.idle_minutes == -1:
            assert rpc_started.is_set()
            return autostopv1_pb2.SetAutostopResponse(
                supports_durable_autodown=True)
        rpc_started.set()
        assert release_rpc.wait(timeout=10)
        return autostopv1_pb2.SetAutostopResponse(
            supports_durable_autodown=True)

    _, apply_autodown_intent, set_autostop = _patch_skylet(
        monkeypatch, apply_side_effect=block_arm_but_apply_cancellation)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            backend.set_autostop,
            handle,
            15,
            autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
            True,
        )
        assert rpc_started.wait(timeout=10)
        configuring = global_user_state.get_autodown_intent('cluster')
        assert configuring is not None
        with mock.patch.object(backend, 'teardown_no_lock') as teardown:
            backend._teardown(handle, terminate=True)
        release_rpc.set()
        with pytest.raises(RuntimeError, match='superseded'):
            future.result(timeout=10)

    cancellation = global_user_state.get_autodown_intent('cluster')
    assert cancellation is not None
    assert cancellation.generation == configuring.generation + 1
    assert cancellation.state is global_user_state.AutodownIntentState.CANCELLED
    cancel_request = apply_autodown_intent.call_args_list[1].args[0]
    assert cancel_request.idle_minutes == -1
    assert cancel_request.generation == cancellation.generation
    teardown.assert_called_once()
    assert apply_autodown_intent.call_count == 2
    assert set_autostop.call_count == 0


@pytest.mark.usefixtures('fresh_state_db')
@pytest.mark.parametrize(('durable_state', 'expected_intent_state'), [
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED,
     global_user_state.AutodownIntentState.PREPARING),
    (autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
     global_user_state.AutodownIntentState.READY),
])
@pytest.mark.parametrize('terminate', [False, True])
def test_manual_teardown_preserves_predecessor_irreversible_claim(
        monkeypatch, durable_state, expected_intent_state, terminate):
    handle = _make_handle()
    _add_cluster(handle)
    get_status, apply_autodown_intent, _ = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    armed = global_user_state.get_autodown_intent('cluster')
    assert armed is not None
    get_status.return_value = _durable_status(
        cluster_hash=armed.cluster_hash,
        generation=armed.generation,
        durable_execution_state=durable_state,
    )
    apply_autodown_intent.side_effect = RuntimeError('update rejected')

    with mock.patch.object(backend, 'teardown_no_lock') as teardown:
        if terminate:
            backend._teardown(handle, terminate=True)
            teardown.assert_called_once()
        else:
            with pytest.raises(RuntimeError,
                               match='cannot be manually stopped'):
                backend._teardown(handle, terminate=False)
            teardown.assert_not_called()

    current = global_user_state.get_autodown_intent('cluster')
    assert current is not None
    assert current.generation == armed.generation
    assert current.state is expected_intent_state
    assert current.to_down
    cancel_request = apply_autodown_intent.call_args_list[-1].args[0]
    assert cancel_request.idle_minutes == -1
    assert cancel_request.generation == armed.generation + 1


@pytest.mark.usefixtures('fresh_state_db')
@pytest.mark.parametrize('claimed_state', [
    global_user_state.AutodownIntentState.PREPARING,
    global_user_state.AutodownIntentState.READY,
])
@pytest.mark.parametrize('terminate', [False, True])
def test_manual_teardown_does_not_cancel_known_irreversible_intent(
        monkeypatch, claimed_state, terminate):
    handle = _make_handle()
    _add_cluster(handle)
    _, apply_autodown_intent, _ = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    armed = global_user_state.get_autodown_intent('cluster')
    assert armed is not None
    assert global_user_state.compare_and_swap_autodown_intent(
        cluster_name=armed.cluster_name,
        cluster_hash=armed.cluster_hash,
        generation=armed.generation,
        expected_states={global_user_state.AutodownIntentState.ARMED},
        expected_attempt_count=armed.attempt_count,
        new_state=claimed_state,
    )
    claimed = global_user_state.get_autodown_intent('cluster')
    assert claimed is not None

    with mock.patch.object(backend, 'teardown_no_lock') as teardown:
        if terminate:
            backend._teardown(handle, terminate=True)
            teardown.assert_called_once()
        else:
            with pytest.raises(RuntimeError,
                               match='cannot be manually stopped'):
                backend._teardown(handle, terminate=False)
            teardown.assert_not_called()

    assert global_user_state.get_autodown_intent('cluster') == claimed
    assert apply_autodown_intent.call_count == 1


@pytest.mark.usefixtures('fresh_state_db')
def test_name_reuse_hash_mismatch_blocks_delayed_finalization(monkeypatch):
    old_handle = _make_handle()
    old_record = _add_cluster(old_handle)
    rpc_started = threading.Event()
    release_rpc = threading.Event()

    def block_rpc(_request):
        rpc_started.set()
        assert release_rpc.wait(timeout=10)
        return autostopv1_pb2.SetAutostopResponse(
            supports_durable_autodown=True)

    _patch_skylet(monkeypatch, apply_side_effect=block_rpc)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            backend.set_autostop,
            old_handle,
            15,
            autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
            True,
        )
        assert rpc_started.wait(timeout=10)
        global_user_state.remove_cluster('cluster', terminate=True)
        new_handle = _make_handle()
        new_record = _add_cluster(new_handle)
        assert new_record['cluster_hash'] != old_record['cluster_hash']
        release_rpc.set()
        with pytest.raises(RuntimeError, match='incarnation changed'):
            future.result(timeout=10)

    current_record = global_user_state.get_cluster_from_name('cluster')
    assert current_record is not None
    assert current_record['cluster_hash'] == new_record['cluster_hash']
    assert (current_record['autostop'], current_record['to_down']) == (-1,
                                                                       False)
    intent = global_user_state.get_autodown_intent('cluster')
    assert intent is not None
    assert intent.cluster_hash == old_record['cluster_hash']
    assert intent.state is global_user_state.AutodownIntentState.CONFIGURING


@pytest.mark.usefixtures('fresh_state_db')
@pytest.mark.parametrize('old_state', [
    global_user_state.AutodownIntentState.PREPARING,
    global_user_state.AutodownIntentState.READY,
    global_user_state.AutodownIntentState.EXECUTING,
    global_user_state.AutodownIntentState.RETRY_WAIT,
])
def test_old_incarnation_irreversible_intent_does_not_block_new_arm(
        monkeypatch, old_state):
    old_handle = _make_handle()
    old_record = _add_cluster(old_handle)
    _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(old_handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    old_intent = global_user_state.get_autodown_intent('cluster')
    assert old_intent is not None
    assert global_user_state.compare_and_swap_autodown_intent(
        cluster_name=old_intent.cluster_name,
        cluster_hash=old_intent.cluster_hash,
        generation=old_intent.generation,
        expected_states={old_intent.state},
        expected_attempt_count=old_intent.attempt_count,
        new_state=old_state,
    )

    global_user_state.remove_cluster('cluster', terminate=True)
    new_handle = _make_handle(TeardownExecutionStrategy.SERVER_ONLY)
    new_record = _add_cluster(new_handle)
    assert new_record['cluster_hash'] != old_record['cluster_hash']

    backend.set_autostop(new_handle,
                         30,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)

    new_intent = global_user_state.get_autodown_intent('cluster')
    assert new_intent is not None
    assert new_intent.cluster_hash == new_record['cluster_hash']
    assert new_intent.generation == old_intent.generation + 1
    assert new_intent.state is global_user_state.AutodownIntentState.ARMED
    assert new_intent.execution_strategy == 'server_only'


@pytest.mark.usefixtures('fresh_state_db')
def test_old_incarnation_strict_intent_does_not_change_new_legacy_path(
        monkeypatch):
    old_handle = _make_handle()
    _add_cluster(old_handle)
    _, apply_autodown_intent, set_autostop = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    backend.set_autostop(old_handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    old_intent = global_user_state.get_autodown_intent('cluster')
    assert old_intent is not None
    assert global_user_state.compare_and_swap_autodown_intent(
        cluster_name=old_intent.cluster_name,
        cluster_hash=old_intent.cluster_hash,
        generation=old_intent.generation,
        expected_states={old_intent.state},
        expected_attempt_count=old_intent.attempt_count,
        new_state=global_user_state.AutodownIntentState.PREPARING,
    )

    global_user_state.remove_cluster('cluster', terminate=True)
    new_handle = _make_handle(TeardownExecutionStrategy.LEGACY_HEAD_CREDENTIALS)
    new_record = _add_cluster(new_handle)
    assert new_record['cluster_hash'] != old_intent.cluster_hash

    backend.set_autostop(new_handle,
                         20,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)

    assert apply_autodown_intent.call_count == 1
    set_autostop.assert_called_once()
    current_record = global_user_state.get_cluster_from_name('cluster')
    assert current_record is not None
    assert current_record['cluster_hash'] == new_record['cluster_hash']
    assert (current_record['autostop'], current_record['to_down']) == (20, True)


@pytest.mark.usefixtures('fresh_state_db')
def test_stale_handle_probes_current_incarnation_before_mutation(monkeypatch):
    stale_handle = _make_handle()
    stale_handle.cluster_name_on_cloud = 'stale-cluster-cloud'
    _add_cluster(stale_handle)
    global_user_state.remove_cluster('cluster', terminate=True)
    current_handle = _make_handle()
    current_handle.cluster_name_on_cloud = 'current-cluster-cloud'
    current_record = _add_cluster(current_handle)
    _, apply_autodown_intent, _ = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    get_status = mock.Mock(return_value=_durable_status())
    monkeypatch.setattr(backend, 'get_durable_autodown_status', get_status)

    backend.set_autostop(stale_handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)

    get_status.assert_called_once()
    probed_handle = get_status.call_args.args[0]
    assert probed_handle.cluster_name_on_cloud == 'current-cluster-cloud'
    request = apply_autodown_intent.call_args.args[0]
    assert request.cluster_hash == current_record['cluster_hash']


@pytest.mark.usefixtures('fresh_state_db')
def test_legacy_and_ordinary_autostop_remain_unfenced(monkeypatch):
    legacy_handle = _make_handle(
        TeardownExecutionStrategy.LEGACY_HEAD_CREDENTIALS, 'legacy')
    strict_handle = _make_handle(
        TeardownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK, 'ordinary')
    _add_cluster(legacy_handle)
    _add_cluster(strict_handle)
    get_status, apply_autodown_intent, set_autostop = _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()

    backend.set_autostop(legacy_handle,
                         15,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=True)
    backend.set_autostop(strict_handle,
                         20,
                         autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                         down=False)

    get_status.assert_not_called()
    apply_autodown_intent.assert_not_called()
    assert global_user_state.get_autodown_intent('legacy') is None
    assert global_user_state.get_autodown_intent('ordinary') is None
    for call in set_autostop.call_args_list:
        request = call.args[0]
        assert not request.HasField('cluster_hash')
        assert not request.HasField('generation')
        assert not request.HasField('execution_strategy')


@pytest.mark.usefixtures('fresh_state_db')
def test_already_held_status_lock_uses_internal_no_lock_path(monkeypatch):
    handle = _make_handle()
    _add_cluster(handle)
    _patch_skylet(monkeypatch)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    lock = locks.get_lock(
        backend_utils.cluster_status_lock_id(handle.cluster_name))

    with lock:
        backend._set_autostop(
            handle,
            15,
            autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
            down=True,
            cluster_lock_already_held=True,
        )

    intent = global_user_state.get_autodown_intent('cluster')
    assert intent is not None
    assert intent.state is global_user_state.AutodownIntentState.ARMED
