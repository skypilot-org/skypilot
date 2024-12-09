import tempfile

import pytest

from sky import task


def test_validate_workdir():
    task_obj = task.Task()
    task_obj.validate_workdir()

    task_obj = task.Task(workdir='/nonexistent/path')
    with pytest.raises(ValueError):
        task_obj.validate_workdir()

    with tempfile.TemporaryDirectory() as d, tempfile.NamedTemporaryFile() as f:
        task_obj = task.Task(workdir=f.name)
        with pytest.raises(ValueError):
            task_obj.validate_workdir()

        task_obj = task.Task(workdir=d)
        task_obj.validate_workdir()


def test_validate_file_mounts():
    # Test empty file_mounts
    task_obj = task.Task()
    task_obj.validate_file_mounts()
    assert task_obj.file_mounts is None

    # Test None file_mounts
    task_obj.file_mounts = None
    task_obj.validate_file_mounts()

    with tempfile.TemporaryDirectory() as d, tempfile.NamedTemporaryFile() as f:
        # Test nonexistent local path
        task_obj = task.Task()
        task_obj.file_mounts = {'/remote': '/nonexistent/path'}
        with pytest.raises(ValueError):
            task_obj.validate_file_mounts()

        # Test file as source
        task_obj.file_mounts = {'/remote': f.name}
        task_obj.validate_file_mounts()

        # Test directory as source
        task_obj.file_mounts = {'/remote': d}
        task_obj.validate_file_mounts()

        # Test multiple mounts
        task_obj.file_mounts = {'/remote1': f.name, '/remote2': d}
        task_obj.validate_file_mounts()

        # Test cloud storage URLs
        task_obj.file_mounts = {
            '/remote': 's3://my-bucket/path',
            '/remote2': 'gs://another-bucket/path'
        }
        task_obj.validate_file_mounts()
