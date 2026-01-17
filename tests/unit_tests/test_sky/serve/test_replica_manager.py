import threading

import pytest

from sky import backends
from sky import global_user_state
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.utils import status_lib


def _make_replica_info(replica_id: int,
                       is_scale_down: bool,
                       purged: bool,
                       *,
                       is_spot: bool = False,
                       version: int = 3) -> replica_managers.ReplicaInfo:
    info = replica_managers.ReplicaInfo(replica_id=replica_id,
                                        cluster_name=f'replica-{replica_id}',
                                        replica_port='8080',
                                        is_spot=is_spot,
                                        location=None,
                                        version=version,
                                        resources_override=None)
    info.status_property.is_scale_down = is_scale_down
    info.status_property.purged = purged
    return info


def _make_manager() -> replica_managers.SkyPilotReplicaManager:
    manager = object.__new__(replica_managers.SkyPilotReplicaManager)
    manager.lock = threading.Lock()
    manager._service_name = 'svc'
    manager._launch_thread_pool = {}
    manager._down_thread_pool = {}
    manager._replica_to_request_id = {}
    manager._replica_to_launch_cancelled = {}
    manager._is_pool = False
    manager.latest_version = 3
    return manager


def test_recovery_syncs_logs_for_left_in_record(monkeypatch):
    """Recovery syncs logs only for replicas kept in the record."""
    manager = _make_manager()
    calls = {}

    def fake_terminate(replica_id,
                       sync_down_logs,
                       replica_drain_delay_seconds,
                       is_scale_down=False,
                       purge=False,
                       is_recovery=False):
        calls[replica_id] = {
            'sync_down_logs': sync_down_logs,
            'is_scale_down': is_scale_down,
            'purge': purge,
            'is_recovery': is_recovery,
        }

    manager._terminate_replica = fake_terminate
    manager._launch_replica = lambda *args, **kwargs: None

    left_in_record = _make_replica_info(1, is_scale_down=False, purged=False)
    scale_down = _make_replica_info(2, is_scale_down=True, purged=False)

    def fake_get_replicas_at_status(service_name, status):
        if status == serve_state.ReplicaStatus.SHUTTING_DOWN:
            return [left_in_record, scale_down]
        return []

    monkeypatch.setattr(serve_state, 'get_replicas_at_status',
                        fake_get_replicas_at_status)

    manager._recover_replica_operations()

    assert calls[1]['sync_down_logs'] is True
    assert calls[1]['is_recovery'] is True
    assert calls[2]['sync_down_logs'] is False
    assert calls[2]['is_recovery'] is True


def test_recovery_launches_pending_and_provisioning(monkeypatch):
    """Recovery relaunches replicas stuck in PENDING/PROVISIONING."""
    manager = _make_manager()
    launched = []

    def fake_launch(replica_id, resources_override=None):
        launched.append(replica_id)

    manager._launch_replica = fake_launch
    manager._terminate_replica = lambda *args, **kwargs: None

    provisioning = _make_replica_info(1, is_scale_down=False, purged=False)
    pending = _make_replica_info(2, is_scale_down=False, purged=False)

    def fake_get_replicas_at_status(service_name, status):
        if status == serve_state.ReplicaStatus.PROVISIONING:
            return [provisioning]
        if status == serve_state.ReplicaStatus.PENDING:
            return [pending]
        return []

    monkeypatch.setattr(serve_state, 'get_replicas_at_status',
                        fake_get_replicas_at_status)

    manager._recover_replica_operations()

    assert launched == [1, 2]


def test_recovery_removes_orphaned_replica(monkeypatch):
    """Recovery drops orphaned replicas when the cluster is missing."""
    manager = _make_manager()
    info = _make_replica_info(1, is_scale_down=False, purged=False)
    removed = []

    monkeypatch.setattr(serve_state, 'get_replica_info_from_id',
                        lambda service_name, replica_id: info)
    monkeypatch.setattr(global_user_state, 'cluster_with_name_exists',
                        lambda name: False)

    def fake_remove(service_name, replica_id):
        removed.append(replica_id)

    def fail_handle(*args, **kwargs):
        raise AssertionError('Unexpected sky down finish in recovery')

    manager._handle_sky_down_finish = fail_handle
    monkeypatch.setattr(serve_state, 'remove_replica', fake_remove)

    manager._terminate_replica(replica_id=1,
                               sync_down_logs=True,
                               replica_drain_delay_seconds=0,
                               is_scale_down=False,
                               purge=False,
                               is_recovery=True)

    assert removed == [1]


def test_scale_down_schedules_termination(monkeypatch):
    """Scale down enqueues a termination thread for existing clusters."""
    manager = _make_manager()
    info = _make_replica_info(1, is_scale_down=False, purged=False)
    updated = []

    monkeypatch.setattr(serve_state, 'get_replica_info_from_id',
                        lambda service_name, replica_id: info)
    monkeypatch.setattr(global_user_state, 'cluster_with_name_exists',
                        lambda name: True)

    def fake_update(service_name, replica_id, replica_info):
        updated.append(replica_info)

    monkeypatch.setattr(serve_state, 'add_or_update_replica', fake_update)

    manager._terminate_replica(replica_id=1,
                               sync_down_logs=False,
                               replica_drain_delay_seconds=0,
                               is_scale_down=True,
                               purge=False)

    assert 1 in manager._down_thread_pool
    assert updated[0].status_property.sky_down_status is not None


def test_orphaned_non_recovery_calls_handle_finish(monkeypatch):
    """Non-recovery path delegates missing clusters to finish handling."""
    manager = _make_manager()
    info = _make_replica_info(1, is_scale_down=False, purged=False)
    calls = []

    monkeypatch.setattr(serve_state, 'get_replica_info_from_id',
                        lambda service_name, replica_id: info)
    monkeypatch.setattr(global_user_state, 'cluster_with_name_exists',
                        lambda name: False)

    def fake_handle(replica_info, format_exc=None):
        calls.append(replica_info)

    manager._handle_sky_down_finish = fake_handle

    manager._terminate_replica(replica_id=1,
                               sync_down_logs=False,
                               replica_drain_delay_seconds=0,
                               is_scale_down=True,
                               purge=False,
                               is_recovery=False)

    assert calls == [info]


@pytest.mark.parametrize('version,purged,failed_spot_availability', [
    (2, False, False),
    (3, True, False),
    (3, False, True),
])
def test_handle_sky_down_finish_removes_replica(monkeypatch, version, purged,
                                                failed_spot_availability):
    """Finished downs remove outdated, purged, or spot-failure replicas."""
    manager = _make_manager()
    info = _make_replica_info(1,
                              is_scale_down=False,
                              purged=purged,
                              version=version)
    info.status_property.failed_spot_availability = failed_spot_availability
    removed = []
    updated = []

    def fake_remove(service_name, replica_id):
        removed.append(replica_id)

    def fake_update(service_name, replica_id, replica_info):
        updated.append(replica_info)

    monkeypatch.setattr(serve_state, 'remove_replica', fake_remove)
    monkeypatch.setattr(serve_state, 'add_or_update_replica', fake_update)

    manager._handle_sky_down_finish(info, format_exc=None)

    assert removed == [1]
    assert updated == []


def test_handle_sky_down_finish_keeps_failed_replica(monkeypatch):
    """Finished downs keep replicas when failures should be retained."""
    manager = _make_manager()
    info = _make_replica_info(1, is_scale_down=False, purged=False)
    removed = []
    updated = []

    def fake_remove(service_name, replica_id):
        removed.append(replica_id)

    def fake_update(service_name, replica_id, replica_info):
        updated.append(replica_info)

    monkeypatch.setattr(serve_state, 'remove_replica', fake_remove)
    monkeypatch.setattr(serve_state, 'add_or_update_replica', fake_update)

    manager._handle_sky_down_finish(info, format_exc=None)

    assert removed == []
    assert updated == [info]


def test_handle_preemption_marks_replica_and_scales_down(monkeypatch):
    """Preemption marks replica and triggers scale-down termination."""
    manager = _make_manager()
    manager._spot_placer = None
    info = _make_replica_info(1,
                              is_scale_down=False,
                              purged=False,
                              is_spot=True)
    updated = []
    term_calls = []

    class DummyHandle:
        pass

    monkeypatch.setattr(backends, 'CloudVmRayResourceHandle', DummyHandle)
    monkeypatch.setattr(global_user_state, 'get_handle_from_cluster_name',
                        lambda name: DummyHandle())
    monkeypatch.setattr(replica_managers.backend_utils,
                        'refresh_cluster_status_handle',
                        lambda name, force_refresh_statuses=None:
                        (status_lib.ClusterStatus.STOPPED, None))

    def fake_update(service_name, replica_id, replica_info):
        updated.append(replica_info)

    def fake_terminate(replica_id,
                       sync_down_logs,
                       replica_drain_delay_seconds,
                       is_scale_down=False,
                       purge=False,
                       is_recovery=False):
        term_calls.append({
            'replica_id': replica_id,
            'sync_down_logs': sync_down_logs,
            'is_scale_down': is_scale_down,
        })

    monkeypatch.setattr(serve_state, 'add_or_update_replica', fake_update)
    manager._terminate_replica = fake_terminate

    assert manager._handle_preemption(info) is True
    assert info.status_property.preempted is True
    assert updated == [info]
    assert term_calls == [{
        'replica_id': 1,
        'sync_down_logs': False,
        'is_scale_down': True,
    }]
