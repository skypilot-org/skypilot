"""Tests for surfacing the cluster launch-progress reason in job details."""
from unittest import mock

from sky.jobs import utils as managed_job_utils


def _job(status='STARTING', **kwargs):
    job = {
        'job_id': 1,
        'task_id': 0,
        'last_recovered_at': 0,
        'submitted_at': 0,
        'status': status,
        'schedule_state': 'ALIVE',
        'failure_reason': None,
        'priority': 500,
        'task_name': 'train',
        'pool': None,
    }
    job.update(kwargs)
    return job


def test_launch_reason_fills_details_when_nothing_else_applies():
    job = _job()
    managed_job_utils._format_job_details(
        job=job,
        highest_blocking_priority=0,
        launch_reason='Launching  (pending:\n QOSGrpGRES)')
    assert job['details'] == 'Launching (pending: QOSGrpGRES)'


def test_failure_reason_wins_over_launch_reason():
    job = _job(failure_reason='boom')
    managed_job_utils._format_job_details(job=job,
                                          highest_blocking_priority=0,
                                          launch_reason='Launching (x)')
    assert job['details'] == 'Failure: boom'


def test_no_launch_reason_leaves_details_empty():
    job = _job()
    managed_job_utils._format_job_details(job=job, highest_blocking_priority=0)
    assert job['details'] is None


def _jobs():
    return [
        _job(job_id=1, task_id=0, status='STARTING', task_name='train'),
        _job(job_id=2,
             task_id=0,
             status='STARTING',
             task_name='train',
             pool='p'),
        _job(job_id=3, task_id=0, status='RUNNING', task_name='train'),
        _job(job_id=4, task_id=0, status='STARTING', task_name=None),
    ]


def test_launch_reasons_derive_cluster_name_for_starting_non_pool_jobs():
    expected = managed_job_utils.generate_managed_job_cluster_name('train', 1)
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_events',
                           return_value={
                               expected: ('Launching (pending: x)', 100)
                           }) as get_reasons:
        reasons = managed_job_utils._get_launch_reasons_by_task(_jobs())
    # Only job 1 qualifies: 2 is a pool job, 3 is not STARTING, 4 has no
    # task name.
    names, _ = get_reasons.call_args.args
    assert names == [expected]
    assert reasons == {(1, 0): 'Launching (pending: x)'}


def test_launch_reasons_are_per_task_in_a_job_group():
    # One job group: task 0 STARTING, task 1 RUNNING, task 2 STARTING. Only the
    # STARTING tasks get a reason, each from its own cluster.
    jobs = [
        _job(job_id=7, task_id=0, status='STARTING', task_name='grp-0'),
        _job(job_id=7, task_id=1, status='RUNNING', task_name='grp-1'),
        _job(job_id=7, task_id=2, status='STARTING', task_name='grp-2'),
    ]
    c0 = managed_job_utils.generate_managed_job_cluster_name('grp-0', 7)
    c2 = managed_job_utils.generate_managed_job_cluster_name('grp-2', 7)
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_events',
                           return_value={
                               c0: ('Launching (pending: Resources)', 100),
                               c2: ('Launching (pending: Priority)', 100),
                           }) as get_reasons:
        reasons = managed_job_utils._get_launch_reasons_by_task(jobs)
    names, _ = get_reasons.call_args.args
    assert names == [c0, c2]
    assert reasons == {
        (7, 0): 'Launching (pending: Resources)',
        (7, 2): 'Launching (pending: Priority)',
    }
    assert (7, 1) not in reasons


def test_launch_reasons_skip_lookup_when_no_starting_jobs():
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_events') as get_reasons:
        assert not managed_job_utils._get_launch_reasons_by_task(
            [_job(status='RUNNING')])
    get_reasons.assert_not_called()


def test_launch_reasons_ignore_events_from_an_earlier_attempt():
    # The cluster name is reused across recovery attempts: an event older
    # than the current attempt's start must not be shown as its reason.
    cluster = managed_job_utils.generate_managed_job_cluster_name('train', 1)
    fresh = _job(job_id=1,
                 task_id=0,
                 status='STARTING',
                 task_name='train',
                 last_recovered_at=500,
                 submitted_at=100)
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_events',
                           return_value={cluster: ('stale reason', 499)}):
        assert not managed_job_utils._get_launch_reasons_by_task([fresh])
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_events',
                           return_value={cluster: ('current reason', 501)}):
        assert managed_job_utils._get_launch_reasons_by_task([fresh]) == {
            (1, 0): 'current reason'
        }
    # No recovery yet: submission time is the attempt start.
    first = _job(job_id=1,
                 task_id=0,
                 status='STARTING',
                 task_name='train',
                 last_recovered_at=None,
                 submitted_at=300)
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_events',
                           return_value={cluster: ('before submit', 299)}):
        assert not managed_job_utils._get_launch_reasons_by_task([first])


def test_launch_reasons_swallow_db_errors():
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_events',
                           side_effect=RuntimeError('db')):
        assert not managed_job_utils._get_launch_reasons_by_task(_jobs())


def test_a_sibling_task_recovery_reason_does_not_shadow_a_launch_reason():
    """In a job group each task has its own row, but recovery and pending
    reasons are looked up by job id alone -- so a STARTING task used to
    inherit a RECOVERING sibling's reason, and since that branch is checked
    first, its own launch reason was hidden. The reason belongs to the task
    that is in that status.
    """
    starting = _job(job_id=7, task_id=1, status='STARTING')
    managed_job_utils._format_job_details(
        job=starting,
        highest_blocking_priority=0,
        recovery_reason='OOMKilled',
        launch_reason='Launching (pending: Resources; partition: dev)')
    assert starting['details'] == (
        'Launching (pending: Resources; partition: dev)')


def test_a_recovering_task_still_reports_its_recovery_reason():
    """The mirror: the guard must not silence the row the reason is about."""
    recovering = _job(job_id=7, task_id=0, status='RECOVERING')
    managed_job_utils._format_job_details(job=recovering,
                                          highest_blocking_priority=0,
                                          recovery_reason='OOMKilled')
    assert recovering['details'] == 'Recovering: OOMKilled'


def test_a_sibling_pending_reason_does_not_shadow_a_launch_reason():
    starting = _job(job_id=7, task_id=1, status='STARTING')
    managed_job_utils._format_job_details(
        job=starting,
        highest_blocking_priority=0,
        pending_reason='Waiting for a launch slot',
        launch_reason='Launching (pending: Priority)')
    assert starting['details'] == 'Launching (pending: Priority)'


def test_a_cancellation_wins_over_a_launch_reason():
    """Cancelling a job cancels every task in it, so unlike the recovery and
    pending reasons this one is right on a sibling's row too -- it is checked
    first and deliberately not gated on the row's own status.
    """
    starting = _job(job_id=7, task_id=1, status='STARTING')
    managed_job_utils._format_job_details(
        job=starting,
        highest_blocking_priority=0,
        cancel_reason='Cancellation requested by alice',
        launch_reason='Launching (pending: Resources)')
    assert starting['details'] == 'Cancellation requested by alice'
