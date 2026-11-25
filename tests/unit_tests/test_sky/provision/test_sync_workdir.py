"""Optional provider workdir transport routing."""
import types
from unittest import mock

import pytest

from sky import provision


def test_sync_workdir_defaults_to_rsync(monkeypatch):
    monkeypatch.setitem(provision.__dict__, 'test_workdir',
                        types.SimpleNamespace())
    assert provision.sync_workdir('test_workdir', [], 'source', 'target',
                                  'log') is False


def test_sync_workdir_routes_registered_handler(monkeypatch):
    handler = mock.Mock(return_value=True)
    plugin = types.SimpleNamespace(module=types.SimpleNamespace(
        sync_workdir=handler))
    monkeypatch.setitem(provision._registered_provisioners, 'test_workdir',
                        plugin)
    runners = [object()]
    assert provision.sync_workdir('test_workdir', runners, 'source', 'target',
                                  'log') is True
    handler.assert_called_once_with(runners, 'source', 'target', 'log')


def test_sync_workdir_propagates_transport_errors(monkeypatch):
    handler = mock.Mock(side_effect=RuntimeError('upload started'))
    plugin = types.SimpleNamespace(module=types.SimpleNamespace(
        sync_workdir=handler))
    monkeypatch.setitem(provision._registered_provisioners, 'test_workdir',
                        plugin)
    with pytest.raises(RuntimeError, match='upload started'):
        provision.sync_workdir('test_workdir', [], 'source', 'target', 'log')


@pytest.mark.parametrize('handled', [False, True])
def test_backend_uses_transport_before_rsync(tmp_path, monkeypatch, handled):
    from sky.backends import cloud_vm_ray_backend as backend

    runner = mock.Mock()
    handle = mock.Mock()
    handle.get_command_runners.return_value = [runner]
    handle.external_ips.return_value = ['127.0.0.1']
    handle.launched_nodes = 1
    handle.launched_resources.cloud = 'test_workdir'
    owner = types.SimpleNamespace(log_dir=str(tmp_path / 'logs'))
    transport = mock.Mock(return_value=handled)
    monkeypatch.setattr(provision, 'sync_workdir', transport)
    monkeypatch.setattr(backend.subprocess_utils, 'get_parallel_threads',
                        lambda _: 1)
    monkeypatch.setattr(
        backend.subprocess_utils, 'run_in_parallel',
        lambda fn, runners, _: [fn(runner) for runner in runners])
    backend.CloudVmRayBackend._sync_path_workdir(owner, handle, str(tmp_path))
    transport.assert_called_once()
    assert runner.rsync.call_count == (0 if handled else 1)
