"""Tests for LocalFilesystemBlobStorage.reset_on_startup."""
import pathlib

from sky.server.blob import local_blob_storage


def _make_blob(client_dir: pathlib.Path,
               user: str,
               blob_id: str,
               content: str = 'data') -> pathlib.Path:
    blob_dir = client_dir / user / 'file_mounts' / 'blobs' / blob_id
    blob_dir.mkdir(parents=True)
    (blob_dir / 'file').write_text(content)
    return blob_dir


def _patch_client_dir(monkeypatch, client_dir: pathlib.Path) -> None:
    monkeypatch.setattr('sky.server.common.API_SERVER_CLIENT_DIR', client_dir)


def _patch_active(monkeypatch, *, requests=None, jobs=None) -> None:
    monkeypatch.setattr(
        'sky.server.requests.requests.get_active_file_mounts_blob_ids',
        lambda: set(requests or set()))
    monkeypatch.setattr('sky.jobs.state.get_active_file_mounts_blob_ids',
                        lambda: set(jobs or set()))


def test_reset_on_startup_preserves_active_blobs(tmp_path, monkeypatch):
    client_dir = tmp_path / 'clients'
    _patch_client_dir(monkeypatch, client_dir)

    active = _make_blob(client_dir, 'user1', 'active', content='keep')
    stale = _make_blob(client_dir, 'user1', 'stale')
    # A non-blob per-user staging dir that should be reclaimed.
    logs = client_dir / 'user1' / 'sky_logs'
    logs.mkdir(parents=True)
    (logs / 'run.log').write_text('log')

    # Referenced by a non-terminal managed job; requests table is empty (as it
    # is at startup, after the request DB is reset).
    _patch_active(monkeypatch, requests=set(), jobs={'active'})

    local_blob_storage.LocalFilesystemBlobStorage().reset_on_startup()

    assert active.exists()
    assert (active / 'file').read_text() == 'keep'
    assert not stale.exists()
    assert not logs.exists()
    # The client dir itself is preserved.
    assert client_dir.exists()


def test_reset_on_startup_does_not_follow_symlinks(tmp_path, monkeypatch):
    client_dir = tmp_path / 'clients'
    _patch_client_dir(monkeypatch, client_dir)
    active = _make_blob(client_dir, 'user1', 'active')

    # An external directory that a symlink under the client dir points to. It
    # must not be touched: the symlink should be unlinked, not followed.
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'important').write_text('do-not-delete')
    link = client_dir / 'user1' / 'link'
    link.symlink_to(external)

    _patch_active(monkeypatch, requests=set(), jobs={'active'})

    local_blob_storage.LocalFilesystemBlobStorage().reset_on_startup()

    assert active.exists()
    assert not link.exists()  # the symlink itself is removed
    assert external.exists()  # ... but its target is untouched
    assert (external / 'important').read_text() == 'do-not-delete'


def test_reset_on_startup_honors_request_refs(tmp_path, monkeypatch):
    client_dir = tmp_path / 'clients'
    _patch_client_dir(monkeypatch, client_dir)
    req_blob = _make_blob(client_dir, 'user1', 'req')
    _patch_active(monkeypatch, requests={'req'}, jobs=set())

    local_blob_storage.LocalFilesystemBlobStorage().reset_on_startup()

    assert req_blob.exists()


def test_reset_on_startup_wipes_when_none_active(tmp_path, monkeypatch):
    client_dir = tmp_path / 'clients'
    _patch_client_dir(monkeypatch, client_dir)
    _make_blob(client_dir, 'user1', 'b1')
    _patch_active(monkeypatch, requests=set(), jobs=set())

    local_blob_storage.LocalFilesystemBlobStorage().reset_on_startup()

    assert not client_dir.exists()


def test_reset_on_startup_preserves_all_on_query_error(tmp_path, monkeypatch):
    client_dir = tmp_path / 'clients'
    _patch_client_dir(monkeypatch, client_dir)
    blob = _make_blob(client_dir, 'user1', 'b1')

    def _boom():
        raise RuntimeError('db down')

    monkeypatch.setattr(
        'sky.server.requests.requests.get_active_file_mounts_blob_ids', _boom)
    monkeypatch.setattr('sky.jobs.state.get_active_file_mounts_blob_ids', _boom)

    local_blob_storage.LocalFilesystemBlobStorage().reset_on_startup()

    # On query failure we err on the side of preserving blobs.
    assert blob.exists()


def test_reset_on_startup_noop_when_missing(tmp_path, monkeypatch):
    client_dir = tmp_path / 'clients'
    _patch_client_dir(monkeypatch, client_dir)
    _patch_active(monkeypatch, requests=set(), jobs=set())
    # Should not raise when the client dir does not exist.
    local_blob_storage.LocalFilesystemBlobStorage().reset_on_startup()
    assert not client_dir.exists()
