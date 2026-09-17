"""Tests for replica manager probe configuration behavior."""
from typing import Optional
from unittest import mock

from sky import backends
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.serve import service_spec
from sky.utils import common_utils
from sky.utils import status_lib


class TestSkypilotReplicaManager:

    @staticmethod
    def _make_spec(
        pool: bool,
        consecutive_failure_threshold_timeout: Optional[int] = None
    ) -> service_spec.SkyServiceSpec:
        config = {'readiness_probe': {'path': '/health',}}
        if consecutive_failure_threshold_timeout:
            config['readiness_probe'][
                'consecutive_failure_threshold_timeout'] = (
                    consecutive_failure_threshold_timeout)
        if pool:
            config['pool'] = {
                'min_workers': 1,
                'max_workers': 5,
            }
        else:
            config['replicas'] = 1
        return service_spec.SkyServiceSpec.from_yaml_config(config)

    @staticmethod
    def _build_mock_skypilot_replica_manager(spec):
        mock_replica_manager = mock.MagicMock(
            spec=replica_managers.SkyPilotReplicaManager)
        mock_replica_manager._get_version_spec.return_value = spec
        mock_replica_manager._is_pool = spec.pool
        mock_replica_manager.latest_version = 1
        mock_replica_manager._consecutive_failure_threshold_timeout.side_effect = (
            lambda: replica_managers.SkyPilotReplicaManager.
            _consecutive_failure_threshold_timeout(mock_replica_manager))
        return mock_replica_manager

    def test_consecutive_failure_threshold_timeout_uses_non_pool_default(self):
        spec = self._make_spec(pool=False)
        manager = self._build_mock_skypilot_replica_manager(spec)

        assert manager._consecutive_failure_threshold_timeout() == 180

    def test_consecutive_failure_threshold_timeout_uses_pool_default(self):
        spec = self._make_spec(pool=True)
        manager = self._build_mock_skypilot_replica_manager(spec)

        assert manager._consecutive_failure_threshold_timeout() == 10

    def test_consecutive_failure_threshold_timeout_uses_config_override(self):
        spec = self._make_spec(pool=True,
                               consecutive_failure_threshold_timeout=25)
        manager = self._build_mock_skypilot_replica_manager(spec)

        assert manager._consecutive_failure_threshold_timeout() == 25


class TestLaunchProgressDetail:
    """The parenthesized detail of a LAUNCH_PROGRESS event reason is what a
    PROVISIONING replica surfaces as its status detail."""

    def test_extracts_detail(self):
        assert replica_managers.launch_progress_detail(
            'Launching (waiting for queue admission)') == (
                'waiting for queue admission')

    def test_reason_without_detail_is_dropped(self):
        # Would only duplicate the PROVISIONING status.
        assert replica_managers.launch_progress_detail(
            'Provisioning on kubernetes in my-ctx') is None

    def test_none(self):
        assert replica_managers.launch_progress_detail(None) is None


def _make_replica_info(replica_id: int = 1,
                       is_spot: bool = False) -> replica_managers.ReplicaInfo:
    return replica_managers.ReplicaInfo(replica_id=replica_id,
                                        cluster_name=f'svc-{replica_id}',
                                        replica_port='8080',
                                        is_spot=is_spot,
                                        location=None,
                                        version=1,
                                        resources_override=None)


class TestToInfoDictStatusDetail:

    @staticmethod
    def _cluster_record():
        return {'launched_at': 1.0, 'cluster_hash': 'h', 'handle': None}

    def test_provisioning_replica_exposes_detail(self):
        info = _make_replica_info()
        info.status_property.sky_launch_status = (
            common_utils.ProcessStatus.RUNNING)
        result = info.to_info_dict(
            with_handle=False,
            with_url=False,
            cluster_record=self._cluster_record(),
            launch_progress='Launching (waiting for queue admission)')
        assert result['status'] == serve_state.ReplicaStatus.PROVISIONING
        assert result['status_detail'] == 'waiting for queue admission'

    def test_no_detail_without_launch_progress(self):
        info = _make_replica_info()
        info.status_property.sky_launch_status = (
            common_utils.ProcessStatus.RUNNING)
        result = info.to_info_dict(with_handle=False,
                                   with_url=False,
                                   cluster_record=self._cluster_record())
        assert 'status_detail' not in result

    def test_ready_replica_ignores_launch_progress(self):
        # A stale launch-progress event must not decorate a replica that
        # has finished launching.
        info = _make_replica_info()
        info.status_property.sky_launch_status = (
            common_utils.ProcessStatus.SUCCEEDED)
        info.status_property.service_ready_now = True
        result = info.to_info_dict(
            with_handle=False,
            with_url=False,
            cluster_record=self._cluster_record(),
            launch_progress='Launching (waiting for queue admission)')
        assert result['status'] == serve_state.ReplicaStatus.READY
        assert 'status_detail' not in result


class TestHandlePreemption:
    """Externally terminated replicas are recycled, spot or not.

    Before, only spot replicas were checked; a non-spot pool worker whose pod
    Kueue evicted for a higher-priority workload lingered as FAILED_PROBING.
    """

    def _make_manager(self, monkeypatch, cluster_status, spot_placer=None):
        manager = object.__new__(replica_managers.SkyPilotReplicaManager)
        manager._service_name = 'svc'
        manager._is_pool = True
        manager._spot_placer = spot_placer
        manager._last_termination_check = {}
        manager._terminate_replica = mock.MagicMock()

        handle = mock.MagicMock(spec=backends.CloudVmRayResourceHandle)
        monkeypatch.setattr(replica_managers.global_user_state,
                            'get_handle_from_cluster_name',
                            lambda cluster_name: handle)
        refresh = mock.MagicMock(return_value=(cluster_status, handle))
        monkeypatch.setattr(replica_managers.backend_utils,
                            'refresh_cluster_status_handle', refresh)
        monkeypatch.setattr(replica_managers.serve_state,
                            'add_or_update_replica', mock.MagicMock())
        return manager, refresh

    def test_non_spot_replica_with_missing_cluster_is_recycled(
            self, monkeypatch):
        manager, refresh = self._make_manager(monkeypatch, cluster_status=None)
        info = _make_replica_info(is_spot=False)
        info.status_property.first_ready_time = 100.0

        assert manager._handle_preemption(info) is True
        assert refresh.call_count == 1
        assert info.status_property.preempted is True
        manager._terminate_replica.assert_called_once_with(
            info.replica_id,
            sync_down_logs=False,
            replica_drain_delay_seconds=0,
            is_scale_down=True)

    def test_non_spot_replica_with_up_cluster_is_kept(self, monkeypatch):
        manager, refresh = self._make_manager(
            monkeypatch, cluster_status=status_lib.ClusterStatus.UP)
        info = _make_replica_info(is_spot=False)
        info.status_property.first_ready_time = 100.0

        assert manager._handle_preemption(info) is False
        assert refresh.call_count == 1
        assert info.status_property.preempted is False
        manager._terminate_replica.assert_not_called()

    def test_never_ready_non_spot_replica_is_rate_limited(self, monkeypatch):
        manager, refresh = self._make_manager(
            monkeypatch, cluster_status=status_lib.ClusterStatus.UP)
        info = _make_replica_info(is_spot=False)
        assert info.status_property.first_ready_time is None

        now = 1000.0
        monkeypatch.setattr(replica_managers.time, 'time', lambda: now)
        assert manager._handle_preemption(info) is False
        assert refresh.call_count == 1

        # A failed probe a few seconds later must not hit the cloud again.
        now += 10.0
        assert manager._handle_preemption(info) is False
        assert refresh.call_count == 1

        # Once the interval has passed the cluster is checked again.
        now += (
            replica_managers._NEVER_READY_TERMINATION_CHECK_INTERVAL_SECONDS)
        assert manager._handle_preemption(info) is False
        assert refresh.call_count == 2

    def test_never_ready_non_spot_replica_is_recycled_when_gone(
            self, monkeypatch):
        # A worker evicted while its setup was still running must not wait
        # out the initial delay and freeze the autoscaler as
        # FAILED_INITIAL_DELAY.
        manager, _ = self._make_manager(monkeypatch, cluster_status=None)
        info = _make_replica_info(is_spot=False)

        assert manager._handle_preemption(info) is True
        assert info.status_property.preempted is True

    def test_previously_ready_replica_is_checked_every_time(self, monkeypatch):
        manager, refresh = self._make_manager(
            monkeypatch, cluster_status=status_lib.ClusterStatus.UP)
        info = _make_replica_info(is_spot=False)
        info.status_property.first_ready_time = 100.0

        now = 1000.0
        monkeypatch.setattr(replica_managers.time, 'time', lambda: now)
        manager._handle_preemption(info)
        now += 1.0
        manager._handle_preemption(info)
        assert refresh.call_count == 2

    def test_spot_replica_is_checked_every_time(self, monkeypatch):
        manager, refresh = self._make_manager(
            monkeypatch, cluster_status=status_lib.ClusterStatus.UP)
        info = _make_replica_info(is_spot=True)

        now = 1000.0
        monkeypatch.setattr(replica_managers.time, 'time', lambda: now)
        manager._handle_preemption(info)
        now += 1.0
        manager._handle_preemption(info)
        assert refresh.call_count == 2

    def test_missing_cluster_record_counts_as_terminated(self, monkeypatch):
        # A status refresh (e.g. the API server's periodic one) can drop the
        # cluster record before the probe runs; the replica must still be
        # recycled rather than skipped into FAILED_PROBING.
        manager, refresh = self._make_manager(
            monkeypatch, cluster_status=status_lib.ClusterStatus.UP)
        monkeypatch.setattr(replica_managers.global_user_state,
                            'get_handle_from_cluster_name',
                            lambda cluster_name: None)
        info = _make_replica_info(is_spot=False)
        info.status_property.first_ready_time = 100.0

        assert manager._handle_preemption(info) is True
        refresh.assert_not_called()
        assert info.status_property.preempted is True
        manager._terminate_replica.assert_called_once()

    def test_spot_placer_only_notified_for_spot_replicas(self, monkeypatch):
        placer = mock.MagicMock()
        manager, _ = self._make_manager(monkeypatch,
                                        cluster_status=None,
                                        spot_placer=placer)
        info = _make_replica_info(is_spot=False)
        info.status_property.first_ready_time = 100.0

        assert manager._handle_preemption(info) is True
        placer.set_preemptive.assert_not_called()
