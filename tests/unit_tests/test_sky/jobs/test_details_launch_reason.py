"""Tests for surfacing the cluster launch-progress reason in job details."""
from unittest import mock

from sky.jobs import utils as managed_job_utils


def _job(status='STARTING', **kwargs):
    job = {
        'job_id': 1,
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
        _job(job_id=1, status='STARTING', task_name='train'),
        _job(job_id=2, status='STARTING', task_name='train', pool='p'),
        _job(job_id=3, status='RUNNING', task_name='train'),
        _job(job_id=4, status='STARTING', task_name=None),
    ]


def test_launch_reasons_derive_cluster_name_for_starting_non_pool_jobs():
    expected = managed_job_utils.generate_managed_job_cluster_name('train', 1)
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_event_reasons',
                           return_value={expected: 'Launching (pending: x)'
                                        }) as get_reasons:
        reasons = managed_job_utils._get_launch_reasons_by_job(_jobs())
    # Only job 1 qualifies: 2 is a pool job, 3 is not STARTING, 4 has no
    # task name.
    names, _ = get_reasons.call_args.args
    assert names == [expected]
    assert reasons == {1: 'Launching (pending: x)'}


def test_launch_reasons_skip_lookup_when_no_starting_jobs():
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_event_reasons') as get_reasons:
        assert not managed_job_utils._get_launch_reasons_by_job(
            [_job(status='RUNNING')])
    get_reasons.assert_not_called()


def test_launch_reasons_swallow_db_errors():
    with mock.patch.object(managed_job_utils.global_user_state,
                           'get_latest_cluster_event_reasons',
                           side_effect=RuntimeError('db')):
        assert not managed_job_utils._get_launch_reasons_by_job(_jobs())
