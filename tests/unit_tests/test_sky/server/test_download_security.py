"""Download authorization tests using real files and archive creation."""

import pathlib
from unittest import mock
import zipfile

import fastapi
import pytest

from sky import models
from sky.jobs.server import server as jobs_server
from sky.serve.server import server as serve_server
from sky.server import server
from sky.server.requests import payloads
from sky.skylet import constants


@pytest.fixture(name='download_env')
def fixture_download_env(tmp_path, monkeypatch):
    clients = tmp_path / 'clients'
    monkeypatch.setattr(server.common, 'API_SERVER_CLIENT_DIR', clients)
    roots = {}
    for user in ('alice', 'bob'):
        root = server.common.api_server_user_logs_dir_prefix(user)
        root.mkdir(parents=True)
        (root / 'run.log').write_text(user)
        roots[user] = root
    secret = tmp_path / 'secret'
    secret.write_text('private credential')
    (roots['alice'] / 'escape').symlink_to(secret)
    sibling = roots['alice'].with_name('sky_logs_evil')
    sibling.mkdir()
    (sibling / 'secret').write_text('private credential')
    request = fastapi.Request({'type': 'http', 'query_string': b''})
    request.state.auth_user = models.User(id='alice', name='Alice')
    return request, roots, secret


def body(path, user='alice'):
    return payloads.DownloadBody(folder_paths=[str(path)],
                                 env_vars={constants.USER_ID_ENV_VAR: user})


@pytest.mark.asyncio
@pytest.mark.parametrize('attack', [
    'traversal', 'absolute', 'sibling', 'symlink', 'cross_user',
    'user_traversal'
])
async def test_download_rejects_escape(download_env, attack):
    request, roots, secret = download_env
    user = 'alice'
    paths = {
        'traversal': roots['alice'] / '..' / '..' / '..' / 'secret',
        'absolute': secret,
        'sibling': roots['alice'].with_name('sky_logs_evil') / 'secret',
        'symlink': roots['alice'] / 'escape',
        'cross_user': roots['bob'] / 'run.log',
        'user_traversal': secret,
    }
    if attack == 'cross_user':
        user = 'bob'
    elif attack == 'user_traversal':
        request.state.auth_user = None
        user = '../../../'
    with pytest.raises(fastapi.HTTPException) as exc:
        await server.download(body(paths[attack], user), request)
    assert exc.value.status_code == 400
    assert not list(roots['alice'].glob('folder_*.zip'))


@pytest.mark.asyncio
@pytest.mark.parametrize('relative', ['home', 'items'])
@pytest.mark.parametrize('anonymous', [False, True])
async def test_download_archive(download_env, relative, anonymous):
    request, roots, _ = download_env
    request.scope['query_string'] = f'relative={relative}'.encode()
    if anonymous:
        request.state.auth_user = None
    # The authenticated identity controls access even with a forged body ID.
    user = 'alice' if anonymous else 'bob'
    response = await server.download(body(roots['alice'] / 'run.log', user),
                                     request)
    with zipfile.ZipFile(response.path) as archive:
        assert len(archive.namelist()) == 1
        assert archive.read(archive.namelist()[0]) == b'alice'
        if relative == 'items':
            assert archive.namelist() == ['run.log']
    await response.background()
    assert not pathlib.Path(response.path).exists()


@pytest.mark.asyncio
async def test_download_directory_does_not_read_nested_symlink(download_env):
    request, roots, _ = download_env
    response = await server.download(body(roots['alice']), request)
    with zipfile.ZipFile(response.path) as archive:
        assert b'private credential' not in [
            archive.read(name) for name in archive.namelist()
        ]
    await response.background()


@pytest.mark.asyncio
async def test_download_missing_allowed_path(download_env):
    request, roots, _ = download_env
    with pytest.raises(fastapi.HTTPException) as exc:
        await server.download(body(roots['alice'] / 'missing'), request)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_download_logs_uses_authenticated_destination(download_env):
    request, roots, _ = download_env
    request.state.request_id = 'download-test'
    download_body = payloads.ClusterJobsDownloadLogsBody(
        cluster_name='test',
        job_ids=None,
        env_vars={constants.USER_ID_ENV_VAR: 'bob'})
    with mock.patch.object(server.executor,
                           'schedule_request_async',
                           new_callable=mock.AsyncMock) as schedule:
        await server.download_logs(request, download_body)
    assert pathlib.Path(download_body.local_dir) == roots['alice']
    assert schedule.call_args.kwargs['auth_user'] == request.state.auth_user


@pytest.mark.asyncio
async def test_download_expands_and_normalizes_path(download_env, monkeypatch):
    request, _, secret = download_env
    monkeypatch.setenv('HOME', str(secret.parent))
    path = '~/clients/alice/sky_logs/../sky_logs/run.log'
    response = await server.download(body(path), request)
    with zipfile.ZipFile(response.path) as archive:
        assert archive.read(archive.namelist()[0]) == b'alice'
    await response.background()


@pytest.mark.asyncio
async def test_download_separate_staging_root(download_env, tmp_path):
    request, _, _ = download_env
    staging = tmp_path / 'staging' / 'alice'
    staging.mkdir(parents=True)
    (staging / 'run.log').write_text('staged log')
    with mock.patch.object(server.bs.get_blob_storage(),
                           'download_tmp_dir',
                           return_value=str(staging)):
        response = await server.download(body(staging / 'run.log'), request)
    with zipfile.ZipFile(response.path) as archive:
        assert archive.read(archive.namelist()[0]) == b'staged log'
    await response.background()


_STAGING_HANDLERS = [
    (server.download_logs, payloads.ClusterJobsDownloadLogsBody,
     dict(cluster_name='test', job_ids=None)),
    (jobs_server.download_logs, payloads.JobsDownloadLogsBody,
     dict(name=None, job_id=1)),
    (jobs_server.pool_download_logs, payloads.JobsPoolDownloadLogsBody,
     dict(pool_name='test', local_dir='', targets=None)),
    (serve_server.download_logs, payloads.ServeDownloadLogsBody,
     dict(service_name='test', local_dir='', targets=None)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('handler,body_type,kwargs', _STAGING_HANDLERS)
@pytest.mark.parametrize('authenticated', [True, False])
async def test_staging_then_download(download_env, handler, body_type, kwargs,
                                     authenticated):
    request, roots, _ = download_env
    request.state.request_id = 'staging-test'
    if not authenticated:
        request.state.auth_user = None
    expected_user = 'alice' if authenticated else 'bob'
    staging_body = body_type(**kwargs,
                             env_vars={constants.USER_ID_ENV_VAR: 'bob'})
    with mock.patch.object(server.executor,
                           'schedule_request_async',
                           new_callable=mock.AsyncMock) as schedule:
        await handler(request, staging_body)
    staged_dir = pathlib.Path(staging_body.local_dir)
    assert staged_dir == roots[expected_user] or roots[
        expected_user] in staged_dir.parents
    assert schedule.call_args.kwargs['request_body'] is staging_body
    assert schedule.call_args.kwargs['auth_user'] == request.state.auth_user
    # Populate the selected destination without contacting a remote cluster.
    staged_log = staged_dir / 'staged.log'
    staged_log.write_text('downloaded logs')
    response = await server.download(body(staged_log, 'bob'), request)
    with zipfile.ZipFile(response.path) as archive:
        assert archive.read(archive.namelist()[0]) == b'downloaded logs'
    await response.background()
    if authenticated:
        with pytest.raises(fastapi.HTTPException) as exc:
            await server.download(body(roots['bob'] / 'run.log', 'bob'),
                                  request)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize('handler,body_type,kwargs', _STAGING_HANDLERS)
async def test_staging_rejects_anonymous_path_component(download_env, handler,
                                                        body_type, kwargs):
    request, _, _ = download_env
    request.state.auth_user = None
    staging_body = body_type(**kwargs,
                             env_vars={constants.USER_ID_ENV_VAR: '../bob'})
    with mock.patch.object(server.bs.get_blob_storage(),
                           'download_tmp_dir') as staging:
        with pytest.raises(fastapi.HTTPException) as exc:
            await handler(request, staging_body)
    assert exc.value.status_code == 400
    staging.assert_not_called()
