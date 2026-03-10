"""Unit tests for managed jobs Prometheus metrics."""
from unittest.mock import patch

from sky.metrics import utils as metrics_utils
from sky.server import metrics


class TestManagedJobsCollector:
    """Tests for ManagedJobsCollector."""

    def test_collect_returns_all_metric_families(self):
        """Verify collect() yields the expected metric families."""
        mock_status_counts = {'RUNNING': 3, 'PENDING': 2, 'SUCCEEDED': 10}
        mock_schedule_counts = {'ALIVE': 3, 'DONE': 10, 'WAITING': 1}
        mock_recovery_counts = {(42, 0): 3, (99, 1): 1}

        collector = metrics.ManagedJobsCollector()
        with patch.object(collector, '_refresh') as mock_refresh:

            def side_effect():
                collector._cached_status_counts = mock_status_counts
                collector._cached_schedule_state_counts = (mock_schedule_counts)
                collector._cached_recovery_counts = mock_recovery_counts

            mock_refresh.side_effect = side_effect

            families = list(collector.collect())

        assert len(families) == 3

        # Check status metric
        status_family = families[0]
        assert status_family.name == 'sky_managed_jobs_by_status'
        status_samples = {
            s.labels['status']: s.value for s in status_family.samples
        }
        assert status_samples == {'RUNNING': 3, 'PENDING': 2, 'SUCCEEDED': 10}

        # Check schedule state metric
        schedule_family = families[1]
        assert schedule_family.name == 'sky_managed_jobs_by_schedule_state'
        schedule_samples = {
            s.labels['schedule_state']: s.value for s in schedule_family.samples
        }
        assert schedule_samples == {'ALIVE': 3, 'DONE': 10, 'WAITING': 1}

        # Check recovery count metric
        recovery_family = families[2]
        assert recovery_family.name == 'sky_managed_jobs_recovery_count'
        recovery_samples = {(s.labels['job_id'], s.labels['task_id']): s.value
                            for s in recovery_family.samples}
        assert recovery_samples == {('42', '0'): 3, ('99', '1'): 1}

    def test_collect_uses_cache(self):
        """Verify collect() caches results and doesn't re-query within TTL."""
        collector = metrics.ManagedJobsCollector()
        collector._cache_ttl = 30

        with patch.object(collector, '_refresh') as mock_refresh:

            def side_effect():
                collector._cached_status_counts = {'RUNNING': 1}
                collector._cached_schedule_state_counts = {'ALIVE': 1}
                collector._cached_recovery_counts = {}

            mock_refresh.side_effect = side_effect

            # First collect triggers refresh
            list(collector.collect())
            assert mock_refresh.call_count == 1

            # Second collect within TTL uses cache
            list(collector.collect())
            assert mock_refresh.call_count == 1

    def test_collect_handles_refresh_error(self):
        """Verify collect() gracefully handles DB errors."""
        collector = metrics.ManagedJobsCollector()

        with patch.object(collector,
                          '_refresh',
                          side_effect=RuntimeError('DB error')):
            # Should not raise, should yield empty metrics
            families = list(collector.collect())
            assert len(families) == 3

    def test_describe_yields_expected_families(self):
        """Verify describe() yields the expected metric family names."""
        collector = metrics.ManagedJobsCollector()
        families = list(collector.describe())
        names = [f.name for f in families]
        assert 'sky_managed_jobs_by_status' in names
        assert 'sky_managed_jobs_by_schedule_state' in names
        assert 'sky_managed_jobs_recovery_count' in names
