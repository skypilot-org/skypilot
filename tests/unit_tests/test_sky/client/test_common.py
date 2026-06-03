"""Unit tests for sky/client/common.py."""
import os
import re

from sky.client.common import _compute_zip_blob_id
from sky.data import storage_utils


def test_blob_id_determinism(tmp_path):
    f = tmp_path / 'hello.txt'
    f.write_text('hello world')

    zip1 = str(tmp_path / 'a.zip')
    zip2 = str(tmp_path / 'b.zip')
    storage_utils.zip_files_and_folders([str(f)], zip1)
    storage_utils.zip_files_and_folders([str(f)], zip2)

    result1 = _compute_zip_blob_id(zip1)
    result2 = _compute_zip_blob_id(zip2)

    assert result1 == result2
    assert len(result1) == 64
    assert re.fullmatch(r'[0-9a-f]{64}', result1)


def test_blob_id_content_sensitivity(tmp_path):
    f = tmp_path / 'data.txt'
    f.write_text('version 1')
    zip1 = str(tmp_path / 'v1.zip')
    storage_utils.zip_files_and_folders([str(f)], zip1)
    hash1 = _compute_zip_blob_id(zip1)

    f.write_text('version 2')
    zip2 = str(tmp_path / 'v2.zip')
    storage_utils.zip_files_and_folders([str(f)], zip2)
    hash2 = _compute_zip_blob_id(zip2)

    assert hash1 != hash2


def test_blob_id_order_independence(tmp_path):
    dir_a = tmp_path / 'a'
    dir_a.mkdir()
    (dir_a / 'file_a.txt').write_text('aaa')

    dir_b = tmp_path / 'b'
    dir_b.mkdir()
    (dir_b / 'file_b.txt').write_text('bbb')

    zip1 = str(tmp_path / 'ab.zip')
    storage_utils.zip_files_and_folders([str(dir_a), str(dir_b)], zip1)
    hash1 = _compute_zip_blob_id(zip1)

    zip2 = str(tmp_path / 'ba.zip')
    storage_utils.zip_files_and_folders([str(dir_b), str(dir_a)], zip2)
    hash2 = _compute_zip_blob_id(zip2)

    assert hash1 == hash2


def test_blob_id_symlink_handling(tmp_path):
    content_dir = tmp_path / 'content'
    content_dir.mkdir()
    target = content_dir / 'target.txt'
    target.write_text('real content')
    link = content_dir / 'link.txt'
    link.symlink_to(target)

    zip1 = str(tmp_path / 'z1.zip')
    storage_utils.zip_files_and_folders([str(content_dir)], zip1)
    hash_val = _compute_zip_blob_id(zip1)
    assert len(hash_val) == 64
    assert re.fullmatch(r'[0-9a-f]{64}', hash_val)

    # Changing the symlink target name changes the hash, even if content is
    # the same, because symlinks hash the target path not the content.
    target2 = content_dir / 'target2.txt'
    target2.write_text('real content')
    os.remove(str(link))
    link.symlink_to(target2)

    zip2 = str(tmp_path / 'z2.zip')
    storage_utils.zip_files_and_folders([str(content_dir)], zip2)
    hash_val2 = _compute_zip_blob_id(zip2)
    assert hash_val2 != hash_val


def test_blob_id_directory_handling(tmp_path):
    content_dir = tmp_path / 'content'
    content_dir.mkdir()
    empty_dir = content_dir / 'empty'
    empty_dir.mkdir()

    zip1 = str(tmp_path / 'z1.zip')
    zip2 = str(tmp_path / 'z2.zip')
    storage_utils.zip_files_and_folders([str(content_dir)], zip1)
    storage_utils.zip_files_and_folders([str(content_dir)], zip2)

    hash1 = _compute_zip_blob_id(zip1)
    hash2 = _compute_zip_blob_id(zip2)

    assert hash1 == hash2
    assert len(hash1) == 64


def test_blob_id_empty_input(tmp_path):
    zip_path = str(tmp_path / 'empty.zip')
    storage_utils.zip_files_and_folders([], zip_path)
    hash_val = _compute_zip_blob_id(zip_path)
    assert len(hash_val) == 64
    assert re.fullmatch(r'[0-9a-f]{64}', hash_val)
