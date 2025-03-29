import os
import tempfile

import pytest

from sky import task


def test_validate_workdir():
    curr_dir = os.getcwd()
    home_dir = os.path.expanduser('~')

    task_obj = task.Task()
    task_obj.expand_and_validate_workdir()

    task_obj = task.Task(workdir='/nonexistent/path')
    with pytest.raises(ValueError):
        task_obj.expand_and_validate_workdir()

    with tempfile.TemporaryDirectory() as d, tempfile.NamedTemporaryFile() as f:
        task_obj = task.Task(workdir=f.name)
        with pytest.raises(ValueError):
            task_obj.expand_and_validate_workdir()

        task_obj = task.Task(workdir=d)
        task_obj.expand_and_validate_workdir()

        task_obj = task.Task(workdir='~')
        task_obj.expand_and_validate_workdir()
        assert task_obj.workdir == home_dir

        task_obj = task.Task(workdir='.')
        task_obj.expand_and_validate_workdir()
        assert task_obj.workdir == curr_dir


def test_validate_file_mounts():
    curr_dir = os.getcwd()
    home_dir = os.path.expanduser('~')

    # Test empty file_mounts
    task_obj = task.Task()
    task_obj.expand_and_validate_file_mounts()
    assert task_obj.file_mounts is None

    # Test None file_mounts
    task_obj.file_mounts = None
    task_obj.expand_and_validate_file_mounts()

    with tempfile.TemporaryDirectory() as d, tempfile.NamedTemporaryFile() as f:
        # Test nonexistent local path
        task_obj = task.Task()
        task_obj.file_mounts = {'/remote': '/nonexistent/path'}
        with pytest.raises(ValueError):
            task_obj.expand_and_validate_file_mounts()

        # Test file as source
        task_obj.file_mounts = {'/remote': f.name}
        task_obj.expand_and_validate_file_mounts()

        # Test directory as source
        task_obj.file_mounts = {
            '/remote': d,
            '/remote-home': '~',
            '/remote-curr': '.'
        }
        task_obj.expand_and_validate_file_mounts()
        assert task_obj.file_mounts['/remote-home'] == home_dir
        assert task_obj.file_mounts['/remote-curr'] == curr_dir

        # Test multiple mounts
        task_obj.file_mounts = {'/remote1': f.name, '/remote2': d}
        task_obj.expand_and_validate_file_mounts()

        # Test cloud storage URLs
        task_obj.file_mounts = {
            '/remote': 's3://my-bucket/path',
            '/remote2': 'gs://another-bucket/path'
        }
        task_obj.expand_and_validate_file_mounts()
