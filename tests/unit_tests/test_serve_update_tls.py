"""Regression tests for persisted TLS configuration on service updates."""
import contextlib
from unittest import mock

import pytest

from sky import backends
from sky import task as task_lib
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.serve.server import impl
from sky.utils import yaml_utils


@pytest.mark.parametrize('encrypted', [True, False])
@pytest.mark.parametrize('submitted_tls', [True, False])
def test_update_preserves_installed_tls(encrypted, submitted_tls):
    service = {'readiness_probe': '/healthz', 'replicas': 1}
    if submitted_tls:
        service['tls'] = {
            'keyfile': '/tmp/upload/key',
            'certfile': '/tmp/upload/cert'
        }
    task = task_lib.Task.from_yaml_config({
        'service': service,
        'resources': {
            'ports': 8080
        },
        'run': 'python app.py'
    })
    handle = mock.MagicMock(spec=backends.CloudVmRayResourceHandle)
    handle.is_grpc_enabled_with_flag = True
    backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    persisted = []

    def capture_yaml(_handle, mounts, storage_mounts):
        del storage_mounts
        persisted.append(yaml_utils.read_yaml(next(iter(mounts.values()))))

    backend.sync_file_mounts.side_effect = capture_yaml
    record = {
        'status': serve_state.ServiceStatus.READY,
        'tls_encrypted': encrypted,
        'load_balancing_policy': task.service.load_balancing_policy
    }
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(task, 'validate'))
        stack.enter_context(
            mock.patch.object(impl.serve_utils, 'validate_service_task'))
        stack.enter_context(
            mock.patch.object(impl.backend_utils,
                              'is_controller_accessible',
                              return_value=handle))
        stack.enter_context(
            mock.patch.object(impl.backend_utils,
                              'get_backend_from_handle',
                              return_value=backend))
        stack.enter_context(
            mock.patch.object(impl, '_get_service_record', return_value=record))
        stack.enter_context(
            mock.patch.object(impl.admin_policy_utils,
                              'apply',
                              return_value=(mock.Mock(tasks=[task]), None)))
        stack.enter_context(
            mock.patch.object(impl.controller_utils,
                              'maybe_translate_local_file_mounts_and_sync_up'))
        stack.enter_context(
            mock.patch.object(impl.serve_rpc_utils.RpcRunner,
                              'add_version',
                              return_value=2))
        stack.enter_context(
            mock.patch.object(impl.serve_rpc_utils.RpcRunner, 'update_service'))
        impl.update(task, 'test-service')

    tls = persisted[0]['service'].get('tls')
    if encrypted:
        assert tls == {
            'keyfile':
                serve_utils.generate_remote_tls_keyfile_name('test-service'),
            'certfile':
                serve_utils.generate_remote_tls_certfile_name('test-service'),
        }
    else:
        assert tls is None
