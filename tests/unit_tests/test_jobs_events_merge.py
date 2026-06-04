"""Tests for merging cluster launch-progress events into job events.

Covers sky.jobs.server.core.get_job_events's include_cluster_events path,
which surfaces provisioning milestones (e.g. image pulling) from the job's
underlying cluster in the managed-job timeline.
"""
import datetime

from sky import global_user_state
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.jobs.server import core


def _job_event(reason, status, ts):
    """Build a job-event dict shaped like managed_job_state.get_job_events."""
    return {
        'spot_job_id': 1,
        'task_id': None,
        'new_status': status,
        'code': None,
        'reason': reason,
        'timestamp': datetime.datetime.fromtimestamp(ts),
    }


def _task(job_name='my-task', pool=None, task_id=0):
    return {'job_name': job_name, 'pool': pool, 'task_id': task_id}


def test_no_merge_when_flag_disabled(monkeypatch):
    job_events = [
        _job_event('Job is starting',
                   managed_job_state.ManagedJobStatus.STARTING, 100)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError('cluster events should not be read')

    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        _should_not_be_called)
    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        _should_not_be_called)

    result = core.get_job_events(job_id=1, include_cluster_events=False)
    assert result == job_events


def test_merge_orders_newest_first_and_truncates(monkeypatch):
    job_events = [
        _job_event('Job has started',
                   managed_job_state.ManagedJobStatus.RUNNING, 300),
        _job_event('Job is starting',
                   managed_job_state.ManagedJobStatus.STARTING, 100),
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [_task()])
    monkeypatch.setattr(managed_job_utils, 'generate_managed_job_cluster_name',
                        lambda job_name, job_id: f'{job_name}-{job_id}')
    cluster_events = [
        {
            'reason': 'Launching (1 pod(s) pending due to Pulling)',
            'transitioned_at': 200
        },
        {
            'reason': 'Launching (Kubernetes cluster is autoscaling)',
            'transitioned_at': 150
        },
    ]
    captured = {}

    def _fake_cluster_events(name, event_types, limit=None):
        captured['name'] = name
        captured['event_types'] = event_types
        captured['limit'] = limit
        return list(cluster_events)

    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        _fake_cluster_events)

    result = core.get_job_events(job_id=1, limit=3, include_cluster_events=True)

    # Cluster name reconstructed from the task's job name + job id.
    assert captured['name'] == 'my-task-1'
    # Both the milestone sequence and the finer-grained launch progress are
    # requested.
    assert (set(captured['event_types']) == {
        global_user_state.ClusterEventType.STATUS_CHANGE,
        global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    })
    # Newest first, truncated to limit=3 (drops the oldest 'Job is starting').
    reasons = [e['reason'] for e in result]
    assert reasons == [
        'Job has started',
        'Launching (1 pod(s) pending due to Pulling)',
        'Launching (Kubernetes cluster is autoscaling)',
    ]
    # Merged cluster events are tagged as STARTING-phase events.
    pulling = next(e for e in result if 'Pulling' in e['reason'])
    assert pulling['new_status'] == managed_job_state.ManagedJobStatus.STARTING
    assert pulling['spot_job_id'] == 1
    assert pulling['task_id'] is None


def test_pool_jobs_skip_merge(monkeypatch):
    job_events = [
        _job_event('Job is starting',
                   managed_job_state.ManagedJobStatus.STARTING, 100)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [_task(pool='my-pool')])

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError('pool clusters must not be merged')

    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        _should_not_be_called)

    result = core.get_job_events(job_id=1, include_cluster_events=True)
    assert result == job_events


def test_merge_is_best_effort_on_error(monkeypatch):
    job_events = [
        _job_event('Job is starting',
                   managed_job_state.ManagedJobStatus.STARTING, 100)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [_task()])
    monkeypatch.setattr(managed_job_utils, 'generate_managed_job_cluster_name',
                        lambda job_name, job_id: f'{job_name}-{job_id}')

    def _raise(*args, **kwargs):
        raise RuntimeError('db unavailable')

    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name', _raise)

    result = core.get_job_events(job_id=1, include_cluster_events=True)
    assert result == job_events


def test_no_tasks_skips_merge(monkeypatch):
    job_events = [
        _job_event('Job submitted to queue',
                   managed_job_state.ManagedJobStatus.PENDING, 100)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [])

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError('no cluster name -> no merge')

    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        _should_not_be_called)

    result = core.get_job_events(job_id=1, include_cluster_events=True)
    assert result == job_events


def test_merged_events_match_job_event_timezone(monkeypatch):
    # On Postgres, job-event timestamps are timezone-aware. Merged cluster
    # events must adopt the same timezone so the list serializes consistently.
    aware_ts = datetime.datetime.fromtimestamp(300, tz=datetime.timezone.utc)
    job_events = [{
        'spot_job_id': 1,
        'task_id': None,
        'new_status': managed_job_state.ManagedJobStatus.RUNNING,
        'code': None,
        'reason': 'Job has started',
        'timestamp': aware_ts,
    }]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [_task()])
    monkeypatch.setattr(managed_job_utils, 'generate_managed_job_cluster_name',
                        lambda job_name, job_id: f'{job_name}-{job_id}')
    monkeypatch.setattr(
        global_user_state, 'get_cluster_events_by_name', lambda *a, **k: [{
            'reason': 'Provisioning',
            'transitioned_at': 200
        }])

    result = core.get_job_events(job_id=1, include_cluster_events=True)
    merged = next(e for e in result if e['reason'] == 'Provisioning')
    # Timezone-aware (matching the job event) and the correct instant.
    assert merged['timestamp'].tzinfo is not None
    assert merged['timestamp'].timestamp() == 200
