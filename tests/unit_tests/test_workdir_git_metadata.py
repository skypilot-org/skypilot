"""Workdir repository provenance survives task serialization."""
import subprocess
from unittest import mock

import pytest

from sky import Task
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
