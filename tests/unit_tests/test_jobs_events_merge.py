"""Tests for merging cluster launch-progress events into job events.

Covers sky.jobs.server.core.get_job_events's include_cluster_events path,
which surfaces provisioning milestones (e.g. image pulling) from the job's
underlying cluster in the managed-job timeline.
"""
import datetime

import pytest

from sky import global_user_state
from sky.jobs import runner as managed_job_runner
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


def _task(task_name='my-task', pool=None, task_id=0, job_name='my-pipeline'):
    # 'task_name' is the per-task name the controller uses to build the
    # cluster name; 'job_name' is the job-level/DAG name (shared across a
    # pipeline's tasks). They are deliberately different so a test fails if
    # the wrong field is used to reconstruct the cluster name.
    return {
        'task_name': task_name,
        'job_name': job_name,
        'pool': pool,
        'task_id': task_id,
    }


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
                        lambda name, job_id: f'{name}-{job_id}')
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

    # Cluster name reconstructed from the per-task name + job id (not the
    # job-level/DAG name 'my-pipeline').
    assert captured['name'] == 'my-task-1'
    # Both the milestone sequence and the finer-grained launch progress are
    # requested.
    assert (set(captured['event_types']) == {
        global_user_state.ClusterEventType.STATUS_CHANGE,
        global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    })
    # Newest first, and the window is exactly the most recent three rows of
    # the merged list -- so the older 'Job is starting' drops out.
    reasons = [e['reason'] for e in result]
    assert reasons == [
        'Job has started',
        'Launching (1 pod(s) pending due to Pulling)',
        'Launching (Kubernetes cluster is autoscaling)',
    ]
    # Merged cluster events are tagged as STARTING-phase events, and carry
    # the task whose cluster produced them (this used to be hard-coded None,
    # which made a job group's tasks indistinguishable in the timeline).
    pulling = next(e for e in result if 'Pulling' in e['reason'])
    assert pulling['new_status'] == managed_job_state.ManagedJobStatus.STARTING
    assert pulling['spot_job_id'] == 1
    assert pulling['task_id'] == 0


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
                        lambda name, job_id: f'{name}-{job_id}')

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
                        lambda name, job_id: f'{name}-{job_id}')
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


def test_pipeline_uses_per_task_cluster_name(monkeypatch):
    # A multi-task pipeline launches one cluster per task, each named from the
    # per-task name (task.name), while the job-level/DAG name is shared. The
    # merge must look up events by per-task cluster name, not the shared name.
    job_events = [
        _job_event('Job has started',
                   managed_job_state.ManagedJobStatus.RUNNING, 300)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    # Two tasks of the same pipeline: distinct per-task names, shared DAG name.
    monkeypatch.setattr(
        managed_job_state, 'get_managed_job_tasks', lambda job_id: [
            _task(task_name='pipe-0', task_id=0, job_name='pipe'),
            _task(task_name='pipe-1', task_id=1, job_name='pipe'),
        ])
    monkeypatch.setattr(managed_job_utils, 'generate_managed_job_cluster_name',
                        lambda name, job_id: f'{name}-{job_id}')

    queried_names = []

    def _fake_cluster_events(name, event_types, limit=None):
        queried_names.append(name)
        return [{'reason': f'Provisioning {name}', 'transitioned_at': 100}]

    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        _fake_cluster_events)

    core.get_job_events(job_id=1, include_cluster_events=True)

    # Per-task cluster names, not the shared DAG name 'pipe-1'.
    assert queried_names == ['pipe-0-1', 'pipe-1-1']


def test_merged_cluster_events_carry_their_task_id(monkeypatch):
    """A job group's cluster events must not all report task_id None.

    Otherwise the CLI's TASK column is wrong for every merged row and the
    events of two tasks are indistinguishable.
    """
    job_events = [
        _job_event('Job is starting',
                   managed_job_state.ManagedJobStatus.STARTING, 100)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(
        managed_job_state, 'get_managed_job_tasks', lambda job_id: [
            _task(task_name='grp-0', task_id=0),
            _task(task_name='grp-1', task_id=1),
        ])
    c0 = managed_job_utils.generate_managed_job_cluster_name('grp-0', 1)
    c1 = managed_job_utils.generate_managed_job_cluster_name('grp-1', 1)
    by_cluster = {
        c0: [{
            'reason': 'Launching (pending: Resources)',
            'transitioned_at': 110
        }],
        c1: [{
            'reason': 'Launching (pending: Priority)',
            'transitioned_at': 120
        }],
    }
    monkeypatch.setattr(
        global_user_state,
        'get_cluster_events_by_name',
        lambda name, event_types, limit=None: by_cluster.get(name, []))

    events = core.get_job_events(job_id=1,
                                 limit=None,
                                 include_cluster_events=True)
    merged = {
        event['reason']: event['task_id']
        for event in events
        if 'pending:' in event['reason']
    }
    assert merged == {
        'Launching (pending: Resources)': 0,
        'Launching (pending: Priority)': 1,
    }


class TestResolveTaskId:
    """A task name or id is resolved the way `sky jobs logs` accepts it."""

    @staticmethod
    def _tasks(monkeypatch):
        monkeypatch.setattr(
            managed_job_state, 'get_managed_job_tasks', lambda job_id: [
                _task(task_name='train', task_id=0),
                _task(task_name='eval', task_id=1),
            ])

    def test_name_and_id(self, monkeypatch):
        self._tasks(monkeypatch)
        assert core._resolve_task_id(1, 'eval') == 1
        assert core._resolve_task_id(1, 0) == 0
        # A numeric string is an id, matching the CLI's documented behavior.
        assert core._resolve_task_id(1, '1') == 1

    def test_unknown_name_or_id_raises(self, monkeypatch):
        self._tasks(monkeypatch)
        with pytest.raises(ValueError, match="'nope' not found"):
            core._resolve_task_id(1, 'nope')
        with pytest.raises(ValueError, match='Task 9 not found'):
            core._resolve_task_id(1, 9)

    def test_missing_job_raises(self, monkeypatch):
        monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                            lambda job_id: [])
        with pytest.raises(ValueError, match='Managed job 7 not found'):
            core._resolve_task_id(7, 'train')


def test_the_newest_row_wins_when_the_job_is_chatty(monkeypatch):
    """A job with enough transitions to fill the budget on its own still
    reports the launch reason when that is the newest row of all: the cut is
    by recency across both sources, not per source.
    """
    job_events = [
        _job_event(f'transition {i}',
                   managed_job_state.ManagedJobStatus.RECOVERING, 100 + i)
        for i in range(5)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(reversed(job_events)))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [_task()])
    monkeypatch.setattr(managed_job_utils, 'generate_managed_job_cluster_name',
                        lambda name, job_id: f'{name}-{job_id}')
    monkeypatch.setattr(global_user_state,
                        'get_cluster_events_by_name',
                        lambda name, event_types, limit=None: [{
                            'reason': 'Launching (pending: Resources)',
                            'transitioned_at': 500
                        }])

    result = core.get_job_events(job_id=1, limit=5, include_cluster_events=True)
    reasons = [event['reason'] for event in result]
    assert len(result) == 5
    # The newest event of all survives, and it is reported first.
    assert reasons[0] == 'Launching (pending: Resources)'
    # The oldest job transition is what gives up its slot.
    assert 'transition 0' not in reasons
    assert 'transition 4' in reasons


def test_the_window_is_exactly_the_most_recent_rows(monkeypatch):
    """Reserving a share for the cluster side was tried and dropped: it gave
    slots to provisioning rows from long ago, so a caller that asked for the
    ten most recent events got six of them. `details` carries the
    "why has this not started" guarantee instead.
    """
    job_events = [
        _job_event(f'transition {i}',
                   managed_job_state.ManagedJobStatus.RECOVERING, 1000 + i)
        for i in range(10)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(reversed(job_events)))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [_task()])
    monkeypatch.setattr(global_user_state,
                        'get_cluster_events_by_name',
                        lambda name, event_types, limit=None: [{
                            'reason': f'Launching (step {i})',
                            'transitioned_at': 100 + i
                        } for i in range(4)])

    result = core.get_job_events(job_id=1,
                                 limit=10,
                                 include_cluster_events=True)
    reasons = [event['reason'] for event in result]
    assert len(reasons) == 10
    assert not any(r.startswith('Launching') for r in reasons)


def test_limit_one_returns_the_newest_event_of_either_source(monkeypatch):
    job_events = [
        _job_event('Job is starting',
                   managed_job_state.ManagedJobStatus.STARTING, 100)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [_task()])
    monkeypatch.setattr(managed_job_utils, 'generate_managed_job_cluster_name',
                        lambda name, job_id: f'{name}-{job_id}')
    monkeypatch.setattr(global_user_state,
                        'get_cluster_events_by_name',
                        lambda name, event_types, limit=None: [{
                            'reason': 'Launching (pending: Resources)',
                            'transitioned_at': 500
                        }])

    result = core.get_job_events(job_id=1, limit=1, include_cluster_events=True)
    assert [e['reason'] for e in result] == ['Launching (pending: Resources)']


def test_events_go_through_the_registered_runner(monkeypatch):
    """The whole point of the indirection: a runner can answer with what the
    infrastructure knows about the job, which the default cannot read."""
    calls = []

    class _Runner:

        def events(self, **kwargs):
            calls.append(kwargs)
            return [_job_event('from the runner', None, 1)]

    monkeypatch.setattr(managed_job_runner, '_current', _Runner())
    result = core.get_job_events(job_id=7,
                                 task_id=1,
                                 limit=5,
                                 include_cluster_events=True,
                                 task='train')
    assert [event['reason'] for event in result] == ['from the runner']
    # Every argument is passed through; nothing is interpreted on the way.
    assert calls == [{
        'job_id': 7,
        'task_id': 1,
        'task': 'train',
        'limit': 5,
        'include_cluster_events': True,
    }]


def test_the_default_runner_still_reads_the_database(monkeypatch):
    """Overriding must be a choice, not the only path: with nothing
    registered the answer is the same as before the indirection existed."""
    job_events = [
        _job_event('Job has started',
                   managed_job_state.ManagedJobStatus.RUNNING, 300)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_runner, '_current', None)
    assert core.get_job_events(job_id=1,
                               include_cluster_events=False) == job_events


def test_limit_one_prefers_the_newest_even_when_it_is_the_job_s_own(
        monkeypatch):
    """The mirror of test_limit_one_returns_the_newest_event_of_either_source:
    a single-row view shows the transition that just happened, not an older
    launch line."""
    job_events = [
        _job_event('Job has started',
                   managed_job_state.ManagedJobStatus.RUNNING, 500)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [_task()])
    monkeypatch.setattr(global_user_state,
                        'get_cluster_events_by_name',
                        lambda name, event_types, limit=None: [{
                            'reason': 'Launching (pending: Resources)',
                            'transitioned_at': 100
                        }])

    result = core.get_job_events(job_id=1, limit=1, include_cluster_events=True)
    assert [event['reason'] for event in result] == ['Job has started']


def test_a_runner_without_events_falls_back_to_the_default(monkeypatch):
    """The plugins that register a runner ship separately from the server, so
    one predating this method can be installed against a newer OSS. Calling
    it unconditionally would fail the endpoint on an interface it never saw.
    """
    job_events = [
        _job_event('Job has started',
                   managed_job_state.ManagedJobStatus.RUNNING, 300)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))

    class _OldRunner:
        """Implements the three methods that existed before `events`."""

        def fetch_managed_job_table(self, **kwargs):
            raise AssertionError('not reached')

        def cancel_managed_jobs(self, **kwargs):
            raise AssertionError('not reached')

        def tail_managed_job_logs(self, **kwargs):
            raise AssertionError('not reached')

    monkeypatch.setattr(managed_job_runner, '_current', _OldRunner())
    assert core.get_job_events(job_id=1,
                               include_cluster_events=False) == job_events
