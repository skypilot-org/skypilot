import asyncio
import io
import os
import pathlib
import stat
import subprocess
import tempfile
import zipfile

import fastapi
import pytest

from sky.data import storage_utils
from sky.server import server
from sky.skylet import constants


@pytest.mark.parametrize('absolute_target', [False, True])
@pytest.mark.parametrize('relative_to_items', [False, True])
def test_directory_symlink_mount_is_materialized(tmp_path, absolute_target,
                                                 relative_to_items):
    donor = tmp_path / 'donor'
    donor.mkdir()
    (donor / 'payload').write_text('mounted contents')
    (donor / 'empty').mkdir()
    (donor / 'file-link').symlink_to('payload')
    (donor / 'directory-link').symlink_to('empty', target_is_directory=True)
    mount = tmp_path / 'mount'
    mount.symlink_to(donor if absolute_target else 'donor',
                     target_is_directory=True)
    archive = tmp_path / 'mount.zip'
    storage_utils.zip_files_and_folders([str(mount)],
                                        archive,
                                        relative_to_items=relative_to_items)

    destination = tmp_path / 'extracted'
    asyncio.run(server.unzip_file(archive, destination))
    mounted = destination / (mount.name
                             if relative_to_items else str(mount).lstrip('/'))
    assert mounted.is_dir() and not mounted.is_symlink()
    assert (mounted / 'payload').read_text() == 'mounted contents'
    assert (mounted / 'empty').is_dir()
    assert (mounted / 'file-link').is_symlink()
    assert (mounted / 'file-link').read_text() == 'mounted contents'
    assert (mounted / 'directory-link').is_symlink()
    assert (mounted / 'directory-link').is_dir()
    assert mount.is_symlink()  # Packaging never rewrites the source.


@pytest.mark.parametrize('parent_first', [False, True])
@pytest.mark.parametrize('double_slash', [False, True])
def test_explicit_symlink_mount_overlapping_parent(tmp_path, parent_first,
                                                   double_slash):
    donor = tmp_path / 'donor'
    donor.mkdir()
    (donor / 'payload').write_text('mounted contents')
    parent = tmp_path / 'parent'
    parent.mkdir()
    mount = parent / 'mount'
    mount.symlink_to(donor, target_is_directory=True)
    items = [str(parent), ('/' if double_slash else '') + str(mount)]
    if not parent_first:
        items.reverse()
    archive = tmp_path / 'mount.zip'
    storage_utils.zip_files_and_folders(items, archive)
    destination = tmp_path / 'extracted'

    asyncio.run(server.unzip_file(archive, destination))

    mounted = destination / str(mount).lstrip('/')
    assert mounted.is_dir() and not mounted.is_symlink()
    assert (mounted / 'payload').read_text() == 'mounted contents'


@pytest.mark.parametrize('dangling', [False, True])
def test_directory_upload_replaces_legacy_root_symlink(tmp_path, dangling):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'payload').write_text('new contents')
    archive = tmp_path / 'upload.zip'
    storage_utils.zip_files_and_folders([str(source)], archive)
    destination = tmp_path / 'received'
    mounted = destination / str(source).lstrip('/')
    mounted.parent.mkdir(parents=True)
    old_target = destination / 'old-target'
    if not dangling:
        old_target.mkdir()
        (old_target / 'payload').write_text('old contents')
    mounted.symlink_to(old_target, target_is_directory=True)

    asyncio.run(server.unzip_file(archive, destination))

    assert not mounted.is_symlink()
    assert (mounted / 'payload').read_text() == 'new contents'
    if dangling:
        assert not old_target.exists()
    else:
        assert (old_target / 'payload').read_text() == 'old contents'


@pytest.mark.parametrize('mode', [0o700, 0o755, 0o640, 0o7755])
def test_uploaded_regular_file_permissions(tmp_path, mode):
    source = tmp_path / 'executable'
    source.write_text('#!/bin/sh\nexit 0\n')
    source.chmod(mode)
    archive = tmp_path / 'upload.zip'
    storage_utils.zip_files_and_folders([str(source)], archive, io.StringIO())
    destination = tmp_path / 'received'
    destination.mkdir()

    asyncio.run(server.unzip_file(archive, destination))

    received = destination / str(source).lstrip('/')
    assert stat.S_IMODE(received.stat().st_mode) == mode & 0o777
    assert received.read_bytes() == source.read_bytes()
    if mode & stat.S_IXUSR:
        subprocess.run([str(received)], check=True, timeout=5)


@pytest.mark.parametrize('mode', [0o444, 0o555])
def test_overlapping_uploads_preserve_readonly_permissions(tmp_path, mode):
    workdir = tmp_path / 'workdir'
    workdir.mkdir()
    source = workdir / 'readonly'
    source.write_bytes(b'payload')
    source.chmod(mode)
    archive = tmp_path / 'upload.zip'
    storage_utils.zip_files_and_folders(
        [str(workdir), str(source)], archive, io.StringIO())
    destination = tmp_path / 'received'
    destination.mkdir()

    asyncio.run(server.unzip_file(archive, destination))

    received = destination / str(source).lstrip('/')
    assert received.read_bytes() == b'payload'
    assert stat.S_IMODE(received.stat().st_mode) == mode


@pytest.mark.parametrize('mode', [0o444, 0o555])
def test_repeated_upload_replaces_readonly_file(tmp_path, mode):
    source = tmp_path / 'readonly'
    source.touch()
    destination = tmp_path / 'received'
    destination.mkdir()
    for payload in (b'first', b'second'):
        source.chmod(0o600)
        source.write_bytes(payload)
        source.chmod(mode)
        archive = tmp_path / 'upload.zip'
        storage_utils.zip_files_and_folders([str(source)], archive,
                                            io.StringIO())

        asyncio.run(server.unzip_file(archive, destination))

        received = destination / str(source).lstrip('/')
        assert received.read_bytes() == payload
        assert stat.S_IMODE(received.stat().st_mode) == mode


def test_permissions_do_not_follow_replacement_symlink(tmp_path):
    archive = tmp_path / 'upload.zip'
    regular = zipfile.ZipInfo('replaced')
    regular.external_attr = (stat.S_IFREG | 0o444) << 16
    symlink = zipfile.ZipInfo('replaced')
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    target = zipfile.ZipInfo('target')
    target.external_attr = (stat.S_IFREG | 0o600) << 16
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr(regular, b'old')
        with pytest.warns(UserWarning, match='Duplicate name'):
            bundle.writestr(symlink, b'target')
        bundle.writestr(target, b'new')
    destination = tmp_path / 'received'
    destination.mkdir()

    asyncio.run(server.unzip_file(archive, destination))

    assert (destination / 'replaced').is_symlink()
    assert (destination / 'replaced').read_bytes() == b'new'
    assert stat.S_IMODE((destination / 'target').stat().st_mode) == 0o600


@pytest.mark.parametrize(('creator', 'attributes'), [(0, 0x20), (3, 0x20),
                                                     (0, 0o100755 << 16)])
def test_upload_without_unix_permissions_keeps_default_mode(
        tmp_path, creator, attributes):
    archive = tmp_path / 'upload.zip'
    member = zipfile.ZipInfo('data')
    member.create_system = creator
    member.external_attr = attributes
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr(member, b'payload')
    destination = tmp_path / 'received'
    destination.mkdir()
    control = destination / 'control'
    control.write_bytes(b'payload')

    asyncio.run(server.unzip_file(archive, destination))

    assert (destination / 'data').read_bytes() == control.read_bytes()
    assert stat.S_IMODE((destination / 'data').stat().st_mode) == stat.S_IMODE(
        control.stat().st_mode)


@pytest.mark.parametrize('root_entry', [False, True])
def test_unzip_file_with_symlinked_destination(tmp_path, root_entry):
    archive = tmp_path / 'upload.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        if root_entry:
            bundle.writestr('./', b'')
        bundle.writestr('data', b'payload')
    destination = tmp_path / 'received'
    destination.mkdir()
    destination_alias = tmp_path / 'received-alias'
    destination_alias.symlink_to(destination, target_is_directory=True)

    asyncio.run(server.unzip_file(archive, destination_alias))

    assert destination_alias.is_symlink()
    assert (destination / 'data').read_bytes() == b'payload'


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

        # Call server.unzip_file
        asyncio.run(server.unzip_file(zip_path, temp_dir_path))

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
        unzipped_dir = os.path.join(str(temp_dir_path),
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


def test_zip_files_and_folders_compression():
    """Test that compression is applied by default."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a text file with repetitive content (highly compressible)
        test_file = os.path.join(temp_dir, 'test.log')
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write('INFO: This is a log line\n' * 10000)

        uncompressed_size = os.path.getsize(test_file)

        # Create zip file in the same temp directory for automatic cleanup
        zip_path = os.path.join(temp_dir, 'test.zip')
        storage_utils.zip_files_and_folders([test_file], zip_path)
        compressed_size = os.path.getsize(zip_path)

        # Compressed ZIP should be significantly smaller
        # Log files with repetitive content should compress to <50% of
        # original size
        assert compressed_size < uncompressed_size * 0.5, (
            f'Compression not effective: {compressed_size} >= '
            f'{uncompressed_size * 0.5} (original: {uncompressed_size})')

        # Verify the zip is valid and can be extracted
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            assert len(zipf.namelist()) == 1
            # Check compression method is DEFLATED
            info = zipf.getinfo(zipf.namelist()[0])
            assert info.compress_type == zipfile.ZIP_DEFLATED


def test_unzip_file_zip_slip_blocked():
    """Test that Zip Slip path traversal attacks are blocked."""
    malicious_names = [
        '../../../etc/passwd',
        'foo/../../bar/../../etc/passwd',
        'normal/../../../etc/shadow',
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = pathlib.Path(tmpdir)

        for name in malicious_names:
            zip_path = tmpdir / 'malicious.zip'
            extract_dir = tmpdir / 'extract'
            extract_dir.mkdir(exist_ok=True)

            with zipfile.ZipFile(zip_path, 'w') as z:
                z.writestr(name, b'malicious content')

            with pytest.raises(fastapi.HTTPException) as exc_info:
                asyncio.run(server.unzip_file(zip_path, extract_dir))

            # HTTPException stores message in .detail, not __str__
            exc = exc_info.value
            error_msg = getattr(exc, 'detail', None) or str(exc)
            assert 'outside target directory' in error_msg, \
                f'Expected "outside target directory" error for {name}'
