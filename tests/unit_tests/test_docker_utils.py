"""Tests for docker container initialization on a remote node."""
from unittest import mock

from sky.provision import docker_utils


def _make_initializer(docker_config, runs, stream_pull_logs=True):
    """Returns a DockerInitializer whose runner records every command."""

    def _fake_run(cmd, **kwargs):
        runs.append((cmd, kwargs))
        if 'command -v docker' in cmd:
            return (0, '/usr/bin/docker', '')
        if 'printenv HOME' in cmd:
            return (0, '/root', '')
        if 'SKYPILOT_DOCKER_USER' in cmd:
            return (0, 'SKYPILOT_DOCKER_USER: root', '')
        if '/proc/meminfo' in cmd:
            return (0, 'MemAvailable:    8388608 kB', '')
        return (0, '', '')

    runner = mock.MagicMock()
    runner.run.side_effect = _fake_run
    return docker_utils.DockerInitializer(docker_config,
                                          runner,
                                          '/dev/null',
                                          stream_pull_logs=stream_pull_logs)


def test_run_does_not_stream_by_default():
    runs = []
    initializer = _make_initializer(
        {
            'container_name': 'sky_container',
            'image': 'myimage:latest'
        }, runs)
    initializer._run('whoami')  # pylint: disable=protected-access
    assert len(runs) == 1
    _, kwargs = runs[0]
    assert kwargs['stream_logs'] is False


def test_run_streams_when_requested():
    runs = []
    initializer = _make_initializer(
        {
            'container_name': 'sky_container',
            'image': 'myimage:latest'
        }, runs)
    # pylint: disable=protected-access
    initializer._run('docker pull myimage:latest', stream_logs=True)
    assert len(runs) == 1
    _, kwargs = runs[0]
    assert kwargs['stream_logs'] is True


def _assert_only_pulls_streamed(runs):
    pull_runs = [(cmd, kwargs) for cmd, kwargs in runs if ' pull ' in cmd]
    assert pull_runs, 'initialize() should have pulled the image'
    for cmd, kwargs in pull_runs:
        assert kwargs['stream_logs'] is True, (
            f'Pull command should stream its output: {cmd}')
    other_runs = [(cmd, kwargs) for cmd, kwargs in runs if ' pull ' not in cmd]
    assert other_runs, 'initialize() should have run setup commands'
    for cmd, kwargs in other_runs:
        assert kwargs['stream_logs'] is False, (
            f'Non-pull command should not stream its output: {cmd}')


def test_initialize_streams_image_pull_only():
    runs = []
    initializer = _make_initializer(
        {
            'container_name': 'sky_container',
            'image': 'myimage:latest',
            'pull_before_run': True,
        }, runs)
    docker_user = initializer.initialize()
    assert docker_user == 'root'
    _assert_only_pulls_streamed(runs)


def test_initialize_streams_conditional_pull():
    runs = []
    initializer = _make_initializer(
        {
            'container_name': 'sky_container',
            'image': 'myimage:latest',
            'pull_before_run': False,
        }, runs)
    docker_user = initializer.initialize()
    assert docker_user == 'root'
    _assert_only_pulls_streamed(runs)


def test_worker_initializer_keeps_pull_quiet():
    runs = []
    initializer = _make_initializer(
        {
            'container_name': 'sky_container',
            'image': 'myimage:latest',
            'pull_before_run': True,
        },
        runs,
        stream_pull_logs=False)
    docker_user = initializer.initialize()
    assert docker_user == 'root'
    assert all(not kwargs['stream_logs'] for _, kwargs in runs)


def test_initialize_docker_streams_head_only(monkeypatch):
    """Head detection must survive the SSH pool's worker threads.

    The provision-logging context is a threading.local set up in the
    provisioning thread; resolving it inside the pool-dispatched closure
    would see the /dev/null default and never stream.
    """
    from concurrent import futures
    import pathlib

    from sky.provision import instance_setup
    from sky.provision import logging as provision_logging

    head_log_path = '/logs/provision.log'
    worker_log_path = '/logs/instance/worker.log'
    monkeypatch.setattr(provision_logging.config,
                        'log_path',
                        pathlib.Path(head_log_path),
                        raising=False)

    constructed = {}

    class _FakeInitializer:

        def __init__(self, docker_config, runner, log_path, stream_pull_logs):
            del docker_config, runner
            constructed[log_path] = stream_pull_logs

        def initialize(self):
            return 'root'

    monkeypatch.setattr(instance_setup.docker_utils, 'DockerInitializer',
                        _FakeInitializer)

    def _fake_parallel_ssh_with_cache(func, cluster_name, stage_name, digest,
                                      cluster_info, ssh_credentials):
        # Mirror the real dispatch: the closure runs in pool threads,
        # head first with the provision log, workers with instance logs.
        with futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                pool.submit(func, mock.MagicMock(), head_log_path),
                pool.submit(func, mock.MagicMock(), worker_log_path),
            ]
            return [future.result() for future in results]

    monkeypatch.setattr(instance_setup, '_parallel_ssh_with_cache',
                        _fake_parallel_ssh_with_cache)

    instance_setup.initialize_docker(
        'test-cluster',
        docker_config={
            'container_name': 'c',
            'image': 'img'
        },
        cluster_info=mock.MagicMock(num_instances=2),
        ssh_credentials={})

    assert constructed == {head_log_path: True, worker_log_path: False}
