"""Unit tests for managed jobs Prometheus metrics."""
from unittest.mock import patch

from sky.metrics import utils as metrics_utils
from sky.server import metrics


class TestManagedJobsCollector:
    """Tests for ManagedJobsCollector."""

    def test_collect_returns_all_metric_families(self):
        """Verify collect() yields the expected metric families."""
        mock_status_counts = {'RUNNING': 3, 'PENDING': 2, 'SUCCEEDED': 10}

        collector = metrics.ManagedJobsCollector()
        with patch.object(collector, '_refresh') as mock_refresh:

            def side_effect():
                collector._cached_status_counts = mock_status_counts

            mock_refresh.side_effect = side_effect

            families = list(collector.collect())

        assert len(families) == 1

        status_family = families[0]
        assert status_family.name == 'sky_managed_jobs_count'
        status_samples = {
            s.labels['status']: s.value for s in status_family.samples
        }
        assert status_samples == {'RUNNING': 3, 'PENDING': 2, 'SUCCEEDED': 10}

    def test_collect_uses_cache(self):
        """Verify collect() caches results and doesn't re-query within TTL."""
        collector = metrics.ManagedJobsCollector()
        collector._cache_ttl = 30

        with patch.object(collector, '_refresh') as mock_refresh:

            def side_effect():
                collector._cached_status_counts = {'RUNNING': 1}

            mock_refresh.side_effect = side_effect

            # First collect triggers refresh
            list(collector.collect())
            assert mock_refresh.call_count == 1

            # Second collect within TTL uses cache
            list(collector.collect())
            assert mock_refresh.call_count == 1

    def test_collect_retries_on_error(self):
        """Verify collect() retries refresh on next scrape after failure."""
        collector = metrics.ManagedJobsCollector()
        collector._cache_ttl = 0  # Always stale

        call_count = 0

        def failing_refresh():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError('DB error')
            collector._cached_status_counts = {'RUNNING': 1}

        with patch.object(collector, '_refresh', side_effect=failing_refresh):
            # First collect fails, should yield empty metrics
            families = list(collector.collect())
            assert len(families) == 1
            assert list(families[0].samples) == []

            # Second collect retries (not skipped by TTL)
            families = list(collector.collect())
            assert len(families) == 1
            samples = {s.labels['status']: s.value for s in families[0].samples}
            assert samples == {'RUNNING': 1}

        assert call_count == 2

    def test_describe_yields_expected_families(self):
        """Verify describe() yields the expected metric family names."""
        collector = metrics.ManagedJobsCollector()
        families = list(collector.describe())
        names = [f.name for f in families]
        assert 'sky_managed_jobs_count' in names


class TestManagedJobsLimitMetrics:
    """Tests for managed jobs limit gauge."""

    def test_launches_per_worker_gauge(self):
        """Verify the LAUNCHES_PER_WORKER gauge can be set and read."""
        gauge = metrics_utils.SKY_MANAGED_JOBS_LIMIT_LAUNCHES_PER_WORKER
        gauge.labels(pid='12345').set(8)
        # In non-multiprocess mode we can read the labeled child directly.
        val = gauge.labels(pid='12345')._value.get()
        assert val == 8.0
