"""Unit tests for sky.serve.autoscalers."""
import unittest
from unittest import mock

from sky.serve import autoscalers
from sky.serve import replica_managers
from sky.serve import serve_state


class TestSelectNonterminalReplicasToScaleDown(unittest.TestCase):
    """Test cases for _select_nonterminal_replicas_to_scale_down."""

    def setUp(self):
        """Set up test fixtures."""
        self.service_name = 'test-service'

        # Create mock ReplicaInfo objects
        self.replica1 = mock.Mock(spec=replica_managers.ReplicaInfo)
        self.replica1.replica_id = 1
        self.replica1.cluster_name = 'test-cluster-1'
        self.replica1.version = 1
        self.replica1.status = serve_state.ReplicaStatus.READY

        self.replica2 = mock.Mock(spec=replica_managers.ReplicaInfo)
        self.replica2.replica_id = 2
        self.replica2.cluster_name = 'test-cluster-2'
        self.replica2.version = 1
        self.replica2.status = serve_state.ReplicaStatus.READY

        self.replica3 = mock.Mock(spec=replica_managers.ReplicaInfo)
        self.replica3.replica_id = 3
        self.replica3.cluster_name = 'test-cluster-3'
        self.replica3.version = 1
        self.replica3.status = serve_state.ReplicaStatus.READY

    @mock.patch('sky.serve.autoscalers.managed_job_state.'
                'get_nonterminal_job_counts_by_pool')
    def test_select_replicas_with_job_counts(self, mock_get_counts):
        """Test that replicas with fewer jobs are selected first."""

        # Mock job counts: replica1 has 2 jobs, replica2 has 0 jobs,
        # replica3 has 1 job
        mock_get_counts.return_value = {
            'test-cluster-1': 2,
            'test-cluster-3': 1,
            # test-cluster-2 absent means 0 jobs
        }

        replica_infos = [self.replica1, self.replica2, self.replica3]

        # Select 2 replicas to scale down
        result = autoscalers._select_nonterminal_replicas_to_scale_down(
            2, replica_infos, self.service_name)

        # Should select replica2 (0 jobs) and replica3 (1 job) first
        # Order should be: replica2 (0 jobs), replica3 (1 job), replica1
        # (2 jobs). Since we're selecting 2, we should get [2, 3]
        self.assertEqual(len(result), 2)
        self.assertEqual(result, [2, 3])

        # Verify the function was called once with the service name
        mock_get_counts.assert_called_once_with(self.service_name)

    @mock.patch('sky.serve.autoscalers.managed_job_state.'
                'get_nonterminal_job_counts_by_pool')
    def test_select_replicas_with_same_job_counts(self, mock_get_counts):
        """Test that when job counts are equal, other sorting criteria apply."""
        # All replicas have the same number of jobs
        mock_get_counts.return_value = {
            'test-cluster-1': 1,
            'test-cluster-2': 1,
            'test-cluster-3': 1,
        }

        replica_infos = [self.replica1, self.replica2, self.replica3]

        # Select 2 replicas to scale down
        result = autoscalers._select_nonterminal_replicas_to_scale_down(
            2, replica_infos, self.service_name)

        # When job counts are equal, should fall back to replica_id
        # descending order. So replica3 (id=3) and replica2 (id=2)
        # should be selected.
        self.assertEqual(len(result), 2)
        self.assertEqual(result, [3, 2])

    @mock.patch('sky.serve.autoscalers.managed_job_state.'
                'get_nonterminal_job_counts_by_pool')
    def test_select_replicas_with_status_priority(self, mock_get_counts):
        """Test that status priority is still respected."""
        # Create replicas with different statuses
        replica_provisioning = mock.Mock(spec=replica_managers.ReplicaInfo)
        replica_provisioning.replica_id = 1
        replica_provisioning.cluster_name = 'test-cluster-1'
        replica_provisioning.version = 1
        replica_provisioning.status = serve_state.ReplicaStatus.PROVISIONING

        replica_ready = mock.Mock(spec=replica_managers.ReplicaInfo)
        replica_ready.replica_id = 2
        replica_ready.cluster_name = 'test-cluster-2'
        replica_ready.version = 1
        replica_ready.status = serve_state.ReplicaStatus.READY

        # PROVISIONING replica has more jobs, but should still be selected
        # first
        mock_get_counts.return_value = {
            'test-cluster-1': 3,
            'test-cluster-2': 1,
        }

        replica_infos = [replica_provisioning, replica_ready]

        # Select 1 replica to scale down
        result = autoscalers._select_nonterminal_replicas_to_scale_down(
            1, replica_infos, self.service_name)

        # Should select PROVISIONING replica first despite having more jobs
        self.assertEqual(len(result), 1)
        self.assertEqual(result, [1])

    @mock.patch('sky.serve.autoscalers.managed_job_state.'
                'get_nonterminal_job_counts_by_pool')
    def test_select_replicas_with_version_priority(self, mock_get_counts):
        """Test that version priority is still respected."""
        # Create replicas with different versions
        replica_old = mock.Mock(spec=replica_managers.ReplicaInfo)
        replica_old.replica_id = 1
        replica_old.cluster_name = 'test-cluster-1'
        replica_old.version = 1
        replica_old.status = serve_state.ReplicaStatus.READY

        replica_new = mock.Mock(spec=replica_managers.ReplicaInfo)
        replica_new.replica_id = 2
        replica_new.cluster_name = 'test-cluster-2'
        replica_new.version = 2
        replica_new.status = serve_state.ReplicaStatus.READY

        # New version replica has fewer jobs, but old version should be
        # selected first
        mock_get_counts.return_value = {
            'test-cluster-1': 2,
            # test-cluster-2 absent means 0 jobs
        }

        replica_infos = [replica_old, replica_new]

        # Select 1 replica to scale down
        result = autoscalers._select_nonterminal_replicas_to_scale_down(
            1, replica_infos, self.service_name)

        # Should select old version replica first despite having more jobs
        self.assertEqual(len(result), 1)
        self.assertEqual(result, [1])


class TestAutoscalerLatestVersionRestore(unittest.TestCase):
    """Tests for latest_version restoration on controller restart (issue #8562).

    On restart, Autoscaler.__init__ must read the persisted version from the DB
    rather than resetting to INITIAL_VERSION, which would cause scale-churn and
    version-guard failures.
    """

    def _make_spec(self, min_replicas: int = 1):
        spec = mock.MagicMock()
        spec.min_replicas = min_replicas
        spec.max_replicas = min_replicas
        spec.num_overprovision = None
        spec.upscale_delay_seconds = None
        spec.downscale_delay_seconds = None
        spec.qps_upper_threshold = None
        spec.qps_lower_threshold = None
        return spec

    @mock.patch('sky.serve.autoscalers.serve_state.get_latest_version')
    def test_init_restores_version_from_db(self, mock_get_version):
        """Autoscaler.__init__ must use the DB version, not INITIAL_VERSION."""
        mock_get_version.return_value = 5

        from sky.serve import constants as serve_constants
        from sky.serve.autoscalers import RequestRateAutoscaler
        autoscaler = RequestRateAutoscaler('svc', self._make_spec())

        mock_get_version.assert_called_once_with('svc')
        self.assertEqual(autoscaler.latest_version, 5)
        # latest_version_ever_ready should track latest_version - 1
        self.assertEqual(autoscaler.latest_version_ever_ready, 4)

    @mock.patch('sky.serve.autoscalers.serve_state.get_latest_version')
    def test_init_falls_back_to_initial_version_when_db_empty(
            self, mock_get_version):
        """When the DB has no version yet, fall back to INITIAL_VERSION."""
        mock_get_version.return_value = None

        from sky.serve import constants as serve_constants
        from sky.serve.autoscalers import RequestRateAutoscaler
        autoscaler = RequestRateAutoscaler('svc', self._make_spec())

        self.assertEqual(autoscaler.latest_version,
                         serve_constants.INITIAL_VERSION)

    @mock.patch('sky.serve.autoscalers.serve_state.get_latest_version')
    def test_dump_and_load_dynamic_states_roundtrip(self, mock_get_version):
        """dump/load_dynamic_states must preserve latest_version."""
        mock_get_version.return_value = 3

        from sky.serve.autoscalers import RequestRateAutoscaler
        autoscaler = RequestRateAutoscaler('svc', self._make_spec())
        autoscaler.latest_version_ever_ready = 3

        states = autoscaler.dump_dynamic_states()
        self.assertIn('latest_version', states)
        self.assertEqual(states['latest_version'], 3)

        # Simulate type-change: new autoscaler inherits state via load
        mock_get_version.return_value = None
        new_autoscaler = RequestRateAutoscaler('svc', self._make_spec())
        new_autoscaler.load_dynamic_states(states)

        self.assertEqual(new_autoscaler.latest_version, 3)
        self.assertEqual(new_autoscaler.latest_version_ever_ready, 3)

    @mock.patch('sky.serve.autoscalers.serve_state.get_latest_version')
    def test_load_dynamic_states_backwards_compat_no_latest_version_key(
            self, mock_get_version):
        """load_dynamic_states must not crash on old dumps lacking the key."""
        mock_get_version.return_value = 7

        from sky.serve.autoscalers import RequestRateAutoscaler
        autoscaler = RequestRateAutoscaler('svc', self._make_spec())

        # Simulate an old dump that only has latest_version_ever_ready
        old_states = autoscaler._dump_dynamic_states()
        old_states['latest_version_ever_ready'] = 6
        # No 'latest_version' key — old format

        autoscaler.load_dynamic_states(old_states)
        # Falls back to DB-restored value (7) set in __init__
        self.assertEqual(autoscaler.latest_version, 7)
        self.assertEqual(autoscaler.latest_version_ever_ready, 6)


if __name__ == '__main__':
    unittest.main()
