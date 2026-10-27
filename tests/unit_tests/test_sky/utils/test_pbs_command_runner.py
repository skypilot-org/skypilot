"""Tests for PbsCommandRunner."""
from unittest import mock

from sky.utils import command_runner


def _runner():
    return command_runner.PbsCommandRunner(
        ('node1', 23456),
        'alice',
        None,
        sky_dir='/home/alice/.sky_clusters/sky-x',
        skypilot_runtime_dir='/tmp/sky-x',
        login_node_proxy_command="ssh -W '[%h]:%p' alice@login",
    )


def test_run_wraps_command_with_cluster_environment():
    runner = _runner()
    with mock.patch.object(command_runner.SSHCommandRunner, 'run',
                           return_value=0) as base_run:
        runner.run(['echo', 'hi'], stream_logs=False)
    wrapped = base_run.call_args.args[1]
    assert wrapped == ('export SKY_RUNTIME_DIR=/tmp/sky-x && '
                       'export UV_CACHE_DIR=/tmp/uv_cache_$(id -u) && '
                       'cd /home/alice/.sky_clusters/sky-x && '
                       'export HOME="$PWD" && echo hi')
    assert base_run.call_args.kwargs == {'stream_logs': False}


def test_ssh_goes_through_login_node_proxy():
    runner = _runner()
    cmd = ' '.join(
        runner.ssh_base_command(ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
                                port_forward=None,
                                connect_timeout=None))
    assert "ProxyCommand='ssh -W '\"'\"'[%h]:%p'\"'\"' alice@login'" in cmd
    assert '-o Port=23456' in cmd
    assert cmd.endswith('alice@node1')
    assert 'ControlMaster' not in cmd


def test_rsync_targets_cluster_home():
    runner = _runner()
    assert runner.get_remote_home_dir() == '/home/alice/.sky_clusters/sky-x'
    with mock.patch.object(command_runner.SSHCommandRunner,
                           'rsync') as base_rsync:
        runner.rsync('/local', '~/remote', up=True, stream_logs=False)
    kwargs = base_rsync.call_args.kwargs
    assert kwargs['get_remote_home_dir']() == '/home/alice/.sky_clusters/sky-x'
    assert kwargs['up'] is True
