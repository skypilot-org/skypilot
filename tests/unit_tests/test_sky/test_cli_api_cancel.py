"""`sky api cancel`: request ids name what to cancel, -a/-u mean "all"."""
from unittest import mock

from click.testing import CliRunner
import pytest

from sky.client.cli import command


@pytest.fixture
def cancel_sdk():
    with mock.patch.object(command.sdk, 'api_cancel',
                           return_value='req') as cancel, \
         mock.patch.object(command.sdk, 'get', return_value=[]):
        yield cancel


def _invoke(*args, input_text=None):
    return CliRunner().invoke(command.api_cancel, list(args), input=input_text)


def test_request_ids_are_cancelled_without_a_prompt(cancel_sdk):
    result = _invoke('0d35ffa7', 'a9d59602')
    assert result.exit_code == 0, result.output
    cancel_sdk.assert_called_once_with(request_ids=('0d35ffa7', 'a9d59602'),
                                       all_users=False)


@pytest.mark.parametrize('flag', ['-a', '--all', '-u', '--all-users'])
def test_request_ids_cannot_be_combined_with_all(cancel_sdk, flag):
    # Before, the ids were dropped and every request was cancelled: with -u,
    # every request on the server.
    result = _invoke('0d35ffa7', flag, '-y')
    assert result.exit_code != 0
    assert 'Can only specify one of REQUEST_IDS or --all/--all-users' in (
        result.output)
    cancel_sdk.assert_not_called()


@pytest.mark.parametrize('flag,all_users', [('-a', False), ('-u', True)])
def test_all_flags_cancel_everything(cancel_sdk, flag, all_users):
    result = _invoke(flag, '-y')
    assert result.exit_code == 0, result.output
    cancel_sdk.assert_called_once_with(request_ids=None, all_users=all_users)


def test_all_users_still_asks_for_confirmation(cancel_sdk):
    result = _invoke('-u', input_text='no\n')
    assert result.exit_code != 0
    assert 'ALL USERS\' requests' in result.output
    cancel_sdk.assert_not_called()

    result = _invoke('-u', input_text='cancel all requests\n')
    assert result.exit_code == 0, result.output
    cancel_sdk.assert_called_once_with(request_ids=None, all_users=True)
