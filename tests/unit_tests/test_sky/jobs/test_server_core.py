"""Tests for sky.jobs.server.core."""
from unittest import mock

import pytest

from sky import backends
from sky.jobs.server import core as jobs_core


def _forwarded_tail(tail):
    """Call ``jobs_core.tail_logs`` with ``tail`` (mocking out the controller
    restart / backend / runner) and return the ``tail`` value forwarded to
    ``tail_managed_job_logs``."""
    fake_backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    fake_runner = mock.MagicMock()
    fake_runner.tail_managed_job_logs.return_value = 0
    with mock.patch.object(jobs_core, '_maybe_restart_controller',
                           return_value=mock.MagicMock()), \
         mock.patch.object(jobs_core.backend_utils,
                           'get_backend_from_handle',
                           return_value=fake_backend), \
         mock.patch.object(jobs_core.managed_job_runner,
                           'current',
                           return_value=fake_runner):
        jobs_core.tail_logs(name=None,
                            job_id=1,
                            follow=False,
                            controller=False,
                            refresh=False,
                            tail=tail)
    fake_runner.tail_managed_job_logs.assert_called_once()
    return fake_runner.tail_managed_job_logs.call_args.kwargs['tail']


@pytest.mark.parametrize(
    ('given', 'expected'),
    [
        (0, None),  # dashboard download button's "all lines" sentinel
        (-1, None),  # `sky jobs logs --tail -1`
        (None, None),  # no tail -> all
        (200, 200),  # positive tail forwarded unchanged
        (5000, 5000),
    ])
def test_tail_logs_normalizes_non_positive_tail(given, expected):
    """A non-positive tail (0 / -1) means "all lines" and must be normalized
    to None before reaching the backward-seek tail reader (which asserts
    tail > 0). Otherwise the dashboard download (tail=0) raises
    AssertionError and produces an empty log."""
    assert _forwarded_tail(given) == expected


class TestCheckJobGroupAttachment:
    """`launch(parent_job_id=...)` pre-checks, and the consolidation-only rule."""

    @staticmethod
    def _run(parent_job_id,
             parent_task_id=None,
             root_job_id=None,
             *,
             explicit=False,
             consolidation=True,
             status=None,
             parent_workspace='default',
             parent_root=None,
             active_workspace='default'):
        status_enum = (None if status is None else
                       jobs_core.managed_job_state.ManagedJobStatus(status))
        parent_row = (None if status is None else
                      jobs_core.managed_job_state.JobInfoRow(
                          job_id=parent_job_id,
                          name='parent',
                          workspace=parent_workspace,
                          user_hash='u',
                          root_job_id=parent_root,
                          parent_job_id=None,
                          parent_task_id=None))
        with mock.patch.object(jobs_core.managed_job_utils,
                               'is_consolidation_mode',
                               return_value=consolidation), \
             mock.patch.object(jobs_core.managed_job_state, 'get_status',
                               return_value=status_enum), \
             mock.patch.object(jobs_core.managed_job_state,
                               'get_job_info_row',
                               return_value=parent_row), \
             mock.patch.object(jobs_core.skypilot_config,
                               'get_active_workspace',
                               return_value=active_workspace):
            return jobs_core._check_job_group_attachment(
                parent_job_id, parent_task_id, root_job_id, explicit)

    def test_no_parent_records_nothing(self):
        assert self._run(None) == (None, None, None)

    def test_task_without_job_rejected(self):
        with pytest.raises(ValueError, match='requires parent_job_id'):
            self._run(None, parent_task_id=1)

    def test_running_parent_in_same_workspace_ok(self):
        assert self._run(42, 1, 42, status='RUNNING') == (42, 1, 42)

    def test_root_comes_from_the_parent_row(self):
        # The parent's row decides the tree, whatever the client sent.
        assert self._run(42, 1, status='RUNNING') == (42, 1, 42)
        assert self._run(42, 1, 42, status='RUNNING') == (42, 1, 42)
        # Parent is itself a dynamic member of 7: the child roots at 7.
        assert self._run(42, 1, status='RUNNING', parent_root=7) == (42, 1, 7)
        # Client drift (claims 99) is corrected to the row's tree.
        assert self._run(42, 1, 99, status='RUNNING',
                         parent_root=7) == (42, 1, 7)
        assert self._run(42, 1, 99, status='RUNNING') == (42, 1, 42)

    def test_terminal_but_not_cancelled_parent_ok(self):
        # Attaching to a finished group is allowed (visibility still useful).
        assert self._run(42, status='SUCCEEDED') == (42, None, 42)

    @pytest.mark.parametrize('status', ['CANCELLING', 'CANCELLED'])
    def test_cancelling_or_cancelled_parent_rejected(self, status):
        with pytest.raises(ValueError, match=status):
            self._run(42, status=status)

    def test_missing_parent_rejected(self):
        with pytest.raises(ValueError, match='no such managed job'):
            self._run(42, status=None)

    def test_other_workspace_rejected(self):
        with pytest.raises(ValueError, match='workspace'):
            self._run(42,
                      status='RUNNING',
                      parent_workspace='team-a',
                      active_workspace='team-b')

    def test_legacy_parent_counts_as_default_workspace(self):
        # JobInfoRow resolves a NULL workspace to 'default' (same as cancel),
        # so a legacy parent is checked, not exempt.
        assert self._run(42,
                         status='RUNNING',
                         parent_workspace='default',
                         active_workspace='default') == (42, None, 42)
        with pytest.raises(ValueError, match='workspace'):
            self._run(42,
                      status='RUNNING',
                      parent_workspace='default',
                      active_workspace='team-b')

    def test_non_consolidation_drops_automatic_attachment(self):
        # A watcher's nested launch on a laptop setup keeps working, as a
        # top-level job, exactly as before dynamic job groups existed.
        assert self._run(42, 1, 42, consolidation=False,
                         explicit=False) == (None, None, None)

    def test_non_consolidation_rejects_explicit_attachment(self):
        with pytest.raises(jobs_core.exceptions.NotSupportedError,
                           match='consolidation mode'):
            self._run(42, consolidation=False, explicit=True)
