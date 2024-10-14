import os
import tempfile

from sky.data import storage_utils
from sky.skylet import constants


def test_get_excluded_files_from_skyignore_no_file():
    excluded_files = storage_utils.get_excluded_files_from_skyignore('.')
    assert len(excluded_files) == 0


def test_get_excluded_files_from_skyignore():
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create workdir
        dirs = ['remove_dir', 'dir', 'dir/subdir', 'dir/subdir/remove_dir']
        files = [
            'remove.py', 'remove.sh', 'remove.a', 'keep.py', 'remove.a',
            'dir/keep.txt', 'dir/remove.sh', 'dir/keep.a', 'dir/remove.b',
            'dir/remove.a', 'dir/subdir/keep.b', 'dir/subdir/remove.py'
        ]
        for dir_name in dirs:
            os.makedirs(os.path.join(temp_dir, dir_name), exist_ok=True)
        for file_path in files:
            full_path = os.path.join(temp_dir, file_path)
            with open(full_path, 'w') as f:
                f.write('test content')

        # Create skyignore file
        skyignore_content = """
        # Current directory
        /remove.py
        /remove_dir
        /*.a
        /dir/*.b
        # Pattern match for all subdirectories
        *.sh
        remove.a
        """
        skyignore_path = os.path.join(temp_dir, constants.SKY_IGNORE_FILE)
        with open(skyignore_path, 'w') as f:
            f.write(skyignore_content)

        # Test function
        excluded_files = storage_utils.get_excluded_files_from_skyignore(
            temp_dir)

        # Validate results
        expected_excluded_files = [
            'remove.py', 'remove_dir', 'remove.sh', 'remove.a', 'dir/remove.sh',
            'dir/remove.b', 'remove.a', 'dir/remove.a'
        ]
        for file_path in expected_excluded_files:
            assert file_path in excluded_files
        assert len(excluded_files) == len(expected_excluded_files)
