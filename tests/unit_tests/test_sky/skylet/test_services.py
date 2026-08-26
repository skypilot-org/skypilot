"""Unit tests for skylet services."""

import math
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import grpc
import pytest

from sky.exceptions import JobExitCode
from sky.schemas.generated import autostopv1_pb2
from sky.schemas.generated import jobsv1_pb2
from sky.skylet import autostop_lib
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.skylet import services
from sky.utils import ux_utils


def test_set_autostop_old_client_uses_legacy_strategy_and_reports_capability():
    service = services.AutostopServiceImpl()
    request = autostopv1_pb2.SetAutostopRequest(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS_AND_SSH,
        down=True,
    )
    context = mock.Mock()

    with mock.patch.object(autostop_lib, 'set_autostop') as set_autostop:
        response = service.SetAutostop(request, context)

    set_autostop.assert_called_once_with(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        hook=None,
        hook_timeout=None,
        cluster_hash=None,
        generation=None,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.LEGACY_HEAD_CREDENTIALS),
    )
    assert response.supports_durable_autodown
    context.abort.assert_not_called()


def test_set_autostop_forwards_present_durable_identity_and_strategy():
    service = services.AutostopServiceImpl()
    request = autostopv1_pb2.SetAutostopRequest(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS,
        down=True,
        cluster_hash='hash-exact',
        generation=42,
        execution_strategy=(
            autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_HEAD_WITH_SERVER_FALLBACK
        ),
    )
    context = mock.Mock()

    with mock.patch.object(autostop_lib, 'set_autostop') as set_autostop:
        response = service.SetAutostop(request, context)

    set_autostop.assert_called_once_with(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS,
        down=True,
        hook=None,
        hook_timeout=None,
        cluster_hash='hash-exact',
        generation=42,
        execution_strategy=(
            autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK),
    )
    assert response.supports_durable_autodown
    context.abort.assert_not_called()


def test_set_autostop_rejection_does_not_mutate_hook_list():
    service = services.AutostopServiceImpl()
    request = autostopv1_pb2.SetAutostopRequest(
        idle_minutes=-1,
        backend='cloud-vm-ray',
        wait_for=autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS,
        down=True,
        cluster_hash='old-hash',
        generation=1,
        execution_strategy=(
            autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_SERVER_ONLY),
        clear_hooks=True,
    )
    context = mock.Mock()
    context.abort.side_effect = RuntimeError('aborted')

    with mock.patch.object(autostop_lib,
                           'set_autostop',
                           return_value=autostop_lib.AutostopConfigUpdateResult.
                           REJECTED) as set_autostop, mock.patch.object(
                               autostop_lib, 'set_hooks') as set_hooks:
        with pytest.raises(RuntimeError, match='aborted'):
            service.SetAutostop(request, context)

    set_autostop.assert_called_once_with(
        idle_minutes=-1,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS,
        down=True,
        hook=None,
        hook_timeout=None,
        cluster_hash='old-hash',
        generation=1,
        execution_strategy=(autostop_lib.AutodownExecutionStrategy.SERVER_ONLY),
        hooks=[],
        clear_hooks=True,
    )
    set_hooks.assert_not_called()
    context.abort.assert_called_once_with(
        grpc.StatusCode.INTERNAL, 'Failed to set autostop configuration.')


def test_apply_autodown_intent_requires_strict_request_before_mutation():
    service = services.AutostopServiceImpl()
    request = autostopv1_pb2.SetAutostopRequest(
        idle_minutes=-1,
        backend='cloud-vm-ray',
        wait_for=autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS,
        down=True,
    )
    context = mock.Mock()
    context.abort.side_effect = RuntimeError('aborted')

    with mock.patch.object(autostop_lib, 'set_autostop') as set_autostop:
        with pytest.raises(RuntimeError, match='aborted'):
            service.ApplyAutodownIntent(request, context)

    set_autostop.assert_not_called()
    context.abort.assert_called_once_with(
        grpc.StatusCode.INTERNAL, 'Failed to set autostop configuration.')


def test_apply_autodown_intent_forwards_strict_request_to_shared_handler():
    service = services.AutostopServiceImpl()
    request = autostopv1_pb2.SetAutostopRequest(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS,
        down=True,
        cluster_hash='strict-hash',
        generation=2,
        execution_strategy=(
            autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_SERVER_ONLY),
    )
    context = mock.Mock()

    with mock.patch.object(autostop_lib, 'set_autostop') as set_autostop:
        response = service.ApplyAutodownIntent(request, context)

    set_autostop.assert_called_once_with(
        idle_minutes=10,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS,
        down=True,
        hook=None,
        hook_timeout=None,
        cluster_hash='strict-hash',
        generation=2,
        execution_strategy=(autostop_lib.AutodownExecutionStrategy.SERVER_ONLY),
    )
    assert response.supports_durable_autodown
    context.abort.assert_not_called()


def test_is_autostopping_reports_capability_and_exact_stored_generation():
    service = services.AutostopServiceImpl()
    config = autostop_lib.AutostopConfig(
        autostop_idle_minutes=10,
        boot_time=123,
        backend='cloud-vm-ray',
        wait_for=autostop_lib.AutostopWaitFor.JOBS,
        down=True,
        cluster_hash='hash-that-fired',
        generation=73,
        execution_strategy=(autostop_lib.AutodownExecutionStrategy.SERVER_ONLY),
    )
    config.durable_execution_state = (
        autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
    config.error_summary = 'Server teardown is required.'
    context = mock.Mock()

    with mock.patch.object(autostop_lib,
                           'get_autostop_config',
                           return_value=config), mock.patch.object(
                               autostop_lib,
                               'get_is_autostopping',
                               return_value=True):
        response = service.IsAutostopping(
            autostopv1_pb2.IsAutostoppingRequest(), context)

    assert response.is_autostopping
    assert response.supports_durable_autodown
    assert response.cluster_hash == 'hash-that-fired'
    assert response.HasField('cluster_hash')
    assert response.generation == 73
    assert response.HasField('generation')
    assert (response.durable_execution_state ==
            autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED)
    assert response.error_summary == 'Server teardown is required.'
    assert response.HasField('error_summary')


def test_is_autostopping_does_not_expose_raw_internal_errors():
    service = services.AutostopServiceImpl()
    context = mock.Mock()
    context.abort.side_effect = RuntimeError('aborted')

    with mock.patch.object(
            autostop_lib,
            'get_autostop_config',
            side_effect=RuntimeError('RUNPOD_API_KEY=secret; response body')):
        with pytest.raises(RuntimeError, match='aborted'):
            service.IsAutostopping(autostopv1_pb2.IsAutostoppingRequest(),
                                   context)

    context.abort.assert_called_once_with(grpc.StatusCode.INTERNAL,
                                          'Failed to query autostop status.')


class TestTailLogsBuffering(unittest.TestCase):

    def setUp(self):
        self.service = services.JobsServiceImpl()
        self.initial_thread_count = threading.active_count()

    def test_successful_job(self):
        """Test reading mocked logs of a finished job.

        Because the log file is already finished, we should see that size-based
        flushing is invoked, because there shouldn't be any delays between each
        line emitted such that we hit the default time limit of 50ms.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, 'run.log')
            with open(log_path, 'w', encoding='utf-8') as f:
                # Header must be present else we won't start streaming.
                f.write(f"{log_lib.LOG_FILE_START_STREAMING_AT}\n")
                # Write around 48KB of data
                for i in range(10000):
                    f.write(f'{i}\n')
                f.flush()
            file_size = os.stat(log_path).st_size

            # Sanity check
            self.assertGreaterEqual(file_size, 48000)
            num_chunks = math.ceil(file_size / log_lib.DEFAULT_LOG_CHUNK_SIZE)
            self.assertEqual(num_chunks, 3)

            with mock.patch('sky.skylet.services.job_lib.get_log_dir_for_job', return_value=tmpdir), \
                 mock.patch('sky.skylet.services.job_lib.update_job_status', return_value=[job_lib.JobStatus.SUCCEEDED]), \
                 mock.patch('sky.skylet.services.job_lib.get_status', return_value=job_lib.JobStatus.SUCCEEDED):

                req = jobsv1_pb2.TailLogsRequest(job_id=1, follow=False)
                responses = list(self.service.TailLogs(req, object()))

        chunks = [r.log_line for r in responses if r.log_line]
        self.assertEqual(len(chunks), num_chunks)
        for chunk in chunks:
            # Actual chunk sizes:
            # [16386, 16385, 16203]
            self.assertGreaterEqual(len(chunk), 16000)

        # First chunk
        self.assertEqual(
            chunks[0].startswith(
                f'{log_lib.LOG_FILE_START_STREAMING_AT}\n0\n1\n'), True)
        last_in_first = int(chunks[0].strip().split('\n')[-1])
        # Second chunk
        self.assertEqual(
            chunks[1].startswith(f'{last_in_first + 1}\n{last_in_first + 2}\n'),
            True)
        last_in_second = int(chunks[1].strip().split('\n')[-1])
        # Last chunk
        self.assertEqual(
            chunks[2].startswith(
                f'{last_in_second + 1}\n{last_in_second + 2}\n'), True)
        finish = ux_utils.finishing_message('Job finished (status: SUCCEEDED).')
        self.assertTrue(chunks[-1].endswith(f'9998\n9999\n{finish}\n'))
        final_exit_code = responses[-1].exit_code
        self.assertEqual(final_exit_code, JobExitCode.SUCCEEDED)

        # Verify no thread leaks
        self.assertEqual(threading.active_count(), self.initial_thread_count)

    def test_in_progress_job(self):
        """Test reading mocked logs of an in-progress job.

        Because the log file is still being written to, we should see that
        time-based flushing is invoked, which we mocked by sleeping for
        longer than the default time limit of 50ms between some log lines.
        """

        def iterator_with_delay(*_args, **_kwargs):
            yield 'a\n'
            # Force flush timeout
            time.sleep(services.DEFAULT_LOG_CHUNK_FLUSH_INTERVAL + 0.01)
            yield 'b\n'
            yield 'c\n'
            # Force flush timeout
            time.sleep(services.DEFAULT_LOG_CHUNK_FLUSH_INTERVAL + 0.01)
            yield 'd\n'

        with mock.patch('sky.skylet.services.job_lib.get_log_dir_for_job', return_value='/tmp'), \
             mock.patch('sky.skylet.services.job_lib.get_status', return_value=job_lib.JobStatus.RUNNING), \
             mock.patch('sky.skylet.services.log_lib.tail_logs_iter', side_effect=iterator_with_delay):

            req = jobsv1_pb2.TailLogsRequest(job_id=2, follow=True, tail=0)
            responses = list(self.service.TailLogs(req, object()))

        chunks = [r.log_line for r in responses if r.log_line]
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], 'a\n')
        self.assertEqual(chunks[1], 'b\nc\n')
        self.assertEqual(chunks[2], 'd\n')

        # For follow=True and RUNNING status, exit code should be NOT_FINISHED.
        self.assertEqual(responses[-1].exit_code, JobExitCode.NOT_FINISHED)

        # Verify no thread leaks
        self.assertEqual(threading.active_count(), self.initial_thread_count)


if __name__ == '__main__':
    unittest.main()
