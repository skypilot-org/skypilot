"""Tests for the --tail option on `sky jobs logs`."""
from unittest import mock

from click.testing import CliRunner
import pytest

from sky.client.cli import command
from sky.jobs.client import sdk as jobs_sdk


class TestJobsLogsTailCli:
    """CLI-layer tests for `sky jobs logs --tail`."""

    def setup_method(self):
        self.runner = CliRunner()

    def _invoke(self, args):
        # Patch the module-level `managed_jobs` alias (= sky.jobs) in
        # command.py. Don't mock sys.exit — click's UsageError handler
        # raises SystemExit, and CliRunner captures it into exit_code.
        # Also stub download_logs_streaming so the streaming-first
        # sync-down branch falls through cleanly to download_logs in
        # tests (no real HTTP).
        with mock.patch.object(command.managed_jobs, 'tail_logs') as tail_mock, \
             mock.patch.object(command.managed_jobs,
                               'download_logs') as download_mock, \
             mock.patch.object(
                 command.managed_jobs, 'download_logs_streaming'
             ) as stream_mock:
            tail_mock.return_value = 0
            download_mock.return_value = {}
            # Streaming returns None to signal "empty, fall back" so the
            # CLI then calls legacy download_logs (and download_mock gets
            # called).
            stream_mock.return_value = None
            result = self.runner.invoke(command.jobs_logs, args)
            return result, tail_mock, download_mock

    def test_sync_down_with_explicit_tail_rejected(self):
        # Explicit --tail with --sync-down still rejected so users get
        # a clear error instead of silently truncating the saved log.
        result, _, _ = self._invoke(['-s', '--tail', '100', '1'])
        assert result.exit_code != 0
        assert '--tail is not supported with --sync-down' in result.output

    def test_tail_passed_to_sdk_when_set(self):
        result, tail_mock, _ = self._invoke(['--tail', '100', '1'])
        # CLI should have successfully called into the SDK.
        assert tail_mock.called, result.output
        _, kwargs = tail_mock.call_args
        assert kwargs.get('tail') == 100

    def test_tail_default_truncates_at_1000(self):
        """Default --tail (no explicit flag) is 1000 lines — speed-up
        for multi-GB logs. Users opt back into 'all' via --tail 0/-1.
        """
        result, tail_mock, _ = self._invoke(['1'])
        assert tail_mock.called, result.output
        _, kwargs = tail_mock.call_args
        assert kwargs.get('tail') == 1000

    def test_tail_zero_means_all_at_sdk(self):
        """--tail 0 explicitly opts into 'all lines' (None at the SDK)."""
        result, tail_mock, _ = self._invoke(['--tail', '0', '1'])
        assert tail_mock.called, result.output
        _, kwargs = tail_mock.call_args
        assert kwargs.get('tail') is None

    def test_tail_all_means_all_at_sdk(self):
        """--tail all is the user-friendly form of --tail 0."""
        result, tail_mock, _ = self._invoke(['--tail', 'all', '1'])
        assert tail_mock.called, result.output
        _, kwargs = tail_mock.call_args
        assert kwargs.get('tail') is None

    def test_tail_negative_means_all_at_sdk(self):
        """--tail -1 is also a valid way to ask for all lines."""
        result, tail_mock, _ = self._invoke(['--tail', '-1', '1'])
        assert tail_mock.called, result.output
        _, kwargs = tail_mock.call_args
        assert kwargs.get('tail') is None

    def test_tail_invalid_value_rejected(self):
        result, _, _ = self._invoke(['--tail', 'wat', '1'])
        assert result.exit_code != 0
        assert 'must be an integer or "all"' in result.output

    def test_sync_down_without_tail_still_works(self):
        # Default --tail with --sync-down silently means "all" so
        # existing scripts (sky jobs logs --sync-down <id>) keep
        # working after the default flip.
        result, _, download_mock = self._invoke(['-s', '1'])
        assert download_mock.called, result.output
        _, kwargs = download_mock.call_args
        # download_logs signature does not accept tail
        assert 'tail' not in kwargs


class TestJobsLogsTailSdk:
    """SDK-layer validation tests for tail_logs."""

    def test_zero_raises(self):
        with pytest.raises(ValueError, match='positive integer'):
            jobs_sdk.tail_logs(job_id=1,
                               tail=0,
                               follow=False,
                               controller=False,
                               refresh=False)

    def test_negative_raises(self):
        with pytest.raises(ValueError, match='positive integer'):
            jobs_sdk.tail_logs(job_id=1,
                               tail=-5,
                               follow=False,
                               controller=False,
                               refresh=False)

    def test_none_does_not_raise(self):
        """tail=None is the 'no limit' sentinel — should not raise."""
        with mock.patch('sky.server.common.make_authenticated_request') as req:
            # Short-circuit the HTTP path so we only exercise validation.
            req.side_effect = RuntimeError('server call not expected')
            with pytest.raises(RuntimeError, match='server call not expected'):
                jobs_sdk.tail_logs(job_id=1,
                                   tail=None,
                                   follow=False,
                                   controller=False,
                                   refresh=False)

    def test_positive_does_not_raise(self):
        with mock.patch('sky.server.common.make_authenticated_request') as req:
            req.side_effect = RuntimeError('server call not expected')
            with pytest.raises(RuntimeError, match='server call not expected'):
                jobs_sdk.tail_logs(job_id=1,
                                   tail=100,
                                   follow=False,
                                   controller=False,
                                   refresh=False)
