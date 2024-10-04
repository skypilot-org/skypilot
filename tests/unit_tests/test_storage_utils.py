import os
import tempfile

from sky.data import storage_utils
from sky.skylet import constants


def test_get_excluded_files_from_skyignore_no_file():
    excluded_files = storage_utils.get_excluded_files_from_skyignore('.')
    assert len(excluded_files) == 0


def test_get_excluded_files_from_skyignore():
    #TODO(yika): process all .skyignore files in the current directory and subdirectories
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create workdir
        dirs = ['remove_dir', 'dir']
        files = [
            'remove.py', 'remove.txt', 'remove.sh', 'keep.py', 'dir/keep.txt',
            'dir/remove.sh'
        ]
        for dir_name in dirs:
            os.makedirs(os.path.join(temp_dir, dir_name), exist_ok=True)
        for file_path in files:
            full_path = os.path.join(temp_dir, file_path)
            with open(full_path, 'w') as f:
                f.write('test content')

        # Create skyignore file
        skyignore_content = """
        # File
        remove.py
        # Directory
        remove_dir/
        # Pattern match for current directory
        ./*.txt
        # Pattern match for all subdirectories
        **/*.sh
        """
        skyignore_path = os.path.join(temp_dir, constants.SKY_IGNORE_FILE)
        with open(skyignore_path, 'w') as f:
            f.write(skyignore_content)

        # Test function
        excluded_files = storage_utils.get_excluded_files_from_skyignore(
            temp_dir)

        # Validate results
        expected_excluded_files = [
            'remove.py', 'remove_dir/', 'remove.txt', 'remove.sh',
            'dir/remove.sh'
        ]
        for file_path in expected_excluded_files:
            assert file_path in excluded_files
        assert len(excluded_files) == len(expected_excluded_files)
