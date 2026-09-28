"""Upload authorization tests: user_hash and upload_id may not escape.

Mirrors test_download_security.py. The upload handlers join a client-supplied
user_hash (and, in v1, upload_id) under API_SERVER_CLIENT_DIR, so a value that
is not a single path component could write into another user's tree, escape the
clients dir, or (v1 cleanup) delete an arbitrary directory.
"""

import asyncio
import datetime
import hashlib
import io
import pathlib
import zipfile

import fastapi
from fastapi import testclient
import pytest

from sky import models
from sky.server import server
from sky.server.blob import local_blob_storage
from sky.skylet import constants
from sky.utils import common_utils

ESCAPES = [
    '/abs/outside', '../escaped', '..', '.', '/', 'a/b', 'a\\b', '', '\x00'
]
VALID_V2_ID = hashlib.sha256(b'x').hexdigest()  # 64 hex
VALID_V1_ID = 'sky-2025-01-17-09-10-13-933602-35d31c22'


@pytest.fixture(name='clients')
def fixture_clients(tmp_path, monkeypatch):
    clients = tmp_path / 'clients'
    clients.mkdir()
    monkeypatch.setattr(server.common, 'API_SERVER_CLIENT_DIR', clients)
    server.upload_ids_to_cleanup.clear()
    yield clients
    server.upload_ids_to_cleanup.clear()


def _zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('marker.txt', 'content')
    return buf.getvalue()


def _request(auth=None):
    request = fastapi.Request({'type': 'http', 'query_string': b''})
    request.state.auth_user = auth
    return request


def _outside(clients):
    # Any path that was created outside the clients dir means an escape.
    root = clients.parent
    return [
        str(p.relative_to(root))
        for p in root.rglob('*')
        if p.is_dir() and clients not in p.parents and p != clients
    ]


# --- the rule ---------------------------------------------------------------


def test_is_single_path_component():
    for ok in ['alice', 'sa-abc-123', 'a', '0']:
        assert common_utils.is_single_path_component(ok)
    for bad in ESCAPES + [None]:
        assert not common_utils.is_single_path_component(bad)


def test_owner_user_id_anonymous_uses_user_hash():
    assert server.download_utils.owner_user_id(_request(), 'alice') == 'alice'


def test_owner_user_id_auth_wins_over_user_hash():
    auth = models.User(id='alice', name='Alice')
    # Even a malicious user_hash is ignored when authenticated.
    assert server.download_utils.owner_user_id(_request(auth),
                                               '../evil') == 'alice'


@pytest.mark.parametrize('bad', ESCAPES)
def test_owner_user_id_rejects_escape(bad):
    with pytest.raises(fastapi.HTTPException) as exc:
        server.download_utils.owner_user_id(_request(), bad)
    assert exc.value.status_code == 400


def test_blobs_dir_rejects_unsafe_user_id():
    storage = local_blob_storage.LocalFilesystemBlobStorage()
    for bad in ['../x', '/x', '..', 'a/b']:
        with pytest.raises(ValueError):
            storage.blobs_dir(bad)


# --- v2 /upload_v2 and /upload_v2/blob --------------------------------------


@pytest.mark.parametrize('bad', ESCAPES)
def test_upload_v2_rejects_user_hash(clients, bad):
    c = testclient.TestClient(server.app, raise_server_exceptions=False)
    r = c.post('/upload_v2',
               params=dict(user_hash=bad,
                           upload_id=VALID_V2_ID,
                           chunk_index=0,
                           total_chunks=1),
               files={'file': ('f.zip', _zip_bytes())})
    assert r.status_code == 400
    assert not _outside(clients)


@pytest.mark.parametrize('bad', ESCAPES)
def test_blob_exists_rejects_user_hash(clients, bad):
    c = testclient.TestClient(server.app, raise_server_exceptions=False)
    r = c.get('/upload_v2/blob',
              params=dict(user_hash=bad, blob_id=VALID_V2_ID))
    assert r.status_code == 400


def test_upload_v2_success_stays_under_user(clients):
    c = testclient.TestClient(server.app, raise_server_exceptions=False)
    r = c.post('/upload_v2',
               params=dict(user_hash='alice',
                           upload_id=VALID_V2_ID,
                           chunk_index=0,
                           total_chunks=1),
               files={'file': ('f.zip', _zip_bytes())})
    assert r.status_code == 200
    markers = list(clients.rglob('marker.txt'))
    assert markers and all('alice' in m.parts for m in markers)
    assert not _outside(clients)


# --- v1 /upload -------------------------------------------------------------


@pytest.mark.parametrize('bad', ESCAPES)
def test_upload_v1_rejects_user_hash(clients, bad):
    c = testclient.TestClient(server.app, raise_server_exceptions=False)
    r = c.post('/upload',
               params=dict(user_hash=bad,
                           upload_id=VALID_V1_ID,
                           chunk_index=0,
                           total_chunks=1),
               files={'file': ('f.zip', b'z')})
    assert r.status_code == 400
    assert not server.upload_ids_to_cleanup
    assert not _outside(clients)


@pytest.mark.parametrize('bad_id', ['/abs/victim', '../../..', '/', 'not-a-ts'])
def test_upload_v1_invalid_upload_id_not_queued(clients, bad_id):
    """The killer regression: a rejected upload_id must not be queued for a
    later rmtree, and it must be a 400 (not a 500)."""
    c = testclient.TestClient(server.app, raise_server_exceptions=False)
    r = c.post('/upload',
               params=dict(user_hash='alice',
                           upload_id=bad_id,
                           chunk_index=0,
                           total_chunks=1),
               files={'file': ('f.zip', b'z')})
    assert r.status_code == 400
    assert not server.upload_ids_to_cleanup


@pytest.mark.asyncio
async def test_upload_v1_auth_user_keys_cleanup_by_owner(clients):
    """With auth on, cleanup is keyed on the authenticated id, not user_hash."""
    auth = models.User(id='alice', name='Alice')
    request = _request(auth)
    # No real body on this request, so the chunk receive fails after the
    # side effects we care about have run; that is enough to inspect the queue.
    try:
        await server.upload_zip_file(request,
                                     user_hash='../evil',
                                     upload_id=VALID_V1_ID,
                                     chunk_index=0,
                                     total_chunks=1)
    except Exception:  # pylint: disable=broad-except
        pass
    assert (VALID_V1_ID, 'alice') in server.upload_ids_to_cleanup
    assert (VALID_V1_ID, '../evil') not in server.upload_ids_to_cleanup


# --- cleanup loop robustness ------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_survives_bad_entry(clients, monkeypatch):
    past = datetime.datetime.now() - datetime.timedelta(days=1)
    server.upload_ids_to_cleanup[('/', 'alice')] = past  # would raise on rmtree
    server.upload_ids_to_cleanup[(VALID_V1_ID, 'alice')] = past

    deleted = []
    monkeypatch.setattr(server.shutil, 'rmtree',
                        lambda p, **_: deleted.append(str(p)))
    monkeypatch.setattr(pathlib.Path, 'unlink', lambda *a, **k: None)

    calls = {'n': 0}

    async def fake_sleep(_):
        calls['n'] += 1
        if calls['n'] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(server.asyncio, 'sleep', fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await server.cleanup_upload_ids()

    # The bad entry did not stop the valid one: the valid entry is cleaned and
    # dequeued, while the failing entry is kept for a later retry (not dropped).
    assert any(VALID_V1_ID in d for d in deleted)
    assert (VALID_V1_ID, 'alice') not in server.upload_ids_to_cleanup
    assert ('/', 'alice') in server.upload_ids_to_cleanup


# --- /launch env user id (9th site) -----------------------------------------


@pytest.mark.parametrize('bad', ['/abs', '../escaped', '..', 'a/b', '', '\x00'])
def test_process_mounts_rejects_unsafe_env_user_id(clients, tmp_path, bad):
    """The env-var user id is joined and mkdir'd by the launch path, so it must
    be validated too (auth-off traversal via /launch, not an upload endpoint)."""
    env = {constants.USER_ID_ENV_VAR: bad}
    task = 'name: t\nrun: echo hi\n'
    with pytest.raises(ValueError):
        server.common.process_mounts_in_task_on_api_server(task,
                                                           env,
                                                           workdir_only=False)
    # Red on master: there the absolute id creates <bad>/file_mounts and does
    # not raise.
    assert not _outside(clients)


def test_process_mounts_absolute_env_id_creates_nothing(clients, tmp_path):
    """Point the escape inside tmp_path so the tree diff can see it."""
    escape = tmp_path / 'escape_via_launch'  # absolute, outside clients/
    env = {constants.USER_ID_ENV_VAR: str(escape)}
    task = 'name: t\nrun: echo hi\n'
    with pytest.raises(ValueError):
        server.common.process_mounts_in_task_on_api_server(task,
                                                           env,
                                                           workdir_only=False)
    assert not escape.exists()


def test_process_mounts_valid_env_id_works(clients):
    """Baseline: a normal user id still resolves and creates its own dir."""
    env = {constants.USER_ID_ENV_VAR: 'alice'}
    task = 'name: t\nrun: echo hi\n'
    server.common.process_mounts_in_task_on_api_server(task,
                                                       env,
                                                       workdir_only=False)
    assert (clients / 'alice' / 'file_mounts').is_dir()
    assert not _outside(clients)


# --- auth id wins over user_hash at the handlers (not just in owner_user_id) --


def _stream_request(body: bytes, auth=None):
    """A minimal streaming POST Request the upload handlers can read."""
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        return {'type': 'http.disconnect'}

    request = fastapi.Request(
        {
            'type': 'http',
            'method': 'POST',
            'path': '/upload',
            'headers': [],
            'query_string': b''
        }, receive)
    request.state.auth_user = auth
    return request


@pytest.mark.asyncio
async def test_check_blob_exists_uses_auth_id_not_user_hash(clients):
    """A blob in bob's tree must not be found when authenticated as alice and
    querying user_hash=bob: the handler must look in alice's namespace."""
    (clients / 'bob' / 'file_mounts' / 'blobs' /
     VALID_V2_ID).mkdir(parents=True)
    auth = models.User(id='alice', name='Alice')
    res = await server.check_blob_exists(_request(auth),
                                         user_hash='bob',
                                         blob_id=VALID_V2_ID,
                                         size_bytes=None)
    assert res == {'exists': False}
    # Sanity: anonymously querying bob does find it, so the fixture is right.
    res2 = await server.check_blob_exists(_request(),
                                          user_hash='bob',
                                          blob_id=VALID_V2_ID,
                                          size_bytes=None)
    assert res2 == {'exists': True}


@pytest.mark.asyncio
async def test_upload_v2_auth_id_writes_under_auth_id(clients):
    """Authenticated as alice with user_hash=bob, the v2 blob must land under
    alice, never bob."""
    auth = models.User(id='alice', name='Alice')
    req = _stream_request(_zip_bytes(), auth)
    await server.upload_blob(req,
                             user_hash='bob',
                             upload_id=VALID_V2_ID,
                             chunk_index=0,
                             total_chunks=1)
    assert (clients / 'alice' / 'file_mounts' / 'blobs' / VALID_V2_ID).is_dir()
    assert not (clients / 'bob').exists()


@pytest.mark.asyncio
async def test_upload_v1_auth_id_writes_under_auth_id(clients):
    """Same for v1 /upload: bytes under alice, bob's dir untouched."""
    auth = models.User(id='alice', name='Alice')
    req = _stream_request(_zip_bytes(), auth)
    await server.upload_zip_file(req,
                                 user_hash='bob',
                                 upload_id=VALID_V1_ID,
                                 chunk_index=0,
                                 total_chunks=1)
    # v1 extracts into the user's file_mounts dir; must be alice's, not bob's.
    assert (clients / 'alice' / 'file_mounts' / 'marker.txt').exists()
    assert not (clients / 'bob').exists()
    # And cleanup is queued under the auth id.
    assert (VALID_V1_ID, 'alice') in server.upload_ids_to_cleanup


async def _run_one_cleanup_tick(monkeypatch):
    calls = {'n': 0}

    async def fake_sleep(_):
        calls['n'] += 1
        if calls['n'] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(server.asyncio, 'sleep', fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await server.cleanup_upload_ids()


@pytest.mark.asyncio
async def test_cleanup_keeps_entry_on_rmtree_error(clients, monkeypatch):
    """A real rmtree error (not a missing dir) must keep the entry for retry,
    so it must not be hidden by ignore_errors."""
    past = datetime.datetime.now() - datetime.timedelta(days=1)
    server.upload_ids_to_cleanup[(VALID_V1_ID, 'alice')] = past

    def rmtree(path, ignore_errors=False):  # honors the kwarg like shutil does
        if ignore_errors:
            return
        raise PermissionError('boom')

    monkeypatch.setattr(server.shutil, 'rmtree', rmtree)
    monkeypatch.setattr(pathlib.Path, 'unlink', lambda *a, **k: None)
    await _run_one_cleanup_tick(monkeypatch)
    assert (VALID_V1_ID, 'alice') in server.upload_ids_to_cleanup


@pytest.mark.asyncio
async def test_cleanup_treats_missing_dir_as_success(clients, monkeypatch):
    """A missing chunk dir (normal for a single-chunk upload) is not a failure:
    the entry is cleaned and dequeued."""
    past = datetime.datetime.now() - datetime.timedelta(days=1)
    server.upload_ids_to_cleanup[(VALID_V1_ID, 'alice')] = past

    def rmtree(path, ignore_errors=False):
        raise FileNotFoundError(path)

    monkeypatch.setattr(server.shutil, 'rmtree', rmtree)
    monkeypatch.setattr(pathlib.Path, 'unlink', lambda *a, **k: None)
    await _run_one_cleanup_tick(monkeypatch)
    assert (VALID_V1_ID, 'alice') not in server.upload_ids_to_cleanup
