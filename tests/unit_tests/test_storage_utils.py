import io
import os
import tempfile
import zipfile

import pytest

from sky.data import storage_utils
from sky.skylet import constants


@pytest.fixture
def skyignore_dir():
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create workdir
        dirs = ['remove_dir', 'dir', 'dir/subdir', 'dir/subdir/remove_dir']
        files = [
            'remove.py',
            'remove.sh',
            'remove.a',
            'keep.py',
            'remove.a',
            'dir/keep.txt',
            'dir/remove.sh',
            'dir/keep.a',
            'dir/remove.b',
            'dir/remove.a',
            'dir/subdir/keep.b',
            'dir/subdir/remove.py',
        ]
        for dir_name in dirs:
            os.makedirs(os.path.join(temp_dir, dir_name), exist_ok=True)
        for file_path in files:
            full_path = os.path.join(temp_dir, file_path)
            with open(full_path, 'w') as f:
                f.write('test content')

        # Create symlinks
        os.symlink(os.path.join(temp_dir, 'keep.py'),
                   os.path.join(temp_dir, 'ln-keep.py'))
        os.symlink(os.path.join(temp_dir, 'dir/keep.py'),
                   os.path.join(temp_dir, 'ln-dir-keep.py'))
        os.symlink(os.path.join(temp_dir, 'keep.py'),
                   os.path.join(temp_dir, 'dir/subdir/ln-keep.py'))

        # Symlinks for folders
        os.symlink(os.path.join(temp_dir, 'dir/subdir/ln-folder'),
                   os.path.join(temp_dir, 'ln-folder'))

        # Create empty directories
        os.makedirs(os.path.join(temp_dir, 'empty-folder'))

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
        with open(skyignore_path, 'w', encoding='utf-8') as f:
            f.write(skyignore_content)

        yield temp_dir


def test_get_excluded_files_from_skyignore_no_file():
    excluded_files = storage_utils.get_excluded_files_from_skyignore('.')
    assert not excluded_files


def test_get_excluded_files_from_skyignore(skyignore_dir):
    # Test function
    excluded_files = storage_utils.get_excluded_files_from_skyignore(
        skyignore_dir)

    # Validate results
    expected_excluded_files = [
        'remove.py', 'remove_dir', 'remove.sh', 'remove.a', 'dir/remove.sh',
        'dir/remove.b', 'remove.a', 'dir/remove.a'
    ]
    for file_path in expected_excluded_files:
        assert file_path in excluded_files
    assert len(excluded_files) == len(expected_excluded_files)


def test_zip_files_and_folders(skyignore_dir):
    log_file = io.StringIO()
    with tempfile.NamedTemporaryFile('wb+', suffix='.zip') as f:
        storage_utils.zip_files_and_folders([skyignore_dir], f, log_file)
        # Print out all files in the zip
        f.seek(0)
        with zipfile.ZipFile(f, 'r') as zipf:
            actual_zipped_files = zipf.namelist()

        expected_zipped_files = [
            'ln-keep.py', 'ln-dir-keep.py', 'dir/subdir/ln-keep.py',
            constants.SKY_IGNORE_FILE, 'dir/subdir/remove.py', 'keep.py',
            'dir/keep.txt', 'dir/keep.a', 'dir/subdir/keep.b', 'ln-folder',
            'empty-folder/', 'dir/', 'dir/subdir/', 'dir/subdir/remove_dir/'
        ]

        expected_zipped_file_paths = []
        for filename in expected_zipped_files:
            file_path = os.path.join(skyignore_dir, filename)
            if 'ln' not in filename:
                file_path = file_path.lstrip('/')
            expected_zipped_file_paths.append(file_path)

        for file in actual_zipped_files:
            assert file in expected_zipped_file_paths, (
                file, expected_zipped_file_paths)
        assert len(actual_zipped_files) == len(expected_zipped_file_paths)
        # Check the log file correctly logs the zipped files
        log_file.seek(0)
        log_file_content = log_file.read()
        assert f'Zipped {skyignore_dir}' in log_file_content
