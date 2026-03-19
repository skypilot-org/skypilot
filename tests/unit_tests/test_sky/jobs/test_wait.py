"""Unit tests for sky.jobs.server.core.wait()."""
import time
from typing import Dict, List, Optional, Tuple
from unittest import mock

import pytest

from sky import exceptions
from sky.jobs.server import core as jobs_core
from sky.jobs.state import ManagedJobStatus
from sky.schemas.api import responses


def _make_record(
    job_id: int,
    status: Optional[ManagedJobStatus],
    job_name: str = 'train',
    task_id: int = 0,
    task_name: Optional[str] = None,
) -> responses.ManagedJobRecord:
    return responses.ManagedJobRecord(
        job_id=job_id,
        job_name=job_name,
        status=status,
        task_id=task_id,
        task_name=task_name or job_name,
    )


def _mock_queue_v2_api(
    side_effect: List[List[responses.ManagedJobRecord]],) -> mock.MagicMock:
    """Create a mock for queue_v2_api that returns records from side_effect.

    Each call pops the first element from side_effect and wraps it in the
    expected (records, total, status_counts, total_no_filter) tuple.
    """
    call_idx = {'i': 0}

    def _side_effect(
        **kwargs
    ) -> Tuple[List[responses.ManagedJobRecord], int, Dict[str, int], int]:
        idx = call_idx['i']
        call_idx['i'] += 1
        records = side_effect[idx]
        return records, len(records), {}, len(records)

    return mock.MagicMock(side_effect=_side_effect)


# ──────────────────────────────────────────────────────────────────────
# Validation tests
# ──────────────────────────────────────────────────────────────────────


class TestWaitValidation:

    def test_both_name_and_job_id_raises(self):
        with pytest.raises(ValueError, match='Cannot specify both'):
            jobs_core.wait(name='foo', job_id=1, timeout=None, poll_interval=15)

    def test_neither_name_nor_job_id_raises(self):
        with pytest.raises(ValueError, match='Must specify either'):
            jobs_core.wait(name=None,
                           job_id=None,
                           timeout=None,
                           poll_interval=15)

    def test_poll_interval_too_small_raises(self):
        with pytest.raises(ValueError, match='at least 5 seconds'):
            jobs_core.wait(name=None, job_id=1, timeout=None, poll_interval=2)


# ──────────────────────────────────────────────────────────────────────
# Single-task tests
# ──────────────────────────────────────────────────────────────────────


class TestWaitSingleTask:

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_already_succeeded(self, mock_sleep, mock_queue):
        mock_queue.return_value = ([
            _make_record(1, ManagedJobStatus.SUCCEEDED)
        ], 1, {}, 1)

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=15)

        assert result == exceptions.JobExitCode.SUCCEEDED
        mock_sleep.assert_not_called()

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_already_failed(self, mock_sleep, mock_queue):
        mock_queue.return_value = ([_make_record(1, ManagedJobStatus.FAILED)],
                                   1, {}, 1)

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=15)

        assert result == exceptions.JobExitCode.FAILED
        mock_sleep.assert_not_called()

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_transitions_to_succeeded(self, mock_sleep, mock_queue):
        mock_queue.side_effect = [
            ([_make_record(1, ManagedJobStatus.RUNNING)], 1, {}, 1),
            ([_make_record(1, ManagedJobStatus.SUCCEEDED)], 1, {}, 1),
        ]

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.SUCCEEDED
        mock_sleep.assert_called_once_with(5)

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_transitions_to_cancelled(self, mock_sleep, mock_queue):
        mock_queue.side_effect = [
            ([_make_record(1, ManagedJobStatus.RUNNING)], 1, {}, 1),
            ([_make_record(1, ManagedJobStatus.CANCELLED)], 1, {}, 1),
        ]

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.CANCELLED

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_job_not_found(self, mock_sleep, mock_queue):
        mock_queue.return_value = ([], 0, {}, 0)

        with pytest.raises(ValueError, match='not found'):
            jobs_core.wait(name=None, job_id=99, timeout=None, poll_interval=5)

    @pytest.mark.parametrize('status', [
        ManagedJobStatus.FAILED,
        ManagedJobStatus.FAILED_SETUP,
        ManagedJobStatus.FAILED_PRECHECKS,
        ManagedJobStatus.FAILED_NO_RESOURCE,
        ManagedJobStatus.FAILED_CONTROLLER,
    ])
    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_all_failure_statuses_return_failed(self, mock_sleep, mock_queue,
                                                status):
        mock_queue.return_value = ([_make_record(1, status)], 1, {}, 1)

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.FAILED


# ──────────────────────────────────────────────────────────────────────
# Timeout tests
# ──────────────────────────────────────────────────────────────────────


class TestWaitTimeout:

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    @mock.patch.object(time, 'time')
    def test_timeout_raises(self, mock_time, mock_sleep, mock_queue):
        # First call to time.time() is start_time, subsequent calls simulate
        # elapsed time.
        mock_time.side_effect = [0.0, 0.0, 31.0]
        mock_queue.side_effect = [
            ([_make_record(1, ManagedJobStatus.RUNNING)], 1, {}, 1),
            ([_make_record(1, ManagedJobStatus.RUNNING)], 1, {}, 1),
        ]

        with pytest.raises(TimeoutError, match='Timed out.*30 seconds'):
            jobs_core.wait(name=None, job_id=1, timeout=30, poll_interval=5)

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    @mock.patch.object(time, 'time')
    def test_timeout_none_keeps_polling(self, mock_time, mock_sleep,
                                        mock_queue):
        """With timeout=None, polling continues until terminal."""
        mock_time.return_value = 0.0
        mock_queue.side_effect = [
            ([_make_record(1, ManagedJobStatus.PENDING)], 1, {}, 1),
            ([_make_record(1, ManagedJobStatus.STARTING)], 1, {}, 1),
            ([_make_record(1, ManagedJobStatus.SUCCEEDED)], 1, {}, 1),
        ]

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.SUCCEEDED
        assert mock_sleep.call_count == 2

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    @mock.patch.object(time, 'time')
    def test_timeout_message_includes_status(self, mock_time, mock_sleep,
                                             mock_queue):
        mock_time.side_effect = [0.0, 0.0, 100.0]
        mock_queue.side_effect = [
            ([_make_record(1, ManagedJobStatus.RECOVERING)], 1, {}, 1),
            ([_make_record(1, ManagedJobStatus.RECOVERING)], 1, {}, 1),
        ]

        with pytest.raises(TimeoutError, match='RECOVERING'):
            jobs_core.wait(name=None, job_id=1, timeout=60, poll_interval=5)


# ──────────────────────────────────────────────────────────────────────
# Name resolution tests
# ──────────────────────────────────────────────────────────────────────


class TestWaitNameResolution:

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_name_resolves_to_job_id(self, mock_sleep, mock_queue):
        record = _make_record(42, ManagedJobStatus.SUCCEEDED, job_name='my-job')
        # First call: name resolution. Second call: poll by job_id.
        mock_queue.side_effect = [
            ([record], 1, {}, 1),
            ([record], 1, {}, 1),
        ]

        result = jobs_core.wait(name='my-job',
                                job_id=None,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.SUCCEEDED
        # First call uses name_match, second uses job_ids.
        calls = mock_queue.call_args_list
        assert calls[0] == mock.call(refresh=False, name_match='my-job')
        assert calls[1] == mock.call(refresh=False, job_ids=[42])

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_name_picks_latest_job_id(self, mock_sleep, mock_queue):
        records = [
            _make_record(10, ManagedJobStatus.FAILED, job_name='dup'),
            _make_record(20, ManagedJobStatus.SUCCEEDED, job_name='dup'),
        ]
        mock_queue.side_effect = [
            (records, 2, {}, 2),
            ([records[1]], 1, {}, 1),
        ]

        result = jobs_core.wait(name='dup',
                                job_id=None,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.SUCCEEDED
        # Should have resolved to job_id=20 (the latest).
        assert mock_queue.call_args_list[1] == mock.call(refresh=False,
                                                         job_ids=[20])

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_name_not_found_raises(self, mock_sleep, mock_queue):
        mock_queue.return_value = ([], 0, {}, 0)

        with pytest.raises(ValueError, match='No managed job found'):
            jobs_core.wait(name='nonexistent',
                           job_id=None,
                           timeout=None,
                           poll_interval=5)

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_name_filters_exact_match(self, mock_sleep, mock_queue):
        """name_match may return partial matches; wait() filters to exact."""
        records = [
            _make_record(1, ManagedJobStatus.SUCCEEDED, job_name='train'),
            _make_record(2, ManagedJobStatus.RUNNING, job_name='train-v2'),
        ]
        mock_queue.side_effect = [
            (records, 2, {}, 2),
            ([records[0]], 1, {}, 1),
        ]

        result = jobs_core.wait(name='train',
                                job_id=None,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.SUCCEEDED
        assert mock_queue.call_args_list[1] == mock.call(refresh=False,
                                                         job_ids=[1])


# ──────────────────────────────────────────────────────────────────────
# JobGroup (multi-task) tests
# ──────────────────────────────────────────────────────────────────────


class TestWaitJobGroup:

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_all_tasks_terminal(self, mock_sleep, mock_queue):
        records = [
            _make_record(1,
                         ManagedJobStatus.SUCCEEDED,
                         task_id=0,
                         task_name='preprocess'),
            _make_record(1,
                         ManagedJobStatus.SUCCEEDED,
                         task_id=1,
                         task_name='train'),
            _make_record(1,
                         ManagedJobStatus.SUCCEEDED,
                         task_id=2,
                         task_name='eval'),
        ]
        mock_queue.return_value = (records, 3, {}, 3)

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.SUCCEEDED
        mock_sleep.assert_not_called()

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_partial_terminal_keeps_polling(self, mock_sleep, mock_queue):
        mock_queue.side_effect = [
            ([
                _make_record(1, ManagedJobStatus.SUCCEEDED, task_id=0),
                _make_record(1, ManagedJobStatus.RUNNING, task_id=1),
            ], 2, {}, 2),
            ([
                _make_record(1, ManagedJobStatus.SUCCEEDED, task_id=0),
                _make_record(1, ManagedJobStatus.SUCCEEDED, task_id=1),
            ], 2, {}, 2),
        ]

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.SUCCEEDED
        mock_sleep.assert_called_once_with(5)

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_worst_exit_code_across_tasks(self, mock_sleep, mock_queue):
        records = [
            _make_record(1, ManagedJobStatus.SUCCEEDED, task_id=0),
            _make_record(1, ManagedJobStatus.FAILED, task_id=1),
        ]
        mock_queue.return_value = (records, 2, {}, 2)

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5)

        assert result == exceptions.JobExitCode.FAILED

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_task_filter_by_int(self, mock_sleep, mock_queue):
        """With task=1, only wait for task_id=1, ignore task_id=0."""
        mock_queue.side_effect = [
            ([
                _make_record(1, ManagedJobStatus.RUNNING, task_id=0),
                _make_record(1,
                             ManagedJobStatus.SUCCEEDED,
                             task_id=1,
                             task_name='train'),
            ], 2, {}, 2),
        ]

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5,
                                task=1)

        assert result == exceptions.JobExitCode.SUCCEEDED
        mock_sleep.assert_not_called()

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_task_filter_by_str(self, mock_sleep, mock_queue):
        mock_queue.side_effect = [
            ([
                _make_record(1,
                             ManagedJobStatus.RUNNING,
                             task_id=0,
                             task_name='preprocess'),
                _make_record(1,
                             ManagedJobStatus.SUCCEEDED,
                             task_id=1,
                             task_name='train'),
            ], 2, {}, 2),
        ]

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5,
                                task='train')

        assert result == exceptions.JobExitCode.SUCCEEDED
        mock_sleep.assert_not_called()

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_task_filter_not_found(self, mock_sleep, mock_queue):
        mock_queue.return_value = ([
            _make_record(1, ManagedJobStatus.RUNNING, task_id=0)
        ], 1, {}, 1)

        with pytest.raises(ValueError, match='No task matching'):
            jobs_core.wait(name=None,
                           job_id=1,
                           timeout=None,
                           poll_interval=5,
                           task=99)

    @mock.patch.object(jobs_core, 'queue_v2_api')
    @mock.patch('time.sleep')
    def test_task_filter_waits_for_specific_task(self, mock_sleep, mock_queue):
        """task=0 is still RUNNING while task=1 is done; keeps polling."""
        mock_queue.side_effect = [
            ([
                _make_record(1, ManagedJobStatus.RUNNING, task_id=0),
                _make_record(1, ManagedJobStatus.SUCCEEDED, task_id=1),
            ], 2, {}, 2),
            ([
                _make_record(1, ManagedJobStatus.FAILED, task_id=0),
                _make_record(1, ManagedJobStatus.SUCCEEDED, task_id=1),
            ], 2, {}, 2),
        ]

        result = jobs_core.wait(name=None,
                                job_id=1,
                                timeout=None,
                                poll_interval=5,
                                task=0)

        assert result == exceptions.JobExitCode.FAILED
        mock_sleep.assert_called_once_with(5)
