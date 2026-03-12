"""Unit tests for sky/client/common.py."""
import os
import re

from sky.client.common import _compute_file_mounts_blob_id


def test_blob_id_determinism(tmp_path):
    f = tmp_path / 'hello.txt'
    f.write_text('hello world')
    upload_list = [str(f)]

    result1 = _compute_file_mounts_blob_id(upload_list)
    result2 = _compute_file_mounts_blob_id(upload_list)

    assert result1 == result2
    assert len(result1) == 64
    assert re.fullmatch(r'[0-9a-f]{64}', result1)


def test_blob_id_content_sensitivity(tmp_path):
    f = tmp_path / 'data.txt'
    f.write_text('version 1')
    hash1 = _compute_file_mounts_blob_id([str(f)])

    f.write_text('version 2')
    hash2 = _compute_file_mounts_blob_id([str(f)])

    assert hash1 != hash2


def test_blob_id_order_independence(tmp_path):
    dir_a = tmp_path / 'a'
    dir_a.mkdir()
    (dir_a / 'file_a.txt').write_text('aaa')

    dir_b = tmp_path / 'b'
    dir_b.mkdir()
    (dir_b / 'file_b.txt').write_text('bbb')

    hash1 = _compute_file_mounts_blob_id([str(dir_a), str(dir_b)])
    hash2 = _compute_file_mounts_blob_id([str(dir_b), str(dir_a)])

    assert hash1 == hash2


def test_blob_id_symlink_handling(tmp_path):
    target = tmp_path / 'target.txt'
    target.write_text('real content')
    link = tmp_path / 'link.txt'
    link.symlink_to(target)

    # Pass the directory so iter_zip_entries walks it and sees the symlink.
    hash_val = _compute_file_mounts_blob_id([str(tmp_path)])
    assert len(hash_val) == 64
    assert re.fullmatch(r'[0-9a-f]{64}', hash_val)

    # Changing the symlink target name changes the hash, even if content is
    # the same, because symlinks hash the target path not the content.
    target2 = tmp_path / 'target2.txt'
    target2.write_text('real content')
    os.remove(str(link))
    link.symlink_to(target2)

    hash_val2 = _compute_file_mounts_blob_id([str(tmp_path)])
    assert hash_val2 != hash_val


def test_blob_id_directory_handling(tmp_path):
    empty_dir = tmp_path / 'empty'
    empty_dir.mkdir()

    hash1 = _compute_file_mounts_blob_id([str(tmp_path)])
    hash2 = _compute_file_mounts_blob_id([str(tmp_path)])

    assert hash1 == hash2
    assert len(hash1) == 64


def test_blob_id_empty_input():
    hash_val = _compute_file_mounts_blob_id([])
    assert len(hash_val) == 64
    assert re.fullmatch(r'[0-9a-f]{64}', hash_val)
