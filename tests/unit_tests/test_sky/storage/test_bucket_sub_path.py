"""Tests that ``_bucket_sub_path`` is handled safely in storage commands."""
import inspect
import os
import shlex
import subprocess
from unittest import mock

import pytest

from sky import exceptions
from sky.data import storage as storage_lib

_STUB_CLIS = ('aws', 'gsutil', 'az', 'rclone', 'oci')


def _run_with_stub_clis(command, tmp_path, cwd=None):
    """Runs ``command`` under bash with the cloud CLIs replaced by stubs.

    Returns the arguments the stubs received, one per line. ``cwd`` sets the
    working directory, which lets a payload use a bare marker name (a file name
    cannot contain '/').
    """
    stub_dir = tmp_path / 'bin'
    stub_dir.mkdir(exist_ok=True)
    argv_log = tmp_path / 'argv.log'
    for cli in _STUB_CLIS:
        stub = stub_dir / cli
        stub.write_text(
            '#!/bin/bash\n'
            f'printf "%s\\n" "$@" >> {shlex.quote(str(argv_log))}\n')
        stub.chmod(0o755)
    env = dict(os.environ, PATH=f'{stub_dir}:/usr/bin:/bin')
    subprocess.run(command,
                   shell=True,
                   executable='/bin/bash',
                   env=env,
                   cwd=cwd,
                   check=True)
    return argv_log.read_text().splitlines()


@pytest.fixture
def sub_path_payload(tmp_path):
    marker = tmp_path / 'injected'
    return f'x$(touch {marker})', marker


def _new_store(store_cls, sub_path, **attrs):
    """Builds a store without __init__: no cloud calls, and the sub path
    reaches the command builders without going through Storage validation."""
    store = object.__new__(store_cls)
    store.name = 'bucket'
    store._bucket_sub_path = sub_path
    for key, value in attrs.items():
        setattr(store, key, value)
    return store


def _s3_batch_sync(sub_path):
    store = _new_store(storage_lib.S3Store,
                       sub_path,
                       config=storage_lib.S3Store.get_config())
    return store.batch_aws_rsync


def _gcs_batch_sync(sub_path):
    store = _new_store(storage_lib.GcsStore, sub_path)
    return store.batch_gsutil_rsync


def _azure_batch_sync(sub_path):
    store = _new_store(storage_lib.AzureBlobStore,
                       sub_path,
                       container_name='bucket',
                       storage_account_name='account',
                       storage_account_key='key')
    return store.batch_az_blob_sync


def _ibm_cos_batch_sync(sub_path):
    store = _new_store(storage_lib.IBMCosStore,
                       sub_path,
                       region='us-east',
                       rclone_profile_name='profile')
    return store.batch_ibm_rsync


def _oci_batch_sync(sub_path):
    store = _new_store(storage_lib.OciStore,
                       sub_path,
                       region='us-ashburn-1',
                       namespace='namespace')
    return store.batch_oci_rsync


@pytest.mark.parametrize('make_batch_sync', [
    _s3_batch_sync, _gcs_batch_sync, _azure_batch_sync, _ibm_cos_batch_sync,
    _oci_batch_sync
])
def test_sync_commands_quote_bucket_sub_path(make_batch_sync, sub_path_payload,
                                             tmp_path):
    payload, marker = sub_path_payload
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    (src_dir / 'data.txt').write_text('data')
    batch_sync = make_batch_sync(payload)
    with mock.patch.object(storage_lib.data_utils,
                           'parallel_upload') as parallel_upload:
        batch_sync([str(src_dir)], create_dirs=True)
    upload_args = inspect.signature(
        storage_lib.data_utils.parallel_upload).bind(
            *parallel_upload.call_args.args,
            **parallel_upload.call_args.kwargs).arguments
    # OCI wraps its generators to set up the OCI CLI first; run only the
    # command that carries the sub path.
    file_sync_cmd_gen, dir_sync_cmd_gen = [
        getattr(gen, '__wrapped__', gen)
        for gen in (upload_args['filesync_command_generator'],
                    upload_args['dirsync_command_generator'])
    ]
    commands = [
        file_sync_cmd_gen(str(src_dir), ['data.txt']),
        dir_sync_cmd_gen(str(src_dir), 'src'),
    ]
    for command in commands:
        argv = _run_with_stub_clis(command, tmp_path)
        assert not marker.exists(), command
        assert any(payload in arg for arg in argv), argv


def test_gcs_cp_command_quotes_bucket_sub_path(sub_path_payload, tmp_path):
    payload, marker = sub_path_payload
    src_file = tmp_path / 'data.txt'
    src_file.write_text('data')
    store = _new_store(storage_lib.GcsStore, payload)
    with mock.patch.object(storage_lib.data_utils,
                           'run_upload_cli') as run_upload_cli:
        store.batch_gsutil_cp([str(src_file)])
    command = run_upload_cli.call_args.args[0]
    argv = _run_with_stub_clis(command, tmp_path)
    assert not marker.exists(), command
    assert any(payload in arg for arg in argv), argv


def test_s3_delete_sub_path_command_quotes_bucket_sub_path(
        sub_path_payload, tmp_path):
    payload, marker = sub_path_payload
    store = _new_store(storage_lib.S3Store,
                       payload,
                       config=storage_lib.S3Store.get_config())
    with mock.patch.object(store, '_execute_remove_command') as remove:
        store._delete_sub_path()
    command = remove.call_args.args[0]
    argv = _run_with_stub_clis(command, tmp_path)
    assert not marker.exists(), command
    assert any(payload in arg for arg in argv), argv


def test_gcs_delete_sub_path_command_quotes_bucket_sub_path(
        sub_path_payload, tmp_path):
    payload, marker = sub_path_payload
    store = _new_store(storage_lib.GcsStore, payload, client=mock.MagicMock())
    with mock.patch.object(storage_lib.subprocess,
                           'check_output') as check_output:
        store._delete_sub_path()
    command = check_output.call_args.args[0]
    argv = _run_with_stub_clis(command, tmp_path)
    assert not marker.exists(), command
    assert any(payload in arg for arg in argv), argv


def _sync_generators(make_batch_sync, src_dir):
    """Returns the real file- and dir-sync command generators a store would use
    to upload ``src_dir``, unwrapping the OCI env decorator."""
    batch_sync = make_batch_sync('')
    with mock.patch.object(storage_lib.data_utils,
                           'parallel_upload') as parallel_upload:
        batch_sync([str(src_dir)], create_dirs=True)
    upload_args = inspect.signature(
        storage_lib.data_utils.parallel_upload).bind(
            *parallel_upload.call_args.args,
            **parallel_upload.call_args.kwargs).arguments
    return [
        getattr(gen, '__wrapped__', gen)
        for gen in (upload_args['filesync_command_generator'],
                    upload_args['dirsync_command_generator'])
    ]


# File names a client can put in an uploaded workdir/file_mount/storage source.
# Each carries a payload that would create ``marker`` if a shell evaluated it.
@pytest.mark.parametrize('name_template', [
    'a$(touch {marker}).txt',
    'a`touch {marker}`.txt',
    'a\'$(touch {marker})\'.txt',
    'a"$(touch {marker})".txt',
    'a$(touch {marker}) b.txt',
])
@pytest.mark.parametrize('make_batch_sync', [
    _s3_batch_sync, _gcs_batch_sync, _azure_batch_sync, _ibm_cos_batch_sync,
    _oci_batch_sync
])
def test_sync_commands_quote_file_names(make_batch_sync, name_template,
                                        tmp_path):
    # A file name cannot contain '/', so the marker is a bare name and the
    # command runs with cwd=tmp_path.
    marker_name = 'injected'
    marker = tmp_path / marker_name
    file_name = name_template.format(marker=marker_name)
    src_dir = tmp_path / 'src'
    src_dir.mkdir()
    (src_dir / file_name).write_text('data')
    # .skyignore globs the file into the excluded list that the dir-sync command
    # interpolates, mirroring how get_excluded_files surfaces client names.
    (src_dir / '.skyignore').write_text('a*\n')

    file_sync_cmd_gen, dir_sync_cmd_gen = _sync_generators(
        make_batch_sync, src_dir)
    # The file-sync command always carries the client file name; assert it is
    # passed literally and never evaluated.
    file_command = file_sync_cmd_gen(str(src_dir), [file_name])
    argv = _run_with_stub_clis(file_command, tmp_path, cwd=str(tmp_path))
    assert not marker.exists(), file_command
    assert any(file_name in arg for arg in argv), (file_name, argv)
    # The dir-sync command interpolates the excluded-file list (derived from the
    # same client names); assert the name cannot reach the shell as code.
    dir_command = dir_sync_cmd_gen(str(src_dir), 'dest')
    _run_with_stub_clis(dir_command, tmp_path, cwd=str(tmp_path))
    assert not marker.exists(), dir_command


@pytest.mark.parametrize('sub_path', [
    'x$(id)',
    'x`id`',
    'x;id',
    'x|id',
    'x&&id',
    'x id',
    'x\nid',
    'x\n',
    'x\'y',
    '..',
    '../other',
    'a/../../other',
])
def test_storage_rejects_invalid_bucket_sub_path(sub_path):
    with pytest.raises(exceptions.StorageSpecError, match='_bucket_sub_path'):
        storage_lib.Storage.from_yaml_config({
            'name': 'bucket',
            'source': 's3://bucket',
            '_bucket_sub_path': sub_path,
        })


@pytest.mark.parametrize('sub_path', [
    'my-dir',
    'job-abc123/local-file-mounts/0',
    'workspaces/default/job-abc123/tmp-files',
    '/leading/and/trailing/',
    'v1.2_data',
])
def test_storage_accepts_valid_bucket_sub_path(sub_path):
    storage_obj = storage_lib.Storage.from_yaml_config({
        'name': 'bucket',
        'source': 's3://bucket',
        '_bucket_sub_path': sub_path,
    })
    assert storage_obj.to_yaml_config()['_bucket_sub_path'] == sub_path


def test_gcs_cp_command_quotes_source_paths(tmp_path):
    # Source paths are client-controlled too; a path component with shell
    # syntax must reach gsutil literally. The marker is a bare name since a
    # path component cannot contain '/', so the command runs with cwd=tmp_path.
    marker = tmp_path / 'injected'
    src_file = tmp_path / 'd$(touch injected)' / 'data.txt'
    store = _new_store(storage_lib.GcsStore, None)
    with mock.patch.object(storage_lib.data_utils,
                           'run_upload_cli') as run_upload_cli:
        store.batch_gsutil_cp([str(src_file)])
    command = run_upload_cli.call_args.args[0]
    _run_with_stub_clis(command, tmp_path, cwd=str(tmp_path))
    assert not marker.exists(), command


def test_gcs_to_s3_quotes_bucket_names(tmp_path):
    from sky.data import data_transfer
    marker = tmp_path / 'injected'
    with mock.patch.object(data_transfer.subprocess, 'call') as call:
        data_transfer.gcs_to_s3('src$(touch injected)', 'dst$(touch injected)')
    command = call.call_args.args[0]
    argv = _run_with_stub_clis(command, tmp_path, cwd=str(tmp_path))
    assert not marker.exists(), command
    assert any('gs://src$(touch injected)' == arg for arg in argv), argv
