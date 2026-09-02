"""Unit tests for launch_attempt accessors in global_user_state.

These cover the attempt-boundary rules, which are the part of the launch
latency breakdown most easily got wrong: a pause must continue one attempt,
a failover must start a new one, and a row abandoned by a crashed launch must
never be adopted by a later one.
"""
import time

from sky import global_user_state
from sky.skylet import constants
from sky.utils.db import db_utils

_OPEN = global_user_state.LaunchOutcome
_MILESTONE = global_user_state.LaunchMilestone


def _fresh_db(tmp_path, monkeypatch):
    """Point the global state DB at a tmp sqlite file."""
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setattr(
        global_user_state,
        '_db_manager',
        db_utils.DatabaseManager(
            'state',
            global_user_state.create_table,
            post_init_fn=lambda _: global_user_state._sqlite_supports_returning(
            ),
        ),
    )


def _rows():
    engine = global_user_state._db_manager.get_engine()
    import sqlalchemy
    from sqlalchemy import orm
    with orm.Session(engine) as session:
        return session.execute(
            sqlalchemy.select(global_user_state.launch_attempt_table).order_by(
                global_user_state.launch_attempt_table.c.attempt_seq)).all()


def _open(cluster='c', chash='h1', request='req-1', start=100.0, **kw):
    return global_user_state.open_launch_attempt(cluster_name=cluster,
                                                 cluster_hash=chash,
                                                 request_id=request,
                                                 provision_start=start,
                                                 **kw)


def test_pause_resume_continues_one_attempt(tmp_path, monkeypatch):
    """A resumed launch continues its attempt instead of starting another.

    This is the Kueue admission path: the launch parks, its provision call
    unwinds, and it resumes as a fresh call under the same request. Opening a
    second attempt here would split one queue wait into two short ones.
    """
    _fresh_db(tmp_path, monkeypatch)

    first = _open(start=100.0)
    global_user_state.record_launch_milestone(first,
                                              _MILESTONE.INSTANCES_REQUESTED,
                                              110.0)
    # The pause leaves the row open on purpose; the resume re-enters here.
    resumed = _open(start=900.0)

    assert resumed == first, 'resume must continue the in-flight attempt'
    assert len(_rows()) == 1


def test_resume_does_not_restamp_earlier_milestones(tmp_path, monkeypatch):
    """Re-walking the provision path must not overwrite what was measured.

    A resumed launch runs the same code again. If milestones were last-write-
    wins, the resume would move instances_requested forward to just before
    admission and the whole pre-pause wait would vanish.
    """
    _fresh_db(tmp_path, monkeypatch)

    attempt = _open(start=100.0)
    global_user_state.record_launch_milestone(attempt,
                                              _MILESTONE.INSTANCES_REQUESTED,
                                              110.0)
    global_user_state.record_launch_milestone(attempt,
                                              _MILESTONE.INSTANCES_REQUESTED,
                                              900.0)

    row = _rows()[0]
    assert row.instances_requested == 110.0


def test_failover_opens_a_new_attempt_under_same_cluster_hash(
        tmp_path, monkeypatch):
    """Failover keeps the clusters row, so cluster_hash is unchanged.

    The attempts must still be separate: each zone tried is its own
    provisioning attempt, and merging them would report one long segment
    instead of several short failed ones.
    """
    _fresh_db(tmp_path, monkeypatch)

    first = _open(start=100.0)
    global_user_state.close_launch_attempt(first, _OPEN.FAILED)
    second = _open(start=200.0)

    assert second != first
    rows = _rows()
    assert len(rows) == 2
    assert rows[0].cluster_hash == rows[1].cluster_hash
    assert [r.attempt_seq for r in rows] == [0, 1]


def test_open_row_from_a_different_request_is_not_adopted(
        tmp_path, monkeypatch):
    """A crashed launch leaves an open row; a later launch must not resume it.

    Adopting it would date the new attempt from the dead one's provision_start
    and report its entire idle time as a queue wait.
    """
    _fresh_db(tmp_path, monkeypatch)

    orphan = _open(request='req-dead', start=100.0)
    global_user_state.record_launch_milestone(orphan,
                                              _MILESTONE.INSTANCES_REQUESTED,
                                              110.0)
    # Deliberately left open: the process died before closing it.
    later = _open(request='req-new', start=99999.0)

    assert later != orphan
    assert len(_rows()) == 2


def test_attempt_seq_continues_across_a_managed_job_recovery(
        tmp_path, monkeypatch):
    """Recovery tears the cluster down, so the relaunch has a new hash.

    The sequence must keep counting: it is what orders a job's attempts in the
    timeline. Scoping it by cluster_hash would restart at 0 here and make the
    recovery's attempt indistinguishable from the original one.
    """
    _fresh_db(tmp_path, monkeypatch)

    first = _open(chash='h1', request='req-1', start=100.0, job_id=7, task_id=0)
    global_user_state.close_launch_attempt(first, _OPEN.FAILED)
    _open(chash='h2', request='req-2', start=200.0, job_id=7, task_id=0)

    rows = _rows()
    assert [(r.cluster_hash, r.attempt_seq) for r in rows] == [('h1', 0),
                                                               ('h2', 1)]


def test_attempt_seq_is_per_cluster_name_not_global(tmp_path, monkeypatch):
    """Two different clusters must not share one sequence.

    A global sequence would still order correctly but would leak one cluster's
    activity into another's timeline numbering.
    """
    _fresh_db(tmp_path, monkeypatch)

    first = _open(cluster='alice', chash='h1', request='req-1')
    global_user_state.close_launch_attempt(first, _OPEN.SUCCEEDED)
    _open(cluster='bob', chash='h2', request='req-2')

    rows = _rows()
    assert [(r.cluster_name, r.attempt_seq) for r in rows] == [('alice', 0),
                                                               ('bob', 0)]


def test_close_is_idempotent_and_keeps_the_first_outcome(tmp_path, monkeypatch):
    """The sweep must not relabel an attempt that already closed itself."""
    _fresh_db(tmp_path, monkeypatch)

    attempt = _open()
    global_user_state.close_launch_attempt(attempt, _OPEN.SUCCEEDED)
    global_user_state.close_launch_attempt(attempt, _OPEN.ABANDONED)

    assert _rows()[0].outcome == _OPEN.SUCCEEDED.value


def test_a_closed_attempt_is_never_resumed(tmp_path, monkeypatch):
    """Only in-flight rows are resumable, whatever the request id."""
    _fresh_db(tmp_path, monkeypatch)

    first = _open(start=100.0)
    global_user_state.close_launch_attempt(first, _OPEN.SUCCEEDED)
    second = _open(start=200.0)

    assert second != first


def test_milestone_for_cluster_targets_the_live_attempt(tmp_path, monkeypatch):
    """Provisioning code stamps by cluster name, without the attempt id.

    When a crashed launch has left an open row behind, the stamp must land on
    the launch that is actually running, not on the corpse.
    """
    _fresh_db(tmp_path, monkeypatch)

    orphan = _open(request='req-dead', start=100.0)
    live = _open(request='req-new', start=500.0)
    global_user_state.record_launch_milestone_for_cluster(
        'c', _MILESTONE.INSTANCES_REQUESTED, 510.0)

    rows = {r.attempt_id: r for r in _rows()}
    assert rows[live].instances_requested == 510.0
    assert rows[orphan].instances_requested is None


def test_milestone_for_cluster_is_write_once(tmp_path, monkeypatch):
    """A resumed launch re-walks the path and must not restamp.

    Restamping is how the pre-pause wait would be erased: the milestone would
    move forward to just before admission and the queue wait would read zero.
    """
    _fresh_db(tmp_path, monkeypatch)

    attempt = _open(start=100.0)
    global_user_state.record_launch_milestone_for_cluster(
        'c', _MILESTONE.INSTANCES_REQUESTED, 110.0)
    global_user_state.record_launch_milestone_for_cluster(
        'c', _MILESTONE.INSTANCES_REQUESTED, 900.0)

    assert _rows()[0].instances_requested == 110.0


def test_milestone_for_cluster_is_a_noop_with_nothing_in_flight(
        tmp_path, monkeypatch):
    """Callers must not have to guard, and a closed attempt is not reopened."""
    _fresh_db(tmp_path, monkeypatch)

    attempt = _open(start=100.0)
    global_user_state.close_launch_attempt(attempt, _OPEN.SUCCEEDED)
    global_user_state.record_launch_milestone_for_cluster(
        'c', _MILESTONE.INSTANCES_REQUESTED, 200.0)

    assert _rows()[0].instances_requested is None


def test_sweep_closes_only_in_flight_attempts(tmp_path, monkeypatch):
    """The sweep must claim the abandoned rows and leave finished ones alone.

    Relabelling a completed attempt would both lose its real outcome and
    report a lost measurement that was never lost.
    """
    _fresh_db(tmp_path, monkeypatch)

    done = _open(cluster='a', chash='h1', request='r1')
    global_user_state.close_launch_attempt(done, _OPEN.SUCCEEDED)
    failed = _open(cluster='b', chash='h2', request='r2')
    global_user_state.close_launch_attempt(failed, _OPEN.FAILED)
    stranded = _open(cluster='d', chash='h3', request='r3')

    assert global_user_state.sweep_abandoned_launch_attempts() == 1

    outcomes = {r.attempt_id: r.outcome for r in _rows()}
    assert outcomes[done] == _OPEN.SUCCEEDED.value
    assert outcomes[failed] == _OPEN.FAILED.value
    assert outcomes[stranded] == _OPEN.ABANDONED.value


def test_swept_attempt_is_not_resumed_by_a_later_launch(tmp_path, monkeypatch):
    """After the sweep, nothing is resumable -- even under the same request.

    This is the sweep's other job: a restart must not let a new launch inherit
    a dead attempt's start time and report its downtime as a queue wait.
    """
    _fresh_db(tmp_path, monkeypatch)

    stranded = _open(request='req-1', start=100.0)
    global_user_state.sweep_abandoned_launch_attempts()
    later = _open(request='req-1', start=99999.0)

    assert later != stranded


def test_milestone_can_be_stamped_by_the_on_cloud_name(tmp_path, monkeypatch):
    """Pod labels carry the on-cloud name, not the display one.

    Requiring the display name would mean every scheduler plugin resolving it
    back before it could record anything.
    """
    _fresh_db(tmp_path, monkeypatch)

    global_user_state.open_launch_attempt(cluster_name='c',
                                          cluster_hash='h1',
                                          request_id='r1',
                                          provision_start=100.0,
                                          cluster_name_on_cloud='c-abc123')
    global_user_state.record_launch_milestone_for_cluster(
        'c-abc123', _MILESTONE.ADMITTED, 700.0)

    assert _rows()[0].admitted == 700.0
