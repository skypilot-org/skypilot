"""Unit tests for job cancel ownership enforcement."""
from unittest import mock

import pytest

from sky.jobs import utils


def _make_cancel_jobs_by_id_mocks(job_statuses, job_owners, job_workspaces):
    """Return a context manager that patches the managed_job_state calls."""

    def get_status(job_id):
        return job_statuses.get(job_id)

    def get_user_hashes_for_jobs(job_ids):
        return {jid: job_owners[jid] for jid in job_ids if jid in job_owners}

    def get_workspace(job_id):
        return job_workspaces.get(job_id, 'default')

    def get_nonterminal_job_ids_by_name(name, user_hash=None, all_users=False):
        return []

    def is_legacy_controller_process(job_id):
        return False

    def set_pending_cancelled(job_id):
        return False

    return mock.patch.multiple(
        'sky.jobs.utils.managed_job_state',
        get_status=mock.Mock(side_effect=get_status),
        get_user_hashes_for_jobs=mock.Mock(
            side_effect=get_user_hashes_for_jobs),
        get_workspace=mock.Mock(side_effect=get_workspace),
        get_nonterminal_job_ids_by_name=mock.Mock(
            side_effect=get_nonterminal_job_ids_by_name),
        is_legacy_controller_process=mock.Mock(
            side_effect=is_legacy_controller_process),
        set_pending_cancelled=mock.Mock(side_effect=set_pending_cancelled),
    )


@mock.patch('sky.jobs.utils.update_managed_jobs_statuses')
def test_cancel_own_job_succeeds(mock_update):
    """A user can cancel a job they own."""
    from sky.jobs.state import ManagedJobStatus

    statuses = {1: ManagedJobStatus.RUNNING}
    owners = {1: 'user-abc'}
    workspaces = {1: 'default'}

    with _make_cancel_jobs_by_id_mocks(statuses, owners, workspaces):
        with mock.patch('pathlib.Path.touch'), \
             mock.patch('filelock.FileLock'):
            msg = utils.cancel_jobs_by_id(
                job_ids=[1],
                current_workspace='default',
                requester_user_hash='user-abc',
            )

    assert 'scheduled to be cancelled' in msg
    assert 'Permission denied' not in msg


@mock.patch('sky.jobs.utils.update_managed_jobs_statuses')
def test_cancel_other_user_job_fails(mock_update):
    """A non-owner cannot cancel another user's job."""
    from sky.jobs.state import ManagedJobStatus

    statuses = {2: ManagedJobStatus.RUNNING}
    owners = {2: 'user-owner'}
    workspaces = {2: 'default'}

    with _make_cancel_jobs_by_id_mocks(statuses, owners, workspaces):
        msg = utils.cancel_jobs_by_id(
            job_ids=[2],
            current_workspace='default',
            requester_user_hash='user-intruder',
        )

    assert 'Permission denied' in msg
    assert 'scheduled to be cancelled' not in msg


@mock.patch('sky.jobs.utils.update_managed_jobs_statuses')
def test_admin_can_cancel_any_job(mock_update):
    """Admin (requester_user_hash=None) can cancel any job."""
    from sky.jobs.state import ManagedJobStatus

    statuses = {3: ManagedJobStatus.RUNNING}
    owners = {3: 'user-owner'}
    workspaces = {3: 'default'}

    with _make_cancel_jobs_by_id_mocks(statuses, owners, workspaces):
        with mock.patch('pathlib.Path.touch'), \
             mock.patch('filelock.FileLock'):
            msg = utils.cancel_jobs_by_id(
                job_ids=[3],
                current_workspace='default',
                requester_user_hash=None,
            )

    assert 'scheduled to be cancelled' in msg
    assert 'Permission denied' not in msg


@mock.patch('sky.jobs.utils.update_managed_jobs_statuses')
def test_cancel_mix_own_and_other(mock_update):
    """Partial cancel: own jobs succeed, other-user jobs are reported."""
    from sky.jobs.state import ManagedJobStatus

    statuses = {
        4: ManagedJobStatus.RUNNING,
        5: ManagedJobStatus.RUNNING,
    }
    owners = {
        4: 'user-requester',
        5: 'user-other',
    }
    workspaces = {4: 'default', 5: 'default'}

    with _make_cancel_jobs_by_id_mocks(statuses, owners, workspaces):
        with mock.patch('pathlib.Path.touch'), \
             mock.patch('filelock.FileLock'):
            msg = utils.cancel_jobs_by_id(
                job_ids=[4, 5],
                current_workspace='default',
                requester_user_hash='user-requester',
            )

    assert 'scheduled to be cancelled' in msg
    assert 'Permission denied' in msg
    assert '5' in msg


def test_cancel_nonexistent_job_skipped():
    """Canceling a nonexistent job ID is silently skipped."""
    statuses = {}
    owners = {}
    workspaces = {}

    with _make_cancel_jobs_by_id_mocks(statuses, owners, workspaces):
        msg = utils.cancel_jobs_by_id(
            job_ids=[999],
            current_workspace='default',
            requester_user_hash='user-abc',
        )

    assert 'No job to cancel' in msg
    assert 'Permission denied' not in msg


@mock.patch('sky.jobs.utils.update_managed_jobs_statuses')
def test_all_users_cancel_bypasses_ownership(mock_update):
    """cancel_jobs_by_id with requester_user_hash=None (admin) respects all_users."""
    from sky.jobs.state import ManagedJobStatus

    statuses = {6: ManagedJobStatus.RUNNING}
    owners = {6: 'user-owner'}
    workspaces = {6: 'default'}

    with _make_cancel_jobs_by_id_mocks(statuses, owners, workspaces):
        with mock.patch('pathlib.Path.touch'), \
             mock.patch('filelock.FileLock'):
            msg = utils.cancel_jobs_by_id(
                job_ids=[6],
                all_users=True,
                current_workspace='default',
                requester_user_hash=None,
            )

    assert 'scheduled to be cancelled' in msg
