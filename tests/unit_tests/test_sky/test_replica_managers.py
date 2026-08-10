"""Tests for replica manager probe configuration behavior."""
from typing import Optional
from unittest import mock

from sky.serve import replica_managers
from sky.serve import service_spec


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
