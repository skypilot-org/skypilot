"""Tests for `sky jobs events` and the sky.jobs.events SDK wrapper."""
import datetime
import json
from unittest import mock

from click import testing as cli_testing
import pytest

from sky.client import sdk
from sky.client.cli import command
from sky.jobs import state as job_state
from sky.jobs.client import sdk as jobs_sdk


def _unwrap(fn):
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


_EVENTS = [
    {
        'spot_job_id': 42,
        'task_id': 0,
        'new_status': 'STARTING',
        'code': None,
        'reason': 'Launching (pending: QOSGrpGRES)',
        'timestamp': '2026-09-07T10:00:05+00:00',
    },
    {
        'spot_job_id': 42,
        'task_id': None,
        'new_status': job_state.ManagedJobStatus.PENDING,
        'code': 'SUBMITTED',
        'reason': 'Job is submitted',
        'timestamp': '2026-09-07T10:00:00+00:00',
    },
]


class TestJobsEventsCli:
    """`sky jobs events` rendering and flag forwarding."""

    def _invoke(self, args, events=None):
        runner = cli_testing.CliRunner()
        with mock.patch.object(command.managed_jobs, 'events',
                               return_value='req-id') as mock_events, \
             mock.patch.object(sdk, 'stream_and_get',
                               return_value=[dict(e) for e in _EVENTS]
                               if events is None else events):
            result = runner.invoke(command.jobs, ['events'] + args)
        return result, mock_events

    def test_json_output_is_machine_readable(self):
        result, mock_events = self._invoke(['42', '-o', 'json'])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert [e['reason'] for e in parsed
               ] == ['Launching (pending: QOSGrpGRES)', 'Job is submitted']
        # Enum statuses are serialized to plain strings.
        assert parsed[1]['new_status'] == 'PENDING'
        mock_events.assert_called_once_with(job_id=42,
                                            task_id=None,
                                            limit=50,
                                            include_cluster_events=False)

    def test_table_output_shows_reason_and_status(self):
        result, _ = self._invoke(['42'])
        assert result.exit_code == 0, result.output
        assert 'Events for managed job 42' in result.output
        assert 'QOSGrpGRES' in result.output
        assert 'PENDING' in result.output
        assert 'SUBMITTED' in result.output

    def test_flags_are_forwarded(self):
        result, mock_events = self._invoke(
            ['42', '--task-id', '1', '--limit', '0', '--cluster-events'])
        assert result.exit_code == 0, result.output
        mock_events.assert_called_once_with(job_id=42,
                                            task_id=1,
                                            limit=None,
                                            include_cluster_events=True)

    def test_no_events(self):
        result, _ = self._invoke(['7'], events=[])
        assert result.exit_code == 0, result.output
        assert 'No events found for managed job 7' in result.output

    def test_negative_limit_is_rejected(self):
        result, mock_events = self._invoke(['7', '--limit', '-1'])
        assert result.exit_code != 0
        assert 'limit' in result.output.lower()
        mock_events.assert_not_called()


class TestFormatJobEventTime:
    """Production hands this datetimes; strings are only the fallback."""

    def test_naive_datetime_is_rendered_as_is(self):
        # SQLite returns naive datetimes (local wall clock).
        naive = datetime.datetime(2026, 9, 7, 10, 0, 5)
        assert command._format_job_event_time(naive) == '2026-09-07 10:00:05'

    def test_aware_datetime_is_converted_to_local(self):
        # Postgres returns tz-aware datetimes; render in the local zone.
        aware = datetime.datetime(2026,
                                  9,
                                  7,
                                  10,
                                  0,
                                  5,
                                  tzinfo=datetime.timezone.utc)
        expected = aware.astimezone().strftime('%Y-%m-%d %H:%M:%S')
        assert command._format_job_event_time(aware) == expected

    def test_iso_string_and_garbage_fall_back(self):
        assert command._format_job_event_time(
            '2026-09-07T10:00:05') == '2026-09-07 10:00:05'
        assert command._format_job_event_time('not a time') == 'not a time'
        assert command._format_job_event_time(None) == 'None'


class TestJobsEventsSdk:
    """sky.jobs.events request body and version gating."""

    def _call(self, remote_version, **kwargs):
        raw_events = _unwrap(jobs_sdk.events)
        with mock.patch.object(jobs_sdk.versions,
                               'get_remote_api_version',
                               return_value=remote_version), \
             mock.patch.object(jobs_sdk.server_common,
                               'make_authenticated_request',
                               return_value='response') as mock_request, \
             mock.patch.object(jobs_sdk.server_common,
                               'get_request_id',
                               return_value='request-id'), \
             mock.patch.object(jobs_sdk.logger, 'warning') as mock_warning:
            result = raw_events(**kwargs)
        assert result == 'request-id'
        args, request_kwargs = mock_request.call_args
        assert args == ('POST', '/jobs/events')
        return request_kwargs['json'], mock_warning

    def test_posts_body(self):
        body, mock_warning = self._call(999,
                                        job_id=42,
                                        task_id=1,
                                        limit=None,
                                        include_cluster_events=True)
        # RequestBody adds common envelope fields; check ours only.
        assert {
            k: body[k]
            for k in ('job_id', 'task_id', 'limit', 'include_cluster_events')
        } == {
            'job_id': 42,
            'task_id': 1,
            'limit': None,
            'include_cluster_events': True,
        }
        mock_warning.assert_not_called()

    def test_negative_limit_is_rejected_before_the_request(self):
        raw_events = _unwrap(jobs_sdk.events)
        with mock.patch.object(jobs_sdk.server_common,
                               'make_authenticated_request') as mock_request:
            with pytest.raises(ValueError, match='non-negative'):
                raw_events(job_id=42, limit=-5)
        mock_request.assert_not_called()

    def test_cluster_events_dropped_on_old_server(self):
        body, mock_warning = self._call(52,
                                        job_id=42,
                                        include_cluster_events=True)
        assert body['include_cluster_events'] is False
        mock_warning.assert_called_once()
