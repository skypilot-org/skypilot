"""Tests for the client-side `-u/--all-users` gate on mutating commands.

The API server rejects `--all-users` on `POST /cancel` and `POST /jobs/cancel`
(see `role_filter.reject_all_users_*`), but `sky down/stop/autostop` expand the
flag into one request per cluster here on the client, so for those three the
CLI check is the only gate. These tests pin that it fires (and that it stays
out of the way when the server has not enabled the restriction).
"""
from unittest import mock

from click.testing import CliRunner
import pytest

from sky.client.cli import command
from sky.server import common as server_common


def _server_info(restricted: bool) -> server_common.ApiServerInfo:
    return server_common.ApiServerInfo(
        status=server_common.ApiServerStatus.HEALTHY,
        restrict_all_users_mutations=restricted,
    )


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.parametrize('cmd,args', [
    (command.down, ['-u', '-y']),
    (command.stop, ['-u', '-y']),
    (command.autostop, ['-u', '-y']),
    (command.cancel, ['some-cluster', '-u', '-y']),
    (command.jobs_cancel, ['-u', '-y']),
])
def test_all_users_rejected_when_restricted(runner, cmd, args):
    with mock.patch.object(server_common,
                           'get_api_server_status',
                           return_value=_server_info(True)):
        result = runner.invoke(cmd, args)
    assert result.exit_code != 0
    assert 'rbac.restrict_all_users_mutations' in result.output


@pytest.mark.parametrize('cmd,args', [
    (command.down, ['-u', '-y']),
    (command.cancel, ['some-cluster', '-u', '-y']),
    (command.jobs_cancel, ['-u', '-y']),
])
def test_all_users_allowed_when_unrestricted(runner, cmd, args):
    """The gate must not fire; the command may still fail further along."""
    with mock.patch.object(server_common,
                           'get_api_server_status',
                           return_value=_server_info(False)):
        result = runner.invoke(cmd, args)
    assert 'rbac.restrict_all_users_mutations' not in result.output


@pytest.mark.parametrize('cmd,args', [
    (command.down, ['-a', '-y']),
    (command.cancel, ['some-cluster', '-a', '-y']),
    (command.jobs_cancel, ['-a', '-y']),
])
def test_gate_not_consulted_without_all_users(runner, cmd, args):
    """`-a/--all` targets only the caller's own resources; never gated."""
    with mock.patch.object(command,
                           '_reject_all_users_if_restricted') as mock_gate:
        runner.invoke(cmd, args)
    mock_gate.assert_not_called()
