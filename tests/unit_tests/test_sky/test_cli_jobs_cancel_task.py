"""`sky jobs cancel <job> --task <n|name>`: one dynamic task of a job group is
cancelled the way one of its declared tasks is addressed, by index or name."""
from unittest import mock

from click.testing import CliRunner
import pytest

from sky.client.cli import command


@pytest.fixture
def _cancel_sdk():
    with mock.patch.object(command.managed_jobs, 'cancel',
                           return_value='req') as cancel, \
         mock.patch.object(command.sdk, 'stream_and_get'):
        yield cancel


def _invoke(*args):
    return CliRunner().invoke(command.jobs_cancel, ['-y', *args])


def test_task_index_is_passed_as_an_int(_cancel_sdk):
    result = _invoke('39', '--task', '2')
    assert result.exit_code == 0, result.output
    assert _cancel_sdk.call_args.kwargs['job_ids'] == (39,)
    assert _cancel_sdk.call_args.kwargs['task'] == 2


def test_task_name_is_passed_as_a_str(_cancel_sdk):
    result = _invoke('39', '--task', 'eval-3')
    assert result.exit_code == 0, result.output
    assert _cancel_sdk.call_args.kwargs['task'] == 'eval-3'


def test_no_task_sends_none(_cancel_sdk):
    result = _invoke('39', '40')
    assert result.exit_code == 0, result.output
    assert _cancel_sdk.call_args.kwargs['job_ids'] == (39, 40)
    assert _cancel_sdk.call_args.kwargs['task'] is None


@pytest.mark.parametrize('args', [
    ('39', '40', '--task', '2'),
    ('--task', '2'),
    ('-n', 'rl', '--task', '2'),
    ('39', '--all', '--task', '2'),
])
def test_task_needs_exactly_one_job_id(_cancel_sdk, args):
    result = _invoke(*args)
    assert result.exit_code != 0
    assert 'exactly one JOB_ID' in result.output
    _cancel_sdk.assert_not_called()


def test_job_id_must_be_an_integer(_cancel_sdk):
    # The old `<group>-<index>` handle is gone; a dynamic task is
    # `<job> --task <index>`.
    result = _invoke('39-2')
    assert result.exit_code != 0
    assert 'not a valid integer' in result.output.lower() or \
        'invalid value' in result.output.lower()
    _cancel_sdk.assert_not_called()
