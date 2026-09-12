"""Tests for job-name resolution in ``sky.jobs.client.sdk.tail_logs``.

``tail_logs`` resolves a job name to a job ID once, before streaming
starts, so that transparently retried streaming requests stay pinned to
the same job (a retried request carrying only the name would fail with
NOT_FOUND once the job reaches a terminal state, or could attach to a
newer job submitted with the same name).
"""
from unittest import mock

import pytest

from sky import exceptions
from sky.jobs.client import sdk as jobs_sdk


def _queue_result(records):
    """Builds a /jobs/queue/v2 result tuple around the given records."""
    return (records, len(records), {}, len(records))


def _resolve(records, name='foo', refresh=False, controller=False):
    with mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request') as request_mock, \
         mock.patch.object(jobs_sdk.server_common, 'get_request_id',
                           return_value='req-id'), \
         mock.patch.object(jobs_sdk.sdk, 'get',
                           return_value=_queue_result(records)):
        result = jobs_sdk._resolve_managed_job_id_by_name(  # pylint: disable=protected-access
            name, refresh, controller)
        return result, request_mock


class TestResolveManagedJobIdByName:

    def test_single_running_job_resolves(self):
        result, request_mock = _resolve([{
            'job_id': 7,
            'job_name': 'foo',
            'status': 'RUNNING'
        }])
        assert result == 7
        # The resolution must go through the queue endpoint.
        assert request_mock.call_args[0][1] == '/jobs/queue/v2'

    def test_only_terminal_job_is_not_found_without_controller(self):
        result, _ = _resolve([{
            'job_id': 7,
            'job_name': 'foo',
            'status': 'SUCCEEDED'
        }])
        message, returncode = result
        assert 'No running managed job found' in message
        assert returncode == int(exceptions.JobExitCode.NOT_FOUND)

    def test_only_terminal_job_resolves_with_controller(self):
        result, _ = _resolve([{
            'job_id': 7,
            'job_name': 'foo',
            'status': 'SUCCEEDED'
        }],
                             controller=True)
        assert result == 7

    def test_no_match_is_not_found(self):
        message, returncode = _resolve([])[0]
        assert 'No running managed job found' in message
        assert returncode == int(exceptions.JobExitCode.NOT_FOUND)

    def test_multiple_running_jobs_raise(self):
        with pytest.raises(ValueError, match='Multiple running jobs'):
            _resolve([{
                'job_id': 7,
                'job_name': 'foo',
                'status': 'RUNNING'
            }, {
                'job_id': 8,
                'job_name': 'foo',
                'status': 'RUNNING'
            }])

    def test_multiple_jobs_raise_with_controller(self):
        with pytest.raises(ValueError, match='Multiple managed jobs'):
            _resolve([{
                'job_id': 7,
                'job_name': 'foo',
                'status': 'SUCCEEDED'
            }, {
                'job_id': 8,
                'job_name': 'foo',
                'status': 'RUNNING'
            }],
                     controller=True)

    def test_name_match_superset_is_filtered_to_exact_matches(self):
        # ``name_match`` is a substring filter server-side (and old servers
        # ignore it entirely), so near-matches must be dropped client-side.
        result, _ = _resolve([{
            'job_id': 6,
            'job_name': 'foobar',
            'status': 'RUNNING'
        }, {
            'job_id': 7,
            'job_name': 'foo',
            'status': 'RUNNING'
        }])
        assert result == 7

    def test_multitask_job_counts_once(self):
        # A multi-task job has one record per task; the job is running if
        # any task is, and it must not be double-counted as two matches.
        result, _ = _resolve([{
            'job_id': 7,
            'job_name': 'foo',
            'status': 'SUCCEEDED'
        }, {
            'job_id': 7,
            'job_name': 'foo',
            'status': 'RUNNING'
        }])
        assert result == 7


class TestTailLogsNameResolution:

    def _tail_logs(self, resolved, **kwargs):
        with mock.patch.object(jobs_sdk.server_common,
                               'check_server_healthy_or_start_fn'), \
             mock.patch.object(jobs_sdk, '_resolve_managed_job_id_by_name',
                               return_value=resolved) as resolve_mock, \
             mock.patch.object(jobs_sdk, '_tail_logs',
                               return_value=0) as tail_mock:
            returncode = jobs_sdk.tail_logs(**kwargs)
            return returncode, resolve_mock, tail_mock

    def test_name_is_replaced_with_resolved_job_id(self):
        _, resolve_mock, tail_mock = self._tail_logs(resolved=7, name='foo')
        resolve_mock.assert_called_once_with('foo', False, False)
        _, kwargs = tail_mock.call_args
        assert kwargs['job_id'] == 7
        assert kwargs['name'] is None

    def test_explicit_job_id_skips_resolution(self):
        _, resolve_mock, tail_mock = self._tail_logs(resolved=7, job_id=3)
        resolve_mock.assert_not_called()
        _, kwargs = tail_mock.call_args
        assert kwargs['job_id'] == 3

    def test_not_found_returns_exit_code_when_following(self, capsys):
        returncode, _, tail_mock = self._tail_logs(resolved=('no job', 102),
                                                   name='foo',
                                                   follow=True)
        assert returncode == 102
        tail_mock.assert_not_called()
        assert 'no job' in capsys.readouterr().out

    def test_not_found_returns_none_when_not_following(self):
        returncode, _, tail_mock = self._tail_logs(resolved=('no job', 102),
                                                   name='foo',
                                                   follow=False)
        assert returncode is None
        tail_mock.assert_not_called()
