"""Autostop failures retain both channels returned by the SSH runner."""

import inspect
from unittest import mock

import pytest

from sky import exceptions
from sky.backends import cloud_vm_ray_backend as backend
from sky.utils import command_runner


@pytest.fixture
def autostop_backend():
    instance = backend.CloudVmRayBackend()
    handle = mock.MagicMock()
    handle.cluster_name = 'autostop-output-test'
    handle.launched_resources.cloud = object()
    handle.is_grpc_enabled_with_flag = False
    with mock.patch.object(instance, 'run_on_head') as run, mock.patch.object(
            backend.global_user_state, 'set_cluster_autostop_value') as update:
        yield instance, handle, run, update


@pytest.mark.parametrize('stream_logs', [False, True])
@pytest.mark.parametrize('returncode,stdout,stderr', [
    (255, 'remote diagnostic\n', ''),
    (255, '', 'transport diagnostic\n'),
    (255, 'remote diagnostic\n', 'transport diagnostic\n'),
    (0, 'successful output\n', 'successful warning\n'),
])
def test_autostop_ssh_output(autostop_backend, stream_logs, returncode, stdout,
                             stderr):
    instance, handle, run, update = autostop_backend
    run.return_value = returncode, stdout, stderr
    wait_for = backend.autostop_lib.AutostopWaitFor.JOBS
    code = backend.autostop_lib.AutostopCodeGen.set_autostop(
        31, instance.NAME, wait_for, True, None, None, None)

    if returncode:
        with pytest.raises(exceptions.CommandError) as caught:
            instance.set_autostop(handle,
                                  31,
                                  wait_for,
                                  down=True,
                                  stream_logs=stream_logs)
        assert caught.value.returncode == returncode
        assert caught.value.command == code
        assert 'Failed to set autostop' in caught.value.error_msg
        assert caught.value.detailed_reason == stdout + stderr
        update.assert_not_called()
    else:
        assert instance.set_autostop(
            handle, 31, wait_for, down=True, stream_logs=stream_logs) is None
        update.assert_called_once_with(handle.cluster_name, 31, True)
    run.assert_called_once_with(handle,
                                code,
                                require_outputs=True,
                                stream_logs=stream_logs)


def test_autostop_runner_merges_remote_stderr():
    # Generate the actual command without running a shell or SSH server.
    parameters = inspect.signature(
        backend.CloudVmRayBackend.run_on_head).parameters
    assert parameters['separate_stderr'].default is False
    command = command_runner.CommandRunner._get_command_to_run(
        mock.MagicMock(),
        'remote-command',
        process_stream=True,
        separate_stderr=False,
        skip_num_lines=0)
    assert command.endswith(' 2>&1')


@pytest.mark.parametrize('fail', [False, True])
def test_autostop_grpc_unchanged(autostop_backend, fail):
    instance, handle, run, update = autostop_backend
    handle.is_grpc_enabled_with_flag = True
    wait_for = backend.autostop_lib.AutostopWaitFor.JOBS
    failure = RuntimeError('grpc diagnostic')
    with mock.patch.object(backend,
                           'SkyletClient') as client, mock.patch.object(
                               backend.backend_utils,
                               'invoke_skylet_with_retries',
                               side_effect=lambda call: call()) as invoke:
        if fail:
            client.return_value.set_autostop.side_effect = failure
            with pytest.raises(RuntimeError) as caught:
                instance.set_autostop(handle, 31, wait_for, down=True)
            assert caught.value is failure
            update.assert_not_called()
        else:
            assert instance.set_autostop(handle, 31, wait_for,
                                         down=True) is None
            update.assert_called_once_with(handle.cluster_name, 31, True)
        invoke.assert_called_once()
        client.assert_called_once_with(handle.get_grpc_channel.return_value)
        request = client.return_value.set_autostop.call_args.args[0]
        assert request.idle_minutes == 31
        assert request.backend == instance.NAME
        assert request.wait_for == wait_for.to_protobuf()
        assert request.down
    run.assert_not_called()
