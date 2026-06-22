"""Tests for sky.server.blob.local_blob_storage."""
import pathlib
from unittest import mock

from sky.server.blob import local_blob_storage


def _make_client_tree(root: pathlib.Path, user: str, blob_id: str,
                      upload_id: str) -> pathlib.Path:
    """Create a representative clients/<user>/ tree and return the user dir."""
    user_dir = root / user
    # Transient uploaded task YAML.
    (user_dir / 'tasks').mkdir(parents=True)
    (user_dir / 'tasks' / 'task1.yaml').write_text('name: test')
    file_mounts = user_dir / 'file_mounts'
    # Legacy per-request (non-blob) upload dir + zip.
    (file_mounts / upload_id).mkdir(parents=True)
    (file_mounts / upload_id / 'data.txt').write_text('legacy')
    (file_mounts / f'{upload_id}.zip').write_text('legacy zip')
    # Content-addressed blob that must survive a restart.
    (file_mounts / 'blobs' / blob_id).mkdir(parents=True)
    (file_mounts / 'blobs' / blob_id / 'mount.txt').write_text('keep me')
    # Blob upload coordination dirs.
    (file_mounts / 'blobs' / '.locks').mkdir()
    return user_dir


def test_reset_on_startup_preserves_blobs(tmp_path):
    """Startup reset clears transient state but keeps file_mounts/blobs."""
    clients_dir = tmp_path / 'clients'
    user = 'a58d87ee'
    blob_id = '9babf19afa16a62c5fb1aee0c243531f2658c630'
    upload_id = 'deadbeef'
    user_dir = _make_client_tree(clients_dir, user, blob_id, upload_id)

    storage = local_blob_storage.LocalFilesystemBlobStorage()
    with mock.patch.object(local_blob_storage.server_common,
                           'API_SERVER_CLIENT_DIR', clients_dir):
        storage.reset_on_startup()

    file_mounts = user_dir / 'file_mounts'
    # Blob and its coordination dirs survive.
    assert (file_mounts / 'blobs' / blob_id / 'mount.txt').exists()
    assert (file_mounts / 'blobs' / '.locks').is_dir()
    # Transient per-request state is wiped.
    assert not (user_dir / 'tasks').exists()
    assert not (file_mounts / upload_id).exists()
    assert not (file_mounts / f'{upload_id}.zip').exists()


def test_reset_on_startup_no_clients_dir(tmp_path):
    """Reset is a no-op when the clients dir does not exist."""
    clients_dir = tmp_path / 'does_not_exist'
    storage = local_blob_storage.LocalFilesystemBlobStorage()
    with mock.patch.object(local_blob_storage.server_common,
                           'API_SERVER_CLIENT_DIR', clients_dir):
        # Should not raise.
        storage.reset_on_startup()
    assert not clients_dir.exists()
