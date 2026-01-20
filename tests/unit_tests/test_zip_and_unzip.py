import asyncio
import io
import os
import pathlib
import tempfile
import zipfile

from sky.data import storage_utils
from sky.server import server
from sky.skylet import constants


def test_zip_files_and_folders(skyignore_dir):
    log_file = io.StringIO()
    with tempfile.NamedTemporaryFile('wb+', suffix='.zip') as f:
        storage_utils.zip_files_and_folders([skyignore_dir], f, log_file)
        # Print out all files in the zip
        f.seek(0)
        with zipfile.ZipFile(f, 'r') as zipf:
            actual_zipped_files = zipf.namelist()

        expected_zipped_files = [
            '', 'ln-keep.py', 'ln-dir-keep.py', 'dir/subdir/ln-keep.py',
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


def test_unzip_file(skyignore_dir, tmp_path):
    """Test server.unzip_file function."""
    # Create a temporary zip file
    zip_path = tmp_path / 'test.zip'
    # Zip the test directory
    storage_utils.zip_files_and_folders([skyignore_dir], zip_path,
                                        io.StringIO())

    excluded_files = storage_utils.get_excluded_files(skyignore_dir)

    # Create a temporary directory to unzip into
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = pathlib.Path(temp_dir)

        # Call server.unzip_file with an upload_id
        upload_id = 'test-upload-id'
        asyncio.run(server.unzip_file(zip_path, temp_dir_path, upload_id))

        # Verify the zip file was deleted
        assert not zip_path.exists()

        # Get list of files in original directory
        original_files = []
        for root, dirs, files in os.walk(skyignore_dir):
            rel_root = os.path.relpath(root, skyignore_dir)
            if rel_root == '.':
                rel_root = ''

            # Add directories
            for d in dirs:
                path = os.path.join(rel_root, d).rstrip('/')
                if path and path not in excluded_files:
                    original_files.append(path)

            # Add files
            for f in files:
                path = os.path.join(rel_root, f)
                if path not in excluded_files:
                    original_files.append(path)

        # Get list of files in unzipped directory
        unzipped_files = []
        # Files are now extracted to temp_dir_path/{upload_id}/{path}
        unzipped_dir = os.path.join(str(temp_dir_path), upload_id,
                                    str(skyignore_dir).lstrip('/'))
        unzipped_dir = pathlib.Path(unzipped_dir)
        print('unzipped_dir', unzipped_dir)
        for root, dirs, files in os.walk(unzipped_dir):
            rel_root = os.path.relpath(root, unzipped_dir)
            if rel_root == '.':
                rel_root = ''
            # Add directories
            for d in dirs:
                path = os.path.join(rel_root, d).rstrip('/')
                if path:
                    unzipped_files.append(path)

            # Add files
            for f in files:
                path = os.path.join(rel_root, f)
                unzipped_files.append(path)

        # Verify files match
        assert sorted(original_files) == sorted(unzipped_files)

        # Verify symlinks are preserved
        assert (unzipped_dir / 'ln-keep.py').is_symlink()
        assert (unzipped_dir / 'ln-dir-keep.py').is_symlink()
        assert (unzipped_dir / 'dir/subdir/ln-keep.py').is_symlink()
        assert (unzipped_dir / 'ln-folder').is_symlink()

        # Verify empty folders are preserved
        assert (unzipped_dir / 'empty-folder').is_dir()
        assert not any((unzipped_dir / 'empty-folder').iterdir())


def test_unzip_file_upload_id_paths(tmp_path):
    """Test that different upload_ids create separate directories."""
    # First upload
    zip_path1 = tmp_path / 'upload1.zip'
    source_dir1 = tmp_path / 'source1'
    source_dir1.mkdir()
    (source_dir1 / 'file_a.txt').write_text('a')
    (source_dir1 / 'file_b.txt').write_text('b')
    (source_dir1 / 'old_file.txt').write_text('delete me')

    storage_utils.zip_files_and_folders([str(source_dir1)], zip_path1,
                                        io.StringIO())

    extract_dir = tmp_path / 'extract'
    extract_dir.mkdir()
    upload_id1 = 'test-upload-id-1'
    asyncio.run(server.unzip_file(zip_path1, extract_dir, upload_id1))

    # Verify all files exist under extract_dir/{upload_id1}/
    extracted1 = extract_dir / upload_id1 / str(source_dir1).lstrip('/')
    assert (extracted1 / 'file_a.txt').exists()
    assert (extracted1 / 'file_b.txt').exists()
    assert (extracted1 / 'old_file.txt').exists()

    # Delete old_file.txt from source to simulate local deletion
    (source_dir1 / 'old_file.txt').unlink()

    # Second upload WITHOUT old_file.txt (using different upload_id)
    zip_path2 = tmp_path / 'upload2.zip'

    storage_utils.zip_files_and_folders([str(source_dir1)], zip_path2,
                                        io.StringIO())
    upload_id2 = 'test-upload-id-2'
    asyncio.run(server.unzip_file(zip_path2, extract_dir, upload_id2))

    # Verify second upload has only new files
    extracted2 = extract_dir / upload_id2 / str(source_dir1).lstrip('/')
    assert (extracted2 / 'file_a.txt').exists()
    assert (extracted2 / 'file_b.txt').exists()
    assert not (extracted2 / 'old_file.txt').exists()

    # First upload directory still exists with old file
    assert (extracted1 / 'old_file.txt').exists()
