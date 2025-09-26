"""Unit tests for the SkyServe replica port forward manager."""

from __future__ import annotations

import itertools

from sky.serve import replica_managers


class _DummyProcess:
    """Simple process stub that mimics the Popen interface used in tests."""

    def __init__(self) -> None:
        self.stdout = None
        self.stderr = None
        self.stdin = None
        self._terminated = False

    def poll(self):
        return None if not self._terminated else 0

    def terminate(self):
        self._terminated = True

    def wait(self, timeout=None):  # pylint: disable=unused-argument
        return 0

    def kill(self):
        self._terminated = True


def _make_replica_info(replica_id: int = 1,
                       replica_port: str = '8000') -> replica_managers.ReplicaInfo:
    return replica_managers.ReplicaInfo(replica_id=replica_id,
                                        cluster_name=f'cluster-{replica_id}',
                                        replica_port=replica_port,
                                        is_spot=False,
                                        location=None,
                                        version=1,
                                        resources_override=None)


def test_ensure_port_forward_sets_local_url_and_reuses_existing(monkeypatch):
    manager = replica_managers._ReplicaPortForwardManager()
    replica_info = _make_replica_info()

    dummy_process = _DummyProcess()
    call_count = {'value': 0}

    def fake_find_free_port(_start: int) -> int:  # pylint: disable=unused-argument
        return 45000

    def fake_start(self, *, info, local_port, remote_port):  # noqa: D401
        call_count['value'] += 1
        assert info is replica_info
        assert local_port == 45000
        assert remote_port == 8000
        return dummy_process

    monkeypatch.setattr(replica_managers.common_utils, 'find_free_port',
                        fake_find_free_port)
    monkeypatch.setattr(replica_managers._ReplicaPortForwardManager,
                        '_start_port_forward_process', fake_start)

    url = manager.ensure_port_forward(replica_info)
    assert url == 'http://127.0.0.1:45000'
    assert replica_info.url == url
    assert call_count['value'] == 1

    # Reusing the tunnel should not trigger another start.
    assert manager.ensure_port_forward(replica_info) == url
    assert call_count['value'] == 1


def test_ensure_port_forward_replaces_when_remote_port_changes(monkeypatch):
    manager = replica_managers._ReplicaPortForwardManager()
    replica_info = _make_replica_info()

    first_process = _DummyProcess()
    second_process = _DummyProcess()
    ports = itertools.cycle([45000, 45001])
    call_args = []
    terminated = []

    def fake_find_free_port(_start: int) -> int:  # pylint: disable=unused-argument
        return next(ports)

    def fake_start(self, *, info, local_port, remote_port):
        call_args.append((local_port, remote_port))
        assert info is replica_info
        return first_process if len(call_args) == 1 else second_process

    def fake_terminate(self, tunnel):
        terminated.append(tunnel)

    monkeypatch.setattr(replica_managers.common_utils, 'find_free_port',
                        fake_find_free_port)
    monkeypatch.setattr(replica_managers._ReplicaPortForwardManager,
                        '_start_port_forward_process', fake_start)
    monkeypatch.setattr(replica_managers._ReplicaPortForwardManager,
                        '_terminate_port_forward_process', fake_terminate)

    url_first = manager.ensure_port_forward(replica_info)
    assert url_first == 'http://127.0.0.1:45000'

    replica_info.replica_port = '9000'
    url_second = manager.ensure_port_forward(replica_info)
    assert url_second == 'http://127.0.0.1:45001'

    assert call_args == [(45000, 8000), (45001, 9000)]
    assert terminated and terminated[0].remote_port == 8000
    assert replica_info.url == url_second

    # The replacement tunnel should be reused without another start.
    assert manager.ensure_port_forward(replica_info) == url_second
    assert len(call_args) == 2


def test_ensure_port_forward_handles_invalid_remote_port(monkeypatch):
    manager = replica_managers._ReplicaPortForwardManager()
    info = _make_replica_info(replica_port='invalid')

    def fail_start(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError('Should not attempt to start tunnel')

    monkeypatch.setattr(replica_managers._ReplicaPortForwardManager,
                        '_start_port_forward_process', fail_start)

    url = manager.ensure_port_forward(info)
    assert url is None
    assert info.url is None
    assert not manager.ensure_port_forward(info)


def test_cleanup_stale_invokes_stop_for_missing_replicas(monkeypatch):
    manager = replica_managers._ReplicaPortForwardManager()
    manager._replica_port_forwards[1] = replica_managers._ReplicaPortForward(  # pylint: disable=protected-access
        local_port=45000,
        remote_port=8000,
        process=_DummyProcess(),
        cluster_name='cluster-1')
    manager._replica_port_forwards[2] = replica_managers._ReplicaPortForward(  # pylint: disable=protected-access
        local_port=45001,
        remote_port=8001,
        process=_DummyProcess(),
        cluster_name='cluster-2')

    stopped = []

    def fake_stop(self, replica_id):
        stopped.append(replica_id)
        manager._replica_port_forwards.pop(replica_id, None)  # pylint: disable=protected-access

    monkeypatch.setattr(replica_managers._ReplicaPortForwardManager, 'stop',
                        fake_stop)

    manager.cleanup_stale({1})
    assert stopped == [2]
    assert 2 not in manager._replica_port_forwards  # pylint: disable=protected-access
    assert 1 in manager._replica_port_forwards  # pylint: disable=protected-access


def test_stop_terminates_and_removes_port_forward(monkeypatch):
    manager = replica_managers._ReplicaPortForwardManager()
    tunnel = replica_managers._ReplicaPortForward(local_port=45000,
                                                  remote_port=8000,
                                                  process=_DummyProcess(),
                                                  cluster_name='cluster-1')
    manager._replica_port_forwards[1] = tunnel  # pylint: disable=protected-access

    terminated = []

    def fake_terminate(self, forward):
        terminated.append(forward)

    monkeypatch.setattr(replica_managers._ReplicaPortForwardManager,
                        '_terminate_port_forward_process', fake_terminate)

    manager.stop(1)
    assert terminated == [tunnel]
    assert 1 not in manager._replica_port_forwards  # pylint: disable=protected-access
