"""The one moment of a gated launch the event table never marked.

A job parked on a scheduler queue already produces events saying it is waiting,
with the queue's name and its position in it. Nothing said when that stopped --
and for a gated job the wait is routinely most of the start-up. These cover the
row that closes it.
"""
import types
from unittest import mock

import pytest

from sky import core
from sky import global_user_state
from sky.metrics import launch_phases

# Bound at import, before the `recorder` fixture replaces it with a stub: one
# test needs the real emitter while using that fixture's fake session.
_REAL_EMIT = global_user_state._record_admission_event


class _Row(types.SimpleNamespace):
    """A stand-in for a SQLAlchemy Row, which the code reads both ways.

    `row[0]` for the attempt id and `row.queue` for the rest. A plain
    SimpleNamespace supports only the second, and because the recorder is
    wrapped in `@_best_effort` the resulting TypeError is swallowed -- the
    test then fails as "nothing was emitted", which is indistinguishable from
    the feature being switched off.
    """

    def __getitem__(self, index):
        return tuple(vars(self).values())[index]


def _attempt(queue='eng-lq',
             instances_requested=30.0,
             provision_start=20.0,
             cluster_name='train-7'):
    # Field order mirrors the recorder's SELECT, because `row[0]` is read
    # positionally.
    return _Row(attempt_id='a1',
                queue=queue,
                instances_requested=instances_requested,
                provision_start=provision_start,
                cluster_name=cluster_name,
                admitted=None,
                instances_ready=None,
                outcome=None)


@pytest.fixture(name='events')
def _events(monkeypatch):
    """Capture what would be written to the cluster event log."""
    written = []

    def fake(cluster_name, new_status, reason, event_type, **kwargs):
        written.append({
            'cluster': cluster_name,
            'reason': reason,
            'type': event_type,
            **kwargs,
        })

    monkeypatch.setattr(global_user_state, 'add_cluster_event', fake)
    return written


def test_the_row_names_the_queue_and_how_long_the_wait_was(events):
    global_user_state._record_admission_event(_attempt(), 146.57)

    assert len(events) == 1
    assert 'eng-lq' in events[0]['reason']
    # 30.0 -> 146.57 is the wait; the text must carry it, because the whole
    # point of the row is that the reader cannot otherwise place its start.
    assert '1m 56s' in events[0]['reason']


def test_it_is_not_launch_progress(events):
    """LAUNCH_PROGRESS is consumed latest-wins as a managed job's `details`
    column -- "what is this launch waiting on now". A row saying a wait has
    ended would sit there as a stale answer for the rest of the launch, which
    is worse than the silence it would replace."""
    global_user_state._record_admission_event(_attempt(), 146.57)

    assert events[0]['type'] == (
        global_user_state.ClusterEventType.LAUNCH_MILESTONE)
    assert events[0]['type'] != (
        global_user_state.ClusterEventType.LAUNCH_PROGRESS)


def test_the_duration_is_the_one_launch_phases_computes(events):
    """Not subtracted here. The wait is measured from `instances_requested`
    where the cloud stamps it and from `provision_start` where it does not, so
    a number computed by hand would disagree with `t_queue_wait` on exactly the
    clouds that fallback exists for."""
    attempt = _attempt(instances_requested=None, provision_start=20.0)

    global_user_state._record_admission_event(attempt, 146.57)

    # The fallback took provision_start, so the wait is longer than it would
    # have been measured from a request that never happened.
    assert launch_phases.queue_wait_from(attempt) == 20.0
    assert '2m 6s' in events[0]['reason']


def test_an_attempt_with_no_boundary_says_nothing(events):
    """Neither milestone recorded: there is no wait to report, and inventing
    one from the admission alone would measure from an instant that is not a
    boundary of anything."""
    global_user_state._record_admission_event(
        _attempt(instances_requested=None, provision_start=None), 146.57)

    assert events == []


def test_the_queue_name_is_optional(events):
    """A short gated wait is admitted before anything records its queue.

    The queue name comes from the scheduler integration's throttled polls; the
    admission has an extra unthrottled backstop that exists for exactly the
    case where no poll ran after admission -- which is also the case where no
    poll recorded the queue. So this is a real path, not defensive coding, and
    the row is still worth writing: the wait is the number a reader cannot
    reconstruct, the queue is the detail."""
    global_user_state._record_admission_event(_attempt(queue=None), 146.57)

    assert len(events) == 1
    assert 'queue' not in events[0]['reason']


# --- the recorder that decides whether to emit at all ------------------------


@pytest.fixture(name='recorder')
def _recorder(monkeypatch):
    """Drive record_launch_milestone_for_cluster with a fake database.

    `stamped` controls whether the conditional UPDATE matched -- which is the
    whole emit-once mechanism, so it is the thing worth being able to set.
    """
    state = {'stamped': True, 'row': _attempt()}
    emitted = []

    class _Result:

        @property
        def rowcount(self):
            return 1 if state['stamped'] else 0

    class _Session:

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, statement):
            if str(statement).lstrip().upper().startswith('SELECT'):
                return mock.Mock(fetchone=lambda: state['row'])
            return _Result()

        def commit(self):
            pass

    monkeypatch.setattr(global_user_state.orm, 'Session',
                        lambda engine: _Session())
    monkeypatch.setattr(global_user_state._db_manager, 'get_engine',
                        lambda: mock.Mock())
    monkeypatch.setattr(global_user_state, '_record_admission_event',
                        lambda *a, **k: emitted.append(a))
    return state, emitted


def test_admission_emits_once(recorder):
    state, emitted = recorder

    global_user_state.record_launch_milestone_for_cluster(
        'train-7', global_user_state.LaunchMilestone.ADMITTED, 146.57)

    assert len(emitted) == 1


def test_a_resumed_launch_does_not_announce_the_same_admission_twice(recorder):
    """A launch that parks on an external condition resumes into the *same*
    open attempt row, so the second call's UPDATE matches nothing. This is the
    failure the provisioning event has -- it re-fires on resume and makes one
    provisioning look like two -- asserted against the design that avoids it.
    """
    state, emitted = recorder
    state['stamped'] = False

    global_user_state.record_launch_milestone_for_cluster(
        'train-7', global_user_state.LaunchMilestone.ADMITTED, 146.57)

    assert emitted == []


def test_the_other_milestones_emit_nothing(recorder):
    """The positive case above passes just as well if nothing ever emits, so
    this pair is only meaningful together: only ADMITTED writes a row, and
    ADMITTED does."""
    state, emitted = recorder

    for milestone in (global_user_state.LaunchMilestone.INSTANCES_REQUESTED,
                      global_user_state.LaunchMilestone.INSTANCES_READY):
        global_user_state.record_launch_milestone_for_cluster(
            'train-7', milestone, 146.57)

    assert emitted == []

    global_user_state.record_launch_milestone_for_cluster(
        'train-7', global_user_state.LaunchMilestone.ADMITTED, 146.57)
    assert len(emitted) == 1


def test_an_on_cloud_caller_still_files_the_event_under_the_display_name(
        recorder, events):
    """The recorder is reachable with either name -- pod labels carry the
    on-cloud one, and the v1 launch-wait hook has only that. But the event
    table is keyed by the display name at both ends: `add_cluster_event`
    resolves the cluster by it, and the reader fetches by it. Filing under the
    caller's argument drops the event for every such caller, silently, because
    the resolve failure is a debug log and a return.
    """
    state, _ = recorder
    state['row'] = _attempt(cluster_name='train-7')

    # The real emitter, not the fixture's stub: the name it files under is the
    # thing under test.
    with mock.patch.object(global_user_state, '_record_admission_event',
                           _REAL_EMIT):
        global_user_state.record_launch_milestone_for_cluster(
            'train-7-a1b2', global_user_state.LaunchMilestone.ADMITTED, 146.57)

    assert len(events) == 1
    assert events[0]['cluster'] == 'train-7'


# --- retention ----------------------------------------------------------------


def test_every_event_type_has_a_retention_window():
    """A type the sweep has no entry for is retained forever, silently.

    `cleanup_cluster_events_with_retention` takes one type, so the daemon can
    only sweep what it is told about. Nothing errors when a type is missing --
    the rows just accumulate -- so this is what stands between a newly added
    type and an unbounded table.
    """
    covered = set()
    for types in global_user_state.CLUSTER_EVENT_RETENTION_GROUPS.values():
        covered.update(types)

    assert covered == set(global_user_state.ClusterEventType)


# --- the API's tolerance of a type it does not know ---------------------------
#
# The cluster event list is rendered by a dashboard shipped separately from the
# server, so a newer one routinely asks for a type an older server has no rows
# of. Before this, that failed the whole request -- taking the types the server
# *could* have answered with it.


def _capture(monkeypatch):
    seen = {}

    def fake(cluster_name, cluster_hash, event_type, include_timestamps, limit):
        seen['types'] = event_type
        return []

    monkeypatch.setattr(global_user_state, 'get_cluster_events', fake)
    return seen


def test_an_unknown_event_type_does_not_fail_the_request(monkeypatch):
    seen = _capture(monkeypatch)

    core.get_cluster_events(cluster_name='c',
                            event_type='STATUS_CHANGE,NOT_A_REAL_TYPE')

    assert seen['types'] == [global_user_state.ClusterEventType.STATUS_CHANGE]


def test_a_request_of_only_unknown_types_is_still_an_error(monkeypatch):
    """Dropping every name would translate to `type IN ()` -- no rows -- which
    a caller reads as "this cluster has no history" rather than as a mistake.
    """
    _capture(monkeypatch)

    with pytest.raises(ValueError):
        core.get_cluster_events(cluster_name='c', event_type='NOT_A_REAL_TYPE')


def test_the_new_type_is_accepted_here(monkeypatch):
    """The positive arm: the two above pass just as well if the enum member
    does not exist, which is exactly what the plugin is about to ask for."""
    seen = _capture(monkeypatch)

    core.get_cluster_events(cluster_name='c',
                            event_type='STATUS_CHANGE,LAUNCH_MILESTONE')

    assert global_user_state.ClusterEventType.LAUNCH_MILESTONE in seen['types']
