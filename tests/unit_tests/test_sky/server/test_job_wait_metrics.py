import datetime
from unittest import mock

import pytest
import sqlalchemy

from sky.jobs import state
from sky.server import job_wait_metrics

NOW = 1800000000.0


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = sqlalchemy.create_engine(f'sqlite:///{tmp_path}/jobs.db')
    state.Base.metadata.create_all(engine)
    monkeypatch.setattr(state._db_manager, 'get_engine', lambda: engine)
    yield engine
    engine.dispose()


def seed(db,
         job_id,
         status='SUCCEEDED',
         submitted=None,
         started=None,
         ended=None,
         events=(),
         task_id=0):
    with db.begin() as conn:
        if task_id == 0:
            conn.execute(state.job_info_table.insert().values(
                spot_job_id=job_id,
                name=f'job-{job_id}',
                workspace='research',
                user_hash='user-1',
                cloud='Kubernetes'))
        conn.execute(state.spot_table.insert().values(
            spot_job_id=job_id,
            task_id=task_id,
            status=status,
            submitted_at=submitted,
            start_at=started,
            end_at=ended,
            resources='1x[L40S:1]',
            full_resources={
                'accelerators': {
                    'L40S': 1
                },
                'labels': {
                    'team': 'vision',
                    'project': 'enhancement'
                }
            }))
        for event_task, event_status, reason, timestamp in events:
            conn.execute(state.job_events_table.insert().values(
                spot_job_id=job_id,
                task_id=event_task,
                new_status=event_status,
                reason=reason,
                timestamp=datetime.datetime.fromtimestamp(
                    timestamp, datetime.timezone.utc)))


def samples():
    with mock.patch.object(job_wait_metrics.time, 'time', return_value=NOW):
        return [
            sample for metric in job_wait_metrics.JobWaitCollector().collect()
            for sample in metric.samples
        ]


def values(data, name, **labels):
    return [
        s.value for s in data if s.name == name and all(
            s.labels.get(k) == v for k, v in labels.items())
    ]


def test_first_submission_survives_retries_and_cleanup(db):
    seed(db,
         1,
         submitted=NOW - 100,
         started=NOW - 50,
         ended=NOW - 10,
         events=[
             (0, 'PENDING', 'Job submitted to queue', NOW - 1000),
             (0, 'STARTING', 'Job is starting', NOW - 990),
             (0, 'PENDING', 'Job is in backoff', NOW - 500),
             (0, 'STARTING', 'Job is starting', NOW - 100),
             (0, 'RUNNING', 'Job has started', NOW - 50),
             (None, 'CANCELLED', 'Job has been cancelled', NOW - 5),
         ])
    data = samples()
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_sum',
                  phase='total',
                  outcome='started') == [950]
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_sum',
                  phase='controller',
                  outcome='started') == [10]
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_sum',
                  phase='launch',
                  outcome='started') == [940]
    assert values(data, 'sky_managed_job_wait_7d_tasks',
                  outcome='started') == [1]


def test_cancellation_failure_and_active_waits_are_separate(db):
    for job_id, status, end in [(1, 'CANCELLED', NOW - 10),
                                (2, 'FAILED_SETUP', NOW - 10),
                                (3, 'STARTING', None)]:
        seed(db,
             job_id,
             status=status,
             submitted=NOW - 100,
             ended=end,
             events=[
                 (0, 'PENDING', 'Job submitted to queue', NOW - 600),
                 (0, 'STARTING', 'Job is starting', NOW - 590),
             ])
    data = samples()
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_sum',
                  outcome='cancelled_before_start') == [590]
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_sum',
                  outcome='failed_before_start') == [590]
    assert values(data, 'sky_managed_job_waiting_tasks') == [1]
    assert values(data, 'sky_managed_job_oldest_wait_seconds') == [600]
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_count',
                  outcome='started') == []


def test_missing_events_are_reported_not_reconstructed_from_mutable_fields(db):
    seed(db, 1, submitted=NOW - 100, started=NOW - 50, ended=NOW - 10)
    seed(db,
         2,
         submitted=NOW - 100,
         ended=NOW - 10,
         events=[
             (0, 'PENDING', 'Job submitted to queue', NOW - 100),
         ])
    data = samples()
    assert values(data,
                  'sky_managed_job_wait_missing_tasks',
                  reason='submission') == [1]
    assert values(data, 'sky_managed_job_wait_missing_tasks',
                  reason='start') == [1]
    assert values(data, 'sky_managed_job_wait_7d_seconds_count') == []


def test_window_uses_original_submission_and_keeps_old_active_waits(db):
    for job_id, status in [(1, 'SUCCEEDED'), (2, 'STARTING')]:
        seed(db,
             job_id,
             status=status,
             submitted=NOW - 100,
             ended=NOW - 10 if job_id == 1 else None,
             events=[
                 (0, 'PENDING', 'Job submitted to queue', NOW - 8 * 86400),
             ])
    data = samples()
    assert values(data, 'sky_managed_job_wait_7d_tasks') == []
    assert values(data, 'sky_managed_job_waiting_tasks') == [1]
    assert values(data, 'sky_managed_job_oldest_wait_seconds') == [8 * 86400]


def test_task_events_do_not_leak_between_pipeline_tasks(db):
    seed(db,
         1,
         submitted=NOW - 300,
         started=NOW - 200,
         ended=NOW - 190,
         events=[
             (0, 'PENDING', 'Job submitted to queue', NOW - 300),
             (0, 'STARTING', 'Job is starting', NOW - 290),
             (0, 'RUNNING', 'Job has started', NOW - 200),
         ])
    seed(db,
         1,
         task_id=1,
         status='CANCELLED',
         submitted=NOW - 180,
         ended=NOW - 10,
         events=[
             (1, 'PENDING', 'Job submitted to queue', NOW - 180),
             (1, 'STARTING', 'Job is starting', NOW - 170),
         ])
    data = samples()
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_sum',
                  phase='total',
                  outcome='started') == [100]
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_sum',
                  outcome='cancelled_before_start') == [170]


def test_cumulative_buckets_and_labels(db):
    for job_id, duration in [(1, 60), (2, 600)]:
        seed(db,
             job_id,
             submitted=NOW - 1000,
             started=NOW - 1000 + duration,
             ended=NOW - 1,
             events=[
                 (0, 'PENDING', 'Job submitted to queue', NOW - 1000),
                 (0, 'RUNNING', 'Job has started', NOW - 1000 + duration),
             ])
    data = samples()
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_bucket',
                  phase='total',
                  le='60') == [1]
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_bucket',
                  phase='total',
                  le='600') == [2]
    assert values(data,
                  'sky_managed_job_wait_7d_seconds_bucket',
                  phase='total',
                  le='+Inf') == [2]
    assert values(data,
                  'sky_managed_job_wait_7d_tasks',
                  team='vision',
                  project='enhancement',
                  gpu='L40S',
                  workspace='research',
                  user='user-1') == [2]


def test_invalid_chronology_does_not_emit_negative_duration(db):
    seed(db,
         1,
         submitted=NOW - 100,
         started=NOW - 200,
         ended=NOW - 1,
         events=[
             (0, 'PENDING', 'Job submitted to queue', NOW - 100),
             (0, 'RUNNING', 'Job has started', NOW - 200),
         ])
    data = samples()
    assert values(data,
                  'sky_managed_job_wait_missing_tasks',
                  reason='invalid_timestamps') == [1]
    assert values(data, 'sky_managed_job_wait_7d_seconds_count') == []


def test_started_status_with_missing_history_is_not_waiting(db):
    seed(db,
         1,
         status='RUNNING',
         submitted=NOW - 100,
         events=[
             (0, 'PENDING', 'Job submitted to queue', NOW - 100),
         ])
    data = samples()
    assert values(data, 'sky_managed_job_waiting_tasks') == []
    assert values(data, 'sky_managed_job_wait_missing_tasks',
                  reason='start') == [1]


def test_legacy_resources_preserve_requested_gpu_type(db):
    seed(db,
         1,
         status='STARTING',
         submitted=NOW - 100,
         events=[
             (0, 'PENDING', 'Job submitted to queue', NOW - 100),
         ])
    with db.begin() as conn:
        conn.execute(state.spot_table.update().values(full_resources=None))
    assert values(samples(), 'sky_managed_job_waiting_tasks', gpu='L40S') == [1]


@pytest.mark.parametrize('started', [None, NOW - 200])
def test_recovery_only_excludes_tasks_that_previously_started(db, started):
    seed(db,
         1,
         status='RECOVERING',
         submitted=NOW - 100,
         started=started,
         events=[
             (0, 'PENDING', 'Job submitted to queue', NOW - 600),
             (0, 'STARTING', 'Job is starting', NOW - 590),
             (0, 'RECOVERING', 'Job is recovering', NOW - 100),
         ])
    data = samples()
    if started is None:
        assert values(data, 'sky_managed_job_waiting_tasks') == [1]
        assert values(data, 'sky_managed_job_oldest_wait_seconds') == [600]
    else:
        assert values(data, 'sky_managed_job_waiting_tasks') == []
        assert values(data,
                      'sky_managed_job_wait_missing_tasks',
                      reason='start') == [1]
