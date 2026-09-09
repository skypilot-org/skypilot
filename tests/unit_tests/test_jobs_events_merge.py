"""Tests for merging cluster launch-progress events into job events.

Covers sky.jobs.server.core.get_job_events's include_cluster_events path,
which surfaces provisioning milestones (e.g. image pulling) from the job's
underlying cluster in the managed-job timeline.
"""
import datetime
import time
import types

import pytest

from sky import clouds
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
        # Two callers now: the merge asks for both types with the budget,
        # and resolving a torn-down cluster's allocation asks for
        # launch-progress alone and unbounded.
        captured.setdefault('calls', []).append((name, set(event_types), limit))
        captured['name'] = name
        return list(cluster_events)

    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        _fake_cluster_events)

    result = core.get_job_events(job_id=1, limit=3, include_cluster_events=True)

    # Cluster name reconstructed from the per-task name + job id (not the
    # job-level/DAG name 'my-pipeline').
    assert captured['name'] == 'my-task-1'
    # Both the milestone sequence and the finer-grained launch progress are
    # requested.
    assert (3, {
        global_user_state.ClusterEventType.STATUS_CHANGE,
        global_user_state.ClusterEventType.LAUNCH_PROGRESS,
    }) in [(limit, types) for _, types, limit in captured['calls']]
    # Newest first, capped at limit=3. The job's own two transitions keep
    # their place and the remaining slot goes to the newest cluster event:
    # a launch can emit more cluster events than the limit, and losing the
    # job's status sequence to them is the worse failure (see
    # test_limit_never_starves_the_job_own_timeline).
    reasons = [e['reason'] for e in result]
    assert reasons == [
        'Job has started',
        'Launching (1 pod(s) pending due to Pulling)',
        'Job is starting',
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

    # Per-task cluster names, not the shared DAG name 'pipe-1'. Each is
    # asked about twice -- once by the merge, once to resolve a torn-down
    # cluster's Slurm allocation -- so compare the distinct names.
    assert list(dict.fromkeys(queried_names)) == ['pipe-0-1', 'pipe-1-1']


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


def test_limit_never_starves_the_job_own_timeline(monkeypatch):
    """A chatty launch must not push PENDING/STARTING out of the window.

    The merged list is capped at `limit`, and the cluster side can produce
    more events than that on its own; the job's transitions are the sequence
    the timeline is read for, so they keep their place.
    """
    job_events = [
        _job_event('Job submitted to queue',
                   managed_job_state.ManagedJobStatus.PENDING, 100),
        _job_event('Job is starting',
                   managed_job_state.ManagedJobStatus.STARTING, 110),
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [_task()])
    # 20 cluster events, all newer than the job's own two.
    monkeypatch.setattr(global_user_state,
                        'get_cluster_events_by_name',
                        lambda name, event_types, limit=None: [{
                            'reason': f'Launching (step {i})',
                            'transitioned_at': 200 + i
                        } for i in range(20)][:limit or 20])

    result = core.get_job_events(job_id=1, limit=5, include_cluster_events=True)
    assert len(result) == 5
    reasons = [event['reason'] for event in result]
    # Both job events survive; the rest of the budget goes to the newest
    # cluster events.
    assert 'Job submitted to queue' in reasons
    assert 'Job is starting' in reasons
    assert sum(1 for r in reasons if r.startswith('Launching')) == 3
    # Still newest-first overall.
    stamps = [event['timestamp'].timestamp() for event in result]
    assert stamps == sorted(stamps, reverse=True)


def test_limit_never_starves_the_cluster_events(monkeypatch):
    """A job with many recoveries must not hide the current launch reason.

    The mirror of test_limit_never_starves_the_job_own_timeline: when the
    job's own transitions can fill the whole budget, the newest row of all
    is usually the launch reason the user is waiting on, so the cluster side
    keeps a floor.
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


def _slurm_handle(name_on_cloud='sky-my-task-1-a1b2', region='dev-slurm'):
    """A cluster record's handle, as far as the Slurm resolution reads it."""
    resources = types.SimpleNamespace(cloud=clouds.Slurm(), region=region)
    return types.SimpleNamespace(cluster_name_on_cloud=name_on_cloud,
                                 launched_resources=resources)


def _other_handle():
    resources = types.SimpleNamespace(cloud=clouds.Kubernetes(),
                                      region='my-context')
    return types.SimpleNamespace(cluster_name_on_cloud='sky-my-task-1-a1b2',
                                 launched_resources=resources)


def _entry(event, at, text):
    return {'event': event, 'at': at, 'text': text}


def test_only_slurm_backed_clusters_resolve_to_an_allocation(monkeypatch):
    records = {
        'slurm-cluster': {
            'handle': _slurm_handle()
        },
        'k8s-cluster': {
            'handle': _other_handle()
        },
        'gone-cluster': None,
    }
    monkeypatch.setattr(global_user_state, 'get_cluster_from_name',
                        lambda name, **kwargs: records.get(name))
    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        lambda *args, **kwargs: [])
    allocations = core._slurm_allocations([('slurm-cluster', 0),
                                           ('k8s-cluster', 1),
                                           ('gone-cluster', 2)])
    # The Slurm cluster name comes from the resources' region, and the sbatch
    # job name from cluster_name_on_cloud. The cluster that is simply not
    # there has no recorded allocation either, so it contributes nothing.
    assert allocations == [
        core._SlurmAllocation('dev-slurm', 'sky-my-task-1-a1b2', [], 0)
    ]


def test_a_cluster_read_that_fails_is_skipped(monkeypatch):

    def _boom(name, **kwargs):
        del kwargs
        raise RuntimeError(f'db is down ({name})')

    monkeypatch.setattr(global_user_state, 'get_cluster_from_name', _boom)
    assert core._slurm_allocations([('slurm-cluster', 0)]) == []


def _patch_slurm(monkeypatch, entries, handle=None, capture=None):
    monkeypatch.setattr(
        global_user_state, 'get_cluster_from_name',
        lambda name, **kwargs: {'handle': handle or _slurm_handle()})

    def _timeline(cluster, job_name, since, deadline=None):
        if capture is not None:
            capture.append((cluster, job_name, since, deadline))
        return list(entries)

    monkeypatch.setattr(core.slurm_provision_utils, 'job_timeline', _timeline)


def test_slurm_entries_join_the_timeline_without_claiming_a_status(monkeypatch):
    job_events = [
        _job_event('Job has started',
                   managed_job_state.ManagedJobStatus.RUNNING, 300)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [dict(_task(), submitted_at=50)])
    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        lambda *args, **kwargs: [])
    _patch_slurm(monkeypatch, [
        _entry('submitted', 100, 'Slurm allocation 17213 submitted'),
        _entry('started', 200, 'Slurm allocation 17213 started'),
    ])

    result = core.get_job_events(job_id=1, include_cluster_events=True)
    assert [event['reason'] for event in result] == [
        'Job has started',
        'Slurm allocation 17213 started',
        'Slurm allocation 17213 submitted',
    ]
    slurm_rows = [e for e in result if e['reason'].startswith('Slurm ')]
    for row in slurm_rows:
        # Unlike a cluster event, these are not transitions of the job, so
        # they assert no status at that instant.
        assert row['new_status'] is None
        assert row['code'] is None
        assert row['spot_job_id'] == 1
        assert row['task_id'] == 0


def test_the_accounting_window_starts_at_the_job_submission(monkeypatch):
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: [])
    monkeypatch.setattr(
        managed_job_state, 'get_managed_job_tasks', lambda job_id: [
            dict(_task(task_id=0), submitted_at=1500),
            dict(_task(task_id=1), submitted_at=900),
        ])
    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        lambda *args, **kwargs: [])
    calls = []
    _patch_slurm(monkeypatch, [], capture=calls)

    core.get_job_events(job_id=1, include_cluster_events=True)
    # The earliest task's submission, so a pipeline's first allocation is
    # still inside sacct's window.
    assert [since for _, _, since, _ in calls] == [900, 900]
    # Both allocations are read against one budget, not one each.
    assert len({deadline for *_, deadline in calls}) == 1


def test_a_job_with_no_submit_time_still_gets_a_window(monkeypatch):
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [dict(_task(), submitted_at=None)])
    since = core._job_submitted_at(1)
    # A fortnight, which reaches past any cluster still in the database.
    assert 13 * 24 * 3600 < time.time() - since < 15 * 24 * 3600


def test_a_slurm_read_that_fails_leaves_the_job_events_intact(monkeypatch):
    job_events = [
        _job_event('Job is starting',
                   managed_job_state.ManagedJobStatus.STARTING, 100)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [dict(_task(), submitted_at=50)])
    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        lambda *args, **kwargs: [])
    monkeypatch.setattr(global_user_state, 'get_cluster_from_name',
                        lambda name, **kwargs: {'handle': _slurm_handle()})

    def _boom(cluster, job_name, since, deadline=None):
        del cluster, job_name, since, deadline
        raise RuntimeError('login node is unreachable')

    monkeypatch.setattr(core.slurm_provision_utils, 'job_timeline', _boom)
    assert core.get_job_events(job_id=1,
                               include_cluster_events=True) == job_events


def test_slurm_entries_share_the_cluster_events_budget(monkeypatch):
    """Both are 'what the infrastructure did'; neither may starve the job's
    own transitions, and one limit governs the pair."""
    job_events = [
        _job_event('Job has started',
                   managed_job_state.ManagedJobStatus.RUNNING, 900)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [dict(_task(), submitted_at=50)])
    monkeypatch.setattr(
        global_user_state, 'get_cluster_events_by_name',
        lambda *args, **kwargs: [{
            'reason': f'Launching (pending: Resources) {i}',
            'transitioned_at': 100 + i,
        } for i in range(5)])
    _patch_slurm(
        monkeypatch,
        [_entry('submitted', 200 + i, f'Slurm entry {i}') for i in range(5)])

    result = core.get_job_events(job_id=1, limit=3, include_cluster_events=True)
    assert len(result) == 3
    # The job's own newest event survives, and the infra rows fill the rest.
    assert result[0]['reason'] == 'Job has started'
    assert all(
        event['reason'].startswith('Slurm entry') for event in result[1:])


def test_the_floor_holds_when_the_job_events_are_all_newer(monkeypatch):
    """The floor has to *reserve* a slot, not just widen the candidate pool.
    A Slurm timeline is older than the job's recent transitions by nature --
    submitted/eligible/started all happen early -- so a job with a full
    budget of its own newer events would otherwise hide the whole timeline,
    which is exactly what the reader asked for."""
    job_events = [
        _job_event('Job is restarting',
                   managed_job_state.ManagedJobStatus.STARTING, 600 + i)
        for i in range(3)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [dict(_task(), submitted_at=50)])
    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        lambda *args, **kwargs: [])
    _patch_slurm(monkeypatch,
                 [_entry('started', 500, 'Slurm allocation 17213 started')])

    result = core.get_job_events(job_id=1, limit=3, include_cluster_events=True)
    assert len(result) == 3
    assert result[-1]['reason'] == 'Slurm allocation 17213 started'


def test_limit_one_prefers_the_newest_even_when_it_is_the_job_s_own(
        monkeypatch):
    """The mirror of test_limit_one_returns_the_newest_event_of_either_source:
    the reserved infra share must never cost the job's own newest row, or a
    single-row view would show an old launch line instead of the transition
    that just happened."""
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


def test_the_slurm_reads_share_one_budget_across_allocations(monkeypatch):
    """Per-read timeouts do not bound a pipeline: an unresponsive login node
    would cost them once per allocation."""
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: [])
    monkeypatch.setattr(
        managed_job_state, 'get_managed_job_tasks', lambda job_id: [
            dict(_task(task_id=0), submitted_at=50),
            dict(_task(task_id=1), submitted_at=50),
        ])
    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        lambda *args, **kwargs: [])
    calls = []
    monkeypatch.setattr(global_user_state, 'get_cluster_from_name',
                        lambda name, **kwargs: {'handle': _slurm_handle()})

    # A budget the first allocation is made to overrun, rather than a
    # mutation mid-loop: the deadline is computed once, which is the point.
    # The margin is wide because the fragile direction is the *first* check:
    # jitter between computing the deadline and reaching the loop would
    # otherwise skip every allocation and leave `calls` empty.
    monkeypatch.setattr(core, '_SLURM_TIMELINE_BUDGET_SECONDS', 0.2)

    def _slow(cluster, job_name, since, deadline=None):
        del cluster, job_name, since, deadline
        calls.append(time.monotonic())
        time.sleep(0.25)
        return []

    monkeypatch.setattr(core.slurm_provision_utils, 'job_timeline', _slow)
    core.get_job_events(job_id=1, include_cluster_events=True)
    # The second allocation is not attempted once the budget is gone.
    assert len(calls) == 1


def test_a_failure_resolving_the_allocations_is_not_an_error(monkeypatch):
    """The guard has to cover the whole merge: resolving the allocations and
    the job's submit time are database reads of their own, and the request
    already has its job events by then."""
    job_events = [
        _job_event('Job is starting',
                   managed_job_state.ManagedJobStatus.STARTING, 100)
    ]
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: list(job_events))
    calls = {'n': 0}

    def _tasks(job_id):
        del job_id
        calls['n'] += 1
        # The first call builds the cluster list; the second, inside the
        # Slurm merge, is the one that fails.
        if calls['n'] > 1:
            raise RuntimeError('connection pool exhausted')
        return [dict(_task(), submitted_at=50)]

    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks', _tasks)
    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        lambda *args, **kwargs: [])
    monkeypatch.setattr(global_user_state, 'get_cluster_from_name',
                        lambda name, **kwargs: {'handle': _slurm_handle()})
    assert core.get_job_events(job_id=1,
                               include_cluster_events=True) == job_events


def _alloc_event(job_id, slurm_cluster='dev-slurm', at=100):
    return {
        'reason': f'Launching (Slurm job {job_id} on {slurm_cluster})',
        'transitioned_at': at,
    }


def test_a_torn_down_cluster_resolves_from_its_recorded_allocations(
        monkeypatch):
    """The point of reading sacct is a job Slurm has already forgotten, and
    that is exactly when the cluster record is gone too. The events written
    at submission outlive it."""
    monkeypatch.setattr(global_user_state, 'get_cluster_from_name',
                        lambda name, **kwargs: None)
    monkeypatch.setattr(
        global_user_state, 'get_cluster_events_by_name',
        lambda *args, **kwargs: [
            _alloc_event('17300', at=300),
            {
                'reason': 'Launching (pending: Resources; partition: dev)',
                'transitioned_at': 200
            },
            _alloc_event('17269', at=100),
        ])
    allocations = core._slurm_allocations([('gone-cluster', 0)])
    # Both attempts, oldest first, under the one Slurm cluster; the
    # pending-reason event is not mistaken for an allocation.
    assert allocations == [
        core._SlurmAllocation('dev-slurm', None, ['17300', '17269'], 0)
    ]


def test_a_live_cluster_record_wins_over_the_recorded_events(monkeypatch):
    """The record is authoritative while it exists; the events are the
    fallback, so a live cluster pays no attention to them."""
    monkeypatch.setattr(global_user_state, 'get_cluster_from_name',
                        lambda name, **kwargs: {'handle': _slurm_handle()})

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError('the events are only the fallback')

    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        _should_not_be_called)
    assert core._slurm_allocations([
        ('live-cluster', 0)
    ]) == [core._SlurmAllocation('dev-slurm', 'sky-my-task-1-a1b2', [], 0)]


def test_a_recorded_allocation_is_read_by_id_not_by_name(monkeypatch):
    """An id needs no time window -- sacct's `-j` puts the job's whole
    history in scope -- while a name-based query would."""
    monkeypatch.setattr(managed_job_state, 'get_job_events',
                        lambda **kwargs: [])
    monkeypatch.setattr(managed_job_state, 'get_managed_job_tasks',
                        lambda job_id: [dict(_task(), submitted_at=50)])
    monkeypatch.setattr(global_user_state, 'get_cluster_from_name',
                        lambda name, **kwargs: None)
    monkeypatch.setattr(global_user_state, 'get_cluster_events_by_name',
                        lambda *args, **kwargs: [_alloc_event('17269')])
    by_ids = []
    monkeypatch.setattr(
        core.slurm_provision_utils,
        'job_timeline_by_ids',
        lambda cluster, job_ids, deadline=None:
        (by_ids.append((cluster, list(job_ids))) or
         [_entry('ended', 500, 'Slurm allocation 17269 ended: COMPLETED')]))

    def _by_name(*args, **kwargs):
        raise AssertionError('a recorded allocation is addressed by id')

    monkeypatch.setattr(core.slurm_provision_utils, 'job_timeline', _by_name)
    result = core.get_job_events(job_id=1, include_cluster_events=True)
    assert by_ids == [('dev-slurm', ['17269'])]
    # The recording event is a launch-progress event too, so it also merges
    # in -- which is how a reader learns the id to pass to sacct themselves.
    assert [event['reason'] for event in result] == [
        'Slurm allocation 17269 ended: COMPLETED',
        'Launching (Slurm job 17269 on dev-slurm)',
    ]
