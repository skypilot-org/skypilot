"""Unit tests for managed jobs Prometheus metrics."""
from unittest.mock import patch

import pytest
import sqlalchemy

from sky.jobs import state
from sky.metrics import utils as metrics_utils
from sky.server import metrics


class TestManagedJobsCollector:
    """Tests for ManagedJobsCollector."""

    def test_collect_returns_all_metric_families(self):
        """Verify collect() yields the expected metric families.

        The collector consumes (workspace, user_hash, cloud, status, count)
        tuples from get_status_counts_by_workspace_user_cloud() — both
        active and terminal statuses are passed through.
        """
        mock_rows = [
            ('ws', 'u', 'AWS', 'RUNNING', 3),
            # Pre-cloud-assignment status: cloud is NULL → empty label.
            ('ws', 'u', None, 'PENDING', 2),
            # Terminal status is included.
            ('ws', 'u', 'AWS', 'SUCCEEDED', 10),
        ]
        mock_recovery_rows = [
            ('EMERGENCY', 'ws', 2),
            # LEFT JOIN miss: NULL workspace → 'default' label.
            ('FAILURE', None, 5),
        ]
        mock_episode_rows = [
            (42, 'train-llm', 'ws', 2),
            # NULL job name → empty label.
            (43, None, None, 1),
        ]

        collector = metrics.ManagedJobsCollector()
        with patch.object(collector, '_refresh') as mock_refresh:

            def side_effect():
                collector._cached_rows = mock_rows
                collector._cached_recovery_rows = mock_recovery_rows
                collector._cached_episode_rows = mock_episode_rows

            mock_refresh.side_effect = side_effect

            families = list(collector.collect())

        assert len(families) == 3

        recovery_family = families[1]
        assert recovery_family.name == 'sky_managed_job_recovery_events_count'
        recovery_samples = {(s.labels['recovery_source'],
                             s.labels['workspace']): s.value
                            for s in recovery_family.samples}
        assert recovery_samples == {
            ('EMERGENCY', 'ws'): 2,
            ('FAILURE', 'default'): 5,
        }

        episode_family = families[2]
        assert (episode_family.name ==
                'sky_managed_job_emergency_recovery_attempts')
        episode_samples = {(s.labels['job_id'], s.labels['job_name'],
                            s.labels['workspace']): s.value
                           for s in episode_family.samples}
        assert episode_samples == {
            ('42', 'train-llm', 'ws'): 2,
            ('43', '', 'default'): 1,
        }

        status_family = families[0]
        assert status_family.name == 'sky_managed_jobs_count'
        samples = {(s.labels['workspace'], s.labels['user'], s.labels['status'],
                    s.labels['cloud']): s.value for s in status_family.samples}
        assert samples == {
            ('ws', 'u', 'RUNNING', 'AWS'): 3,
            ('ws', 'u', 'PENDING', ''): 2,
            ('ws', 'u', 'SUCCEEDED', 'AWS'): 10,
        }

    def test_collect_uses_cache(self):
        """Verify collect() caches results and doesn't re-query within TTL."""
        collector = metrics.ManagedJobsCollector()
        collector._cache_ttl = 30

        with patch.object(collector, '_refresh') as mock_refresh:

            def side_effect():
                collector._cached_rows = [('ws', 'u', 'AWS', 'RUNNING', 1)]

            mock_refresh.side_effect = side_effect

            # First collect triggers refresh
            list(collector.collect())
            assert mock_refresh.call_count == 1

            # Second collect within TTL uses cache
            list(collector.collect())
            assert mock_refresh.call_count == 1

    def test_collect_retries_on_error(self):
        """Verify collect() re-queries on the next scrape after a failure.

        With _cache_ttl=0 the backoff window is zero, so a transient error
        does not permanently wedge the collector. (Backoff for a non-zero
        TTL is covered by
        test_metrics.py::test_managed_jobs_collector_advances_timestamp_on_failure.)
        """
        collector = metrics.ManagedJobsCollector()
        collector._cache_ttl = 0  # Always stale

        call_count = 0

        def failing_refresh():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError('DB error')
            collector._cached_rows = [('ws', 'u', 'AWS', 'RUNNING', 1)]

        with patch.object(collector, '_refresh', side_effect=failing_refresh):
            # First collect fails, should yield empty metrics
            families = list(collector.collect())
            assert len(families) == 3
            assert all(list(f.samples) == [] for f in families)

            # Second collect retries (not skipped by zero TTL)
            families = list(collector.collect())
            assert len(families) == 3
            samples = {s.labels['status']: s.value for s in families[0].samples}
            assert samples == {'RUNNING': 1}

        assert call_count == 2

    def test_describe_yields_expected_families(self):
        """Verify describe() yields the expected metric family names."""
        collector = metrics.ManagedJobsCollector()
        families = list(collector.describe())
        names = [f.name for f in families]
        assert 'sky_managed_jobs_count' in names
        assert 'sky_managed_job_recovery_events_count' in names
        assert 'sky_managed_job_emergency_recovery_attempts' in names


class TestManagedJobsLimitMetrics:
    """Tests for managed jobs limit gauge."""

    def test_launches_per_worker_gauge(self):
        """Verify the LAUNCHES_PER_WORKER gauge can be set and read."""
        gauge = metrics_utils.SKY_MANAGED_JOBS_LIMIT_LAUNCHES_PER_WORKER
        gauge.labels(pid='12345').set(8)
        # In non-multiprocess mode we can read the labeled child directly.
        val = gauge.labels(pid='12345')._value.get()
        assert val == 8.0


@pytest.fixture
def admission_db(monkeypatch):
    engine = sqlalchemy.create_engine('sqlite://')
    state.Base.metadata.create_all(engine)
    monkeypatch.setattr(state._db_manager, 'get_engine', lambda: engine)
    with engine.connect() as conn:
        yield conn
    engine.dispose()


def _job(conn, job_id, schedule_state, **kwargs):
    conn.execute(state.job_info_table.insert().values(
        spot_job_id=job_id, schedule_state=schedule_state, **kwargs))


def _task(conn, job_id, task_id, status, **kwargs):
    conn.execute(state.spot_table.insert().values(spot_job_id=job_id,
                                                  task_id=task_id,
                                                  status=status,
                                                  **kwargs))


def _pending_event(conn, job_id, task_id, code):
    conn.execute(state.job_events_table.insert().values(spot_job_id=job_id,
                                                        task_id=task_id,
                                                        new_status='PENDING',
                                                        code=code))


def test_controller_wait_counts(admission_db):
    db = admission_db
    _job(db, 1, 'LAUNCHING', user_hash='a')
    _task(db, 1, 0, 'STARTING', submitted_at=1)
    _job(db, 2, 'WAITING', user_hash='a')
    _task(db, 2, 0, 'PENDING')
    _task(db, 2, 1, 'PENDING')  # One claim for both tasks.
    _job(db, 3, 'WAITING', user_hash='a')
    _task(db, 3, 0, 'RUNNING', submitted_at=1)  # Controller restart.
    _job(db, 4, 'WAITING', user_hash='a')
    _job(db, 5, 'DONE', user_hash='b')
    _job(db, 6, 'WAITING', user_hash='b')
    for child, parent in [(4, 1), (4, 2), (6, 5)]:
        db.execute(state.job_dependencies_table.insert().values(
            spot_job_id=child, depends_on_job_id=parent))
    _job(db, 7, 'INACTIVE')
    _job(db, 8, None)
    _job(db, 9, 'ALIVE', pool='p', is_batch=True)
    _job(db, 10, 'WAITING', pool='p', is_batch=True, user_hash='b')
    db.commit()

    assert set(state.get_controller_wait_counts(1)) == {
        (None, 'b', 'batch_pool', 1), (None, 'a', 'dependency', 1),
        (None, 'a', 'user_limit', 1), (None, 'a', 'reattachment', 1),
        (None, 'b', 'controller_claim', 1)
    }
    assert (None, 'a', 'controller_claim',
            1) in (state.get_controller_wait_counts(None))


def test_launch_stages_are_per_active_task(admission_db):
    db = admission_db
    _job(db, 1, 'LAUNCHING', execution='parallel')
    _task(db, 1, 0, 'STARTING', status_override='PENDING')
    _task(db, 1, 1, 'PENDING', submitted_at=1, status_override='PENDING')
    _pending_event(db, 1, 1, 'launch_parked')
    _task(db, 1, 2, 'PENDING', submitted_at=1, status_override='PENDING')
    _pending_event(db, 1, 2, 'launch_parked')
    _pending_event(db, 1, 2, 'launch_retry:no_resources')
    _task(db, 1, 3, 'PENDING', submitted_at=1)
    _pending_event(db, 1, 3, 'launch_parked')  # A lock can park a launch.
    _task(db, 1, 4, 'PENDING', submitted_at=1)
    _pending_event(db, 1, 4, 'launch_retry:old_attempt')
    _pending_event(db, 1, 4, None)  # Latest reason unavailable.
    _task(db, 1, 5, 'PENDING')  # Parallel group member can start now.
    _task(db, 1, 6, 'RUNNING', status_override='PENDING')
    _task(db, 1, 7, 'CANCELLED', status_override='PENDING')
    _job(db, 2, 'ALIVE')
    _task(db, 2, 0, 'RECOVERING', status_override='PENDING')
    _task(db, 2, 1, 'PENDING')  # Future pipeline step, even on older rows.
    _job(db, 3, 'ALIVE')
    _task(db, 3, 1, 'PENDING', eligible_at=1)  # After pipeline handoff.
    _job(db, 4, 'LAUNCHING')
    _task(db, 4, 0, 'PENDING', submitted_at=1)  # Event retention or old DB.
    _job(db, 5, 'WAITING')
    _task(db, 5, 0, 'STARTING', status_override='PENDING')
    _job(db, 6, 'DONE')
    _task(db, 6, 0, 'PENDING')
    db.commit()

    assert set(state.get_launch_stage_counts()) == {
        (None, None, 'reported_queue', 2), (None, None, 'retry_pending', 1),
        (None, None, 'parked', 1), (None, None, 'unknown_pending', 2),
        (None, None, 'starting', 2), (None, None, 'recovering', 1)
    }


def test_admission_scrape_drops_drained_accounts_and_reports_zero(admission_db):
    db = admission_db
    _job(db, 1, 'WAITING', workspace='default', user_hash='a')
    _job(db, 2, 'WAITING', user_hash='a')
    db.commit()
    collector = metrics.ManagedJobAdmissionCollector()
    waiting, launching = collector.collect()
    assert [f.name for f in collector.describe()] == [
        'sky_managed_jobs_waiting_for_controller',
        'sky_managed_job_tasks_launching'
    ]
    sample, = [s for s in waiting.samples if s.value]
    assert sample.labels == {
        'workspace': 'default',
        'user': 'a',
        'reason': 'controller_claim'
    }
    assert sample.value == 2
    assert len(launching.samples) == 6
    assert all(s.value == 0 for s in launching.samples)

    db.execute(state.job_info_table.update().values(schedule_state='DONE'))
    db.commit()
    waiting, _ = collector.collect()
    assert len(waiting.samples) == 5
    assert all(s.value == 0 and s.labels['user'] == '' for s in waiting.samples)
