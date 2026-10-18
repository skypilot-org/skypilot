"""Tests for the backend `sky launch` picks when --docker is not given."""
from unittest import mock

from click import testing as cli_testing

from sky import backends
from sky.client.cli import command


def _launch_backend(args):
    runner = cli_testing.CliRunner()
    with mock.patch.object(command.sdk, 'launch') as mock_launch, \
            mock.patch.object(command, '_async_call_or_wait',
                              return_value=(None, None)):
        result = runner.invoke(command.launch, ['-y', '-d', '--dryrun'] + args)
    assert result.exit_code == 0, (result.output, result.exception)
    return mock_launch.call_args.kwargs['backend']


def test_launch_defaults_to_cloud_vm_ray_backend():
    # Without --docker, the backend must be CloudVmRayBackend. Under
    # click >= 8.2 a flag_value option's `default=False` reached the command
    # as False instead of None, and launch failed with
    # "False backend is not supported."
    backend = _launch_backend(['echo', 'hi'])
    assert isinstance(backend, backends.CloudVmRayBackend)
