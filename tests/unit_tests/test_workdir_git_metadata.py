"""Workdir repository provenance survives task serialization."""
import subprocess
from unittest import mock

import pytest

from sky import Task
from sky.skylet import constants
from sky.utils import common_utils


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args],
                          check=True,
                          capture_output=True,
                          text=True).stdout.strip()


@pytest.mark.parametrize('origin', [
    'git@github.com:team/project.git',
    'ssh://git@github.com/team/project.git',
    'https://user:secret@github.com/team/project.git?token=secret',
    'https://github.com/team/project/',
])
def test_workdir_metadata(origin, tmp_path):
    git(tmp_path, 'init')
    git(tmp_path, 'remote', 'add', 'origin', origin)
    workdir = tmp_path / 'training'
    workdir.mkdir()
    (workdir / 'main.py').write_text('print("before")\n')
    git(tmp_path, 'add', 'training/main.py')
    git(tmp_path, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
        'commit', '-m', 'Initial source')
    task = Task(workdir=str(workdir))
    task.expand_and_validate_workdir()
    assert task.metadata['git_origin_url'] == 'https://github.com/team/project'
    assert task.metadata['git_workdir_relpath'] == 'training'
    assert task.metadata['git_dirty'] is False
    (workdir / 'main.py').write_text('print("after")\n')
    task.expand_and_validate_workdir()
    assert task.metadata['git_dirty'] is True
    # Uploaded subdirectories have no .git directory on the server.
    uploaded = tmp_path / 'uploaded'
    uploaded.mkdir()
    with mock.patch.object(common_utils, 'get_git_commit', return_value=None):
        with mock.patch.object(common_utils,
                               'get_git_workdir_metadata',
                               return_value={}):
            task = Task.from_yaml_config(task.to_yaml_config())
            task.workdir = str(uploaded)
            task.expand_and_validate_workdir()
    assert task.metadata['git_origin_url'] == 'https://github.com/team/project'
    assert task.metadata['git_workdir_relpath'] == 'training'
    assert task.metadata['git_dirty'] is True


def test_no_remote_and_no_repository(tmp_path):
    assert not common_utils.get_git_workdir_metadata(str(tmp_path))
    git(tmp_path, 'init')
    git(tmp_path, 'remote', 'add', 'origin', '/local/repo')
    metadata = common_utils.get_git_workdir_metadata(str(tmp_path))
    assert metadata == {'git_workdir_relpath': '', 'git_dirty': False}


def test_git_metadata_timeout():
    with mock.patch('subprocess.run',
                    side_effect=subprocess.TimeoutExpired('git', 5)):
        assert not common_utils.get_git_workdir_metadata('/workdir')


@pytest.mark.parametrize('client_has_metadata', [True, False])
def test_server_validation_preserves_client_metadata(tmp_path, monkeypatch,
                                                     client_has_metadata):
    monkeypatch.delenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, raising=False)
    client = tmp_path / 'client'
    client.mkdir()
    git(client, 'init')
    git(client, 'remote', 'add', 'origin',
        'https://github.com/team/project.git')
    workdir = client / 'training'
    workdir.mkdir()
    source = workdir / 'main.py'
    source.write_text('print("before")\n')
    git(client, 'add', 'training/main.py')
    git(client, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
        'commit', '-m', 'Initial source')
    source.write_text('print("after")\n')
    task = Task(workdir=str(workdir))
    task.expand_and_validate_workdir()
    if not client_has_metadata:
        task.metadata.clear()
    expected = task.metadata.copy()
    task = Task.from_yaml_config(task.to_yaml_config())

    server = tmp_path / 'server'
    server.mkdir()
    git(server, 'init')
    git(server, 'remote', 'add', 'origin',
        'https://github.com/team/dotfiles.git')
    uploaded = server / 'blobs' / 'uploaded'
    uploaded.mkdir(parents=True)
    (uploaded / 'main.py').write_text(source.read_text())
    git(server, 'add', 'blobs/uploaded/main.py')
    git(server, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
        'commit', '-m', 'Server files')
    assert common_utils.get_git_workdir_metadata(str(uploaded)) == {
        'git_origin_url': 'https://github.com/team/dotfiles',
        'git_workdir_relpath': 'blobs/uploaded',
        'git_dirty': False,
    }
    task.workdir = str(uploaded)
    monkeypatch.setenv(constants.ENV_VAR_IS_SKYPILOT_SERVER, 'true')
    with mock.patch.object(common_utils.subprocess, 'run',
                           wraps=subprocess.run) as run:
        task.expand_and_validate_workdir()
    assert task.metadata == expected
    run.assert_not_called()

    task.workdir = str(server / 'missing')
    with pytest.raises(ValueError, match='Workdir must be a valid directory'):
        task.expand_and_validate_workdir()
