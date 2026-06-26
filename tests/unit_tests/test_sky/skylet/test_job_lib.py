"""Unit tests for skylet job_lib — PENDING grace period."""
import time
from unittest import mock

import pytest

from sky.skylet import job_lib


@pytest.fixture(autouse=True)
def _mock_lock_path(tmp_path):
    """Route file locks to a temp directory to avoid side effects."""
    with mock.patch.object(
            job_lib,
            '_get_lock_path',
            side_effect=lambda jid: str(tmp_path / f'.job_{jid}.lock')):
        yield


class TestPendingGracePeriod:
    """Tests for the PENDING grace period in update_job_status().

    When a launched job stays in PENDING for longer than
    _PENDING_GRACE_PERIOD and the driver process is not running, it should
    transition to FAILED_DRIVER so that is_cluster_idle() returns True and
    autodown can proceed.
    See https://github.com/skypilot-org/skypilot/issues/9815.
    """

    # pylint: disable=protected-access

    def _make_job_record(self, job_id, status, submitted_at, pid=0):
        return {
            'job_id': job_id,
            'job_name': 'test',
            'username': 'user',
            'submitted_at': submitted_at,
            'status': status,
            'run_timestamp': '2024-01-01',
            'start_at': -1,
            'end_at': None,
            'resources': None,
            'pid': pid,
            'metadata': {},
        }

    def _make_pending_job(self, created_time, submit=0):
        return {
            'created_time': created_time,
            'submit': submit,
            'run_cmd': 'echo test',
        }

    @mock.patch.object(job_lib, '_set_status_no_lock')
    @mock.patch.object(job_lib, '_get_pending_job')
    @mock.patch.object(job_lib, '_get_jobs_by_ids')
    @mock.patch.object(job_lib,
                       '_is_job_driver_process_running',
                       return_value=False)
    def test_stale_launched_pending_job_transitions_to_failed_driver(
            self, mock_driver_running, mock_get_jobs, mock_get_pending,
            mock_set_status):
        """A launched PENDING job past the grace period should fail.

        Simulates a job whose driver was started (submit > 0, pid > 0) but
        crashed during early init before updating status past PENDING.
        """
        del mock_driver_running  # unused: patched globally
        now = time.time()
        stale_submitted_at = now - job_lib._PENDING_GRACE_PERIOD - 60

        mock_get_jobs.return_value = [
            self._make_job_record(1,
                                  job_lib.JobStatus.PENDING,
                                  stale_submitted_at,
                                  pid=42),
        ]
        mock_get_pending.return_value = self._make_pending_job(
            created_time=int(stale_submitted_at),
            submit=int(stale_submitted_at + 1))

        with mock.patch('psutil.boot_time',
                        return_value=stale_submitted_at - 100):
            statuses = job_lib.update_job_status([1])

        assert statuses == [job_lib.JobStatus.FAILED_DRIVER]
        mock_set_status.assert_called_once_with(1,
                                                job_lib.JobStatus.FAILED_DRIVER)

    @mock.patch.object(job_lib, '_set_status_no_lock')
    @mock.patch.object(job_lib, '_get_pending_job')
    @mock.patch.object(job_lib, '_get_jobs_by_ids')
    @mock.patch.object(job_lib,
                       '_is_job_driver_process_running',
                       return_value=True)
    def test_recent_launched_pending_job_stays_pending(self,
                                                       mock_driver_running,
                                                       mock_get_jobs,
                                                       mock_get_pending,
                                                       mock_set_status):
        """A launched PENDING job within the grace period should stay.

        Simulates a recently launched job whose driver is still running
        but hasn't updated status past PENDING yet. This is normal.
        """
        del mock_driver_running  # unused: patched globally
        now = time.time()
        recent_submitted_at = now - 30  # 30 seconds, well within grace period

        mock_get_jobs.return_value = [
            self._make_job_record(1,
                                  job_lib.JobStatus.PENDING,
                                  recent_submitted_at,
                                  pid=42),
        ]
        mock_get_pending.return_value = self._make_pending_job(
            created_time=int(recent_submitted_at),
            submit=int(recent_submitted_at + 1))

        with mock.patch('psutil.boot_time',
                        return_value=recent_submitted_at - 100):
            statuses = job_lib.update_job_status([1])

        assert statuses == [job_lib.JobStatus.PENDING]
        mock_set_status.assert_not_called()

    @mock.patch.object(job_lib, '_set_status_no_lock')
    @mock.patch.object(job_lib, '_get_pending_job')
    @mock.patch.object(job_lib, '_get_jobs_by_ids')
    @mock.patch.object(job_lib,
                       '_is_job_driver_process_running',
                       return_value=True)
    def test_stale_pending_job_with_running_driver_stays_pending(
            self, mock_driver_running, mock_get_jobs, mock_get_pending,
            mock_set_status):
        """A PENDING job past grace period with a running driver stays."""
        del mock_driver_running  # unused: patched globally
        now = time.time()
        stale_submitted_at = now - job_lib._PENDING_GRACE_PERIOD - 60

        mock_get_jobs.return_value = [
            self._make_job_record(1,
                                  job_lib.JobStatus.PENDING,
                                  stale_submitted_at,
                                  pid=12345),
        ]
        mock_get_pending.return_value = self._make_pending_job(
            created_time=int(stale_submitted_at),
            submit=int(stale_submitted_at + 1))

        with mock.patch('psutil.boot_time',
                        return_value=stale_submitted_at - 100):
            statuses = job_lib.update_job_status([1])

        # The driver process is running, so the job should stay PENDING.
        assert statuses == [job_lib.JobStatus.PENDING]
        mock_set_status.assert_not_called()

    @mock.patch.object(job_lib, '_set_status_no_lock')
    @mock.patch.object(job_lib, '_get_pending_job', return_value=None)
    @mock.patch.object(job_lib, '_get_jobs_by_ids')
    @mock.patch.object(job_lib,
                       '_is_job_driver_process_running',
                       return_value=False)
    def test_stale_pending_no_pending_entry_fails_via_status_none(
            self, mock_driver_running, mock_get_jobs, mock_get_pending,
            mock_set_status):
        """No pending_jobs entry: status stays None, then FAILED_DRIVER."""
        # unused: patched globally
        del mock_driver_running, mock_get_pending, mock_set_status
        now = time.time()
        stale_submitted_at = now - job_lib._PENDING_GRACE_PERIOD - 60

        mock_get_jobs.return_value = [
            self._make_job_record(1,
                                  job_lib.JobStatus.PENDING,
                                  stale_submitted_at,
                                  pid=0),
        ]

        with mock.patch('psutil.boot_time',
                        return_value=stale_submitted_at - 100):
            statuses = job_lib.update_job_status([1])

        # status remains None -> falls through to the `status is None` branch
        # which sets FAILED_DRIVER for nonterminal original_status.
        assert statuses == [job_lib.JobStatus.FAILED_DRIVER]

    @mock.patch.object(job_lib, '_set_status_no_lock')
    @mock.patch.object(job_lib, '_get_pending_job')
    @mock.patch.object(job_lib, '_get_jobs_by_ids')
    @mock.patch.object(job_lib,
                       '_is_job_driver_process_running',
                       return_value=False)
    def test_stale_pending_legacy_pid_transitions(self, mock_driver_running,
                                                  mock_get_jobs,
                                                  mock_get_pending,
                                                  mock_set_status):
        """Legacy jobs (pid=-1) stuck in PENDING should also fail."""
        del mock_driver_running  # unused: patched globally
        now = time.time()
        stale_submitted_at = now - job_lib._PENDING_GRACE_PERIOD - 60

        mock_get_jobs.return_value = [
            self._make_job_record(1,
                                  job_lib.JobStatus.PENDING,
                                  stale_submitted_at,
                                  pid=-1),
        ]
        mock_get_pending.return_value = self._make_pending_job(
            created_time=int(stale_submitted_at),
            submit=int(stale_submitted_at + 1))

        with mock.patch('psutil.boot_time',
                        return_value=stale_submitted_at - 100):
            statuses = job_lib.update_job_status([1])

        assert statuses == [job_lib.JobStatus.FAILED_DRIVER]
        mock_set_status.assert_called_once_with(1,
                                                job_lib.JobStatus.FAILED_DRIVER)

    @mock.patch.object(job_lib, '_set_status_no_lock')
    @mock.patch.object(job_lib, '_get_pending_job')
    @mock.patch.object(job_lib, '_get_jobs_by_ids')
    @mock.patch.object(job_lib,
                       '_is_job_driver_process_running',
                       return_value=True)
    def test_launched_pending_job_at_boundary_stays_pending(
            self, mock_driver_running, mock_get_jobs, mock_get_pending,
            mock_set_status):
        """A launched job inside the grace period boundary stays PENDING."""
        del mock_driver_running  # unused: patched globally
        now = time.time()
        # 1 minute of slack to account for the small time delta between
        # this `now` and the `time.time()` call inside update_job_status.
        boundary_submitted_at = now - job_lib._PENDING_GRACE_PERIOD + 60

        mock_get_jobs.return_value = [
            self._make_job_record(1,
                                  job_lib.JobStatus.PENDING,
                                  boundary_submitted_at,
                                  pid=42),
        ]
        mock_get_pending.return_value = self._make_pending_job(
            created_time=int(boundary_submitted_at),
            submit=int(boundary_submitted_at + 1))

        with mock.patch('psutil.boot_time',
                        return_value=boundary_submitted_at - 100):
            statuses = job_lib.update_job_status([1])

        assert statuses == [job_lib.JobStatus.PENDING]
        mock_set_status.assert_not_called()

    @mock.patch.object(job_lib, '_set_status_no_lock')
    @mock.patch.object(job_lib, '_get_pending_job')
    @mock.patch.object(job_lib, '_get_jobs_by_ids')
    @mock.patch.object(job_lib,
                       '_is_job_driver_process_running',
                       return_value=False)
    def test_queued_pending_job_not_failed_even_if_stale(
            self, mock_driver_running, mock_get_jobs, mock_get_pending,
            mock_set_status):
        """A queued job (submit=0) waiting >10min should NOT be failed."""
        del mock_driver_running  # unused: patched globally
        now = time.time()
        stale_submitted_at = now - job_lib._PENDING_GRACE_PERIOD - 60

        mock_get_jobs.return_value = [
            self._make_job_record(1,
                                  job_lib.JobStatus.PENDING,
                                  stale_submitted_at,
                                  pid=0),
        ]
        # submit=0: the job is still queued, not yet launched.
        mock_get_pending.return_value = self._make_pending_job(
            created_time=int(stale_submitted_at), submit=0)

        with mock.patch('psutil.boot_time',
                        return_value=stale_submitted_at - 100):
            statuses = job_lib.update_job_status([1])

        # The job is legitimately waiting in the queue.
        assert statuses == [job_lib.JobStatus.PENDING]
        mock_set_status.assert_not_called()
