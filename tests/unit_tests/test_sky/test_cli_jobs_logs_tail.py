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
        # Mock download_logs_streaming to return None so the CLI's
        # streaming-first sync-down branch falls through to the legacy
        # download_logs (which we also mock).
        with mock.patch.object(command.managed_jobs, 'tail_logs') as tail_mock, \
             mock.patch.object(command.managed_jobs,
                               'download_logs') as download_mock, \
             mock.patch.object(
                 command.managed_jobs,
                 'download_logs_streaming') as stream_mock:
            tail_mock.return_value = 0
            download_mock.return_value = {}
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

    def test_tail_default_follow_truncates_at_1000(self):
        """Default --tail (no explicit flag) in follow mode is 1000
        lines — speed-up for multi-GB logs. Users opt back into 'all'
        via --tail 0.
        """
        # follow=True is the default; pass no --no-follow.
        result, tail_mock, _ = self._invoke(['1'])
        assert tail_mock.called, result.output
        _, kwargs = tail_mock.call_args
        assert kwargs.get('tail') == 1000

    def test_tail_default_no_follow_means_all(self):
        """Default --tail with --no-follow is 'all lines' — preserves
        pre-PR behavior so scripts that grep for early markers (e.g.
        'sky jobs logs --no-follow | grep <marker>') keep working.
        """
        result, tail_mock, _ = self._invoke(['--no-follow', '1'])
        assert tail_mock.called, result.output
        _, kwargs = tail_mock.call_args
        assert kwargs.get('tail') is None

    def test_tail_zero_means_all_at_sdk(self):
        """--tail 0 explicitly opts into 'all lines' (None at the SDK)."""
        result, tail_mock, _ = self._invoke(['--tail', '0', '1'])
        assert tail_mock.called, result.output
        _, kwargs = tail_mock.call_args
        assert kwargs.get('tail') is None

    def test_tail_negative_means_all_at_sdk(self):
        """--tail with a negative count is also "all lines" — but is
        also the same value Click uses as the no-flag sentinel, so
        passing -1 explicitly is indistinguishable from omitting the
        flag (both apply the 1000-line default in the interactive
        path; both mean "all" with --sync-down)."""
        # Sync-down path is unambiguous: -1 / no-flag both mean "all".
        result, _, download_mock = self._invoke(['-s', '--tail', '-1', '1'])
        assert download_mock.called, result.output

    def test_tail_invalid_value_rejected(self):
        # Click's int parser rejects non-numeric values.
        result, _, _ = self._invoke(['--tail', 'wat', '1'])
        assert result.exit_code != 0
        assert 'is not a valid integer' in result.output

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
