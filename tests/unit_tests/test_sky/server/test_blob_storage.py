"""Unit tests for BlobStorage ``ensure_resolved`` hook and its consumers.

The hook is called by several core consumers (Storage source validation,
file-mount rsync) before any stat/open on a path that may live inside a
backend-managed blob cache. Base-class default is a no-op; plugin
backends that use a per-host local cache override it to trigger lazy
extraction.
"""

import contextlib
import os
import pathlib
from unittest import mock

import pytest

from sky.server.blob import blob_storage as bs
from sky.server.blob import local_blob_storage as lbs

# --- Base-class + default backend ---


def test_default_backend_ensure_resolved_is_noop():
    """The default ``BlobStorage.ensure_resolved`` returns the input path.

    Verified through ``LocalFilesystemBlobStorage`` (the default backend
    when no plugin overrides it) — it inherits the base-class no-op.
    """
    backend = lbs.LocalFilesystemBlobStorage()
    assert backend.ensure_resolved('/tmp/foo') == '/tmp/foo'
    assert backend.ensure_resolved('') == ''
    assert backend.ensure_resolved('relative/path') == 'relative/path'


# --- Consumer wiring ---


class _RecordingBlobStorage(bs.BlobStorage):
    """Minimal backend that records every ``ensure_resolved`` call.

    Abstract stubs are not exercised by the tests; we mark the unused
    parameters with ``del`` to silence the lint.
    """

    def __init__(self):
        self.calls: list = []

    def ensure_resolved(self, path: str) -> str:
        self.calls.append(path)
        return path

    async def blob_exists(self, user_id, blob_id):  # pragma: no cover
        del user_id, blob_id
        return False

    @contextlib.asynccontextmanager
    async def acquire_upload_lock(self, user_id, blob_id):  # pragma: no cover
        del user_id, blob_id
        yield None

    async def store_blob(self, user_id, blob_id,
                         staging_dir):  # pragma: no cover
        del user_id, blob_id, staging_dir

    def resolve_blob_to_dir(self, user_id, blob_id):  # pragma: no cover
        del user_id, blob_id
        return ''

    def delete_blob(self, user_id, blob_id):  # pragma: no cover
        del user_id, blob_id

    def list_blob_ids(self, user_id):  # pragma: no cover
        del user_id
        return []

    def release_stale_uploads(self, user_id):  # pragma: no cover
        del user_id

    def list_users(self):  # pragma: no cover
        return []

    def blobs_dir(self, user_id):  # pragma: no cover
        return pathlib.Path('/tmp/stub-blobs') / user_id

    def reset_on_startup(self):  # pragma: no cover
        pass


# pylint: disable=redefined-outer-name  # pytest fixture idiom


@pytest.fixture()
def recorder(monkeypatch):
    rec = _RecordingBlobStorage()
    monkeypatch.setattr(bs, 'get_blob_storage', lambda: rec)
    return rec


def test_validate_local_source_calls_ensure_resolved(tmp_path, recorder):
    """Storage source validation must route through the hook.

    Uses the ``_validate_source`` classmethod directly — that is the
    function where the hook was wired. The public ``Storage(...)`` init
    is lazy and defers validation to ``construct()``.
    """
    # pylint: disable=import-outside-toplevel
    from sky.data import storage as storage_lib

    src_dir = tmp_path / 'src'
    src_dir.mkdir()

    storage_lib.Storage._validate_source(  # pylint: disable=protected-access
        str(src_dir), storage_lib.StorageMode.COPY, True)

    expected = os.path.abspath(os.path.expanduser(str(src_dir)))
    assert expected in recorder.calls, (
        f'expected {expected} in ensure_resolved calls, got {recorder.calls}')


def test_validate_local_source_ensure_resolved_for_list_source(
        tmp_path, recorder):
    """List-valued source should call the hook for each path."""
    # pylint: disable=import-outside-toplevel
    from sky.data import storage as storage_lib

    a = tmp_path / 'a'
    b = tmp_path / 'b'
    a.mkdir()
    b.mkdir()

    storage_lib.Storage._validate_source(  # pylint: disable=protected-access
        [str(a), str(b)], storage_lib.StorageMode.COPY, True)

    assert str(a.resolve()) in recorder.calls
    assert str(b.resolve()) in recorder.calls


def _run_execute_file_mounts(file_mounts):
    """Invoke ``_execute_file_mounts`` enough to drive its validation loop.

    The downstream rsync / cloud-sync paths require real runners, cloud
    SDKs, etc.; we don't care about them here — once the validation loop
    has finished calling ``ensure_resolved`` we let the function raise,
    then return. The recorder captured what we need.
    """
    # pylint: disable=import-outside-toplevel
    from sky.backends import cloud_vm_ray_backend

    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    handle = mock.MagicMock()
    handle.launched_resources.cloud = 'kubernetes'
    handle.get_command_runners.return_value = []

    with mock.patch.object(cloud_vm_ray_backend.backend_utils,
                           'parallel_data_transfer_to_nodes'), \
         mock.patch.object(cloud_vm_ray_backend, 'rich_utils'), \
         mock.patch.object(cloud_vm_ray_backend, 'cloud_stores'), \
         mock.patch('os.system'):
        try:
            backend._execute_file_mounts(  # pylint: disable=protected-access
                handle, file_mounts)
        except (TypeError, AssertionError, AttributeError):
            # The second (rsync) loop touches heavily-mocked helpers and
            # may fail; the first (validation) loop — where our hook sits
            # — has already run by this point.
            pass


def test_execute_file_mounts_calls_ensure_resolved(tmp_path, recorder):
    """Each local-source file_mount should go through the hook."""
    f = tmp_path / 'file.txt'
    f.write_text('x')
    d = tmp_path / 'dir'
    d.mkdir()

    _run_execute_file_mounts({
        '/remote/file': str(f),
        '/remote/dir': str(d),
    })

    assert os.path.abspath(os.path.expanduser(str(f))) in recorder.calls
    assert os.path.abspath(os.path.expanduser(str(d))) in recorder.calls


def test_execute_file_mounts_skips_ensure_resolved_for_cloud_urls(recorder):
    """Cloud-store URLs must not be passed through ``ensure_resolved``."""
    _run_execute_file_mounts({'/remote/s3dst': 's3://some-bucket/path'})
    assert recorder.calls == []
