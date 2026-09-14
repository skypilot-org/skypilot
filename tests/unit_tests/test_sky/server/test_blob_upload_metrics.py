"""Blob lookup metrics through the client upload and server routes."""
import functools

import fastapi
from fastapi import testclient
import prometheus_client as prom
import pytest

from sky import dag as dag_lib
from sky import task as task_lib
from sky.client import common as client_common
from sky.metrics import utils as metrics_utils
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server import server
from sky.server.blob import local_blob_storage

_METRIC = 'sky_apiserver_blob_check_size_bytes'
_UPLOAD_ROUTE_PATHS = ('/upload_v2/blob', '/upload_v2')


@pytest.fixture(name='blob_upload_app')
def _blob_upload_app(tmp_path, monkeypatch):
    app = fastapi.FastAPI()
    # FastAPI >=0.137 also puts private included-router wrappers, which have
    # no ``path``, in ``app.routes``. The upload routes are registered on the
    # app itself, so a guarded flat scan still reaches both.
    upload_routes = [
        route for route in server.app.routes
        if getattr(route, 'path', None) in _UPLOAD_ROUTE_PATHS
    ]
    found_paths = {route.path for route in upload_routes}
    assert found_paths == set(_UPLOAD_ROUTE_PATHS), found_paths
    app.router.routes.extend(upload_routes)
    requests = []

    @app.middleware('http')
    async def auth_and_record(request, call_next):
        request.state.auth_user = None
        requests.append((request.method, dict(request.query_params)))
        return await call_next(request)

    monkeypatch.setattr(server_common, 'API_SERVER_CLIENT_DIR',
                        tmp_path / 'clients')
    storage = local_blob_storage.LocalFilesystemBlobStorage()
    monkeypatch.setattr(server.bs, 'get_blob_storage', lambda: storage)
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', True)
    registry = prom.CollectorRegistry()
    registry.register(metrics_utils.SKY_APISERVER_BLOB_CHECK_SIZE_BYTES)
    return app, requests, registry


def _sample(registry, suffix, result, **labels):
    return registry.get_sample_value(f'{_METRIC}_{suffix}', {
        'result': result,
        **labels,
    }) or 0


@pytest.mark.parametrize('chunk_bytes', [95_000_000, 256])
def test_client_upload_records_size_on_miss_and_hit(blob_upload_app, tmp_path,
                                                    monkeypatch, chunk_bytes):
    app, requests, registry = blob_upload_app
    client = testclient.TestClient(app)
    monkeypatch.setattr(server_common, 'is_api_server_local', lambda: False)
    monkeypatch.setattr(server_common, 'get_server_url',
                        lambda: 'http://testserver')
    monkeypatch.setattr(server_common, 'get_api_cookie_jar', lambda: None)
    monkeypatch.setattr(server_common, 'make_authenticated_request',
                        client.request)
    monkeypatch.setattr(client_common.httpx, 'Client',
                        lambda **kwargs: testclient.TestClient(app))
    monkeypatch.setattr(client_common.versions, 'get_remote_api_version',
                        lambda: server_constants.API_VERSION)
    monkeypatch.setattr(client_common.common_utils, 'get_user_hash',
                        lambda: 'test-user')
    monkeypatch.setattr(client_common.service_account_auth,
                        'get_service_account_headers', lambda: {})
    monkeypatch.setattr(client_common, 'FILE_UPLOAD_LOGS_DIR',
                        str(tmp_path / 'logs'))
    monkeypatch.setattr(client_common, '_FILE_UPLOAD_LOCK_DIR',
                        str(tmp_path / 'locks'))
    monkeypatch.setattr(client_common, '_UPLOAD_CHUNK_BYTES', chunk_bytes)
    monkeypatch.setattr(
        client_common.tempfile, 'NamedTemporaryFile',
        functools.partial(client_common.tempfile.NamedTemporaryFile,
                          dir=tmp_path))

    workdir = tmp_path / 'workdir'
    workdir.mkdir()
    (workdir / 'code.py').write_text('print("hello")\n' * 1000)
    mount = tmp_path / 'data.txt'
    mount.write_text('data\n' * 1000)
    dag = dag_lib.Dag()
    dag.add(
        task_lib.Task(workdir=str(workdir),
                      file_mounts={'/data.txt': str(mount)}))
    before = {(result, suffix): _sample(registry, suffix, result)
              for result in ('hit', 'miss') for suffix in ('count', 'sum')}

    _, blob_id = client_common.upload_mounts_to_api_server(dag)
    checks = [params for method, params in requests if method == 'GET']
    assert len(checks) == 1
    size = int(checks[0]['size_bytes'])
    assert 0 < size < (workdir /
                       'code.py').stat().st_size + mount.stat().st_size
    uploads = [params for method, params in requests if method == 'POST']
    assert len(uploads) == (size + chunk_bytes - 1) // chunk_bytes
    assert _sample(registry, 'count', 'miss') == before['miss', 'count'] + 1
    assert _sample(registry, 'sum', 'miss') == before['miss', 'sum'] + size
    assert _sample(registry, 'count', 'hit') == before['hit', 'count']
    blob_dir = server.bs.get_blob_storage().get_target_dir('test-user', blob_id)
    assert (blob_dir /
            str(mount).lstrip('/')).read_bytes() == mount.read_bytes()

    requests.clear()
    _, reused_blob_id = client_common.upload_mounts_to_api_server(dag)
    assert reused_blob_id == blob_id
    assert len(requests) == 1
    method, params = requests[0]
    assert method == 'GET'
    assert int(params['size_bytes']) == size
    assert _sample(registry, 'count', 'hit') == before['hit', 'count'] + 1
    assert _sample(registry, 'sum', 'hit') == before['hit', 'sum'] + size
    assert _sample(registry, 'count', 'miss') == before['miss', 'count'] + 1
    assert not list(tmp_path.glob('*.zip'))


@pytest.mark.parametrize(
    'size', [None, '-1', 'nan', '1.5',
             str(2**63), str(10**309)])
def test_missing_or_invalid_size_not_observed(blob_upload_app, size):
    app, _, registry = blob_upload_app
    before = _sample(registry, 'count', 'miss')
    params = {'user_hash': 'test-user', 'blob_id': 'a' * 64}
    if size is not None:
        params['size_bytes'] = size
    response = testclient.TestClient(app).get('/upload_v2/blob', params=params)
    assert response.status_code == (200 if size is None else 422)
    assert _sample(registry, 'count', 'miss') == before


@pytest.mark.parametrize('size', [0, 1024, 64 * 2**30])
def test_size_histogram_buckets(blob_upload_app, size):
    app, _, registry = blob_upload_app
    before_count = _sample(registry, 'count', 'miss')
    before_sum = _sample(registry, 'sum', 'miss')
    before_bucket = _sample(registry, 'bucket', 'miss', le='1024.0')
    response = testclient.TestClient(app).get('/upload_v2/blob',
                                              params={
                                                  'user_hash': 'test-user',
                                                  'blob_id': 'a' * 64,
                                                  'size_bytes': size,
                                              })
    assert response.status_code == 200
    assert response.json() == {'exists': False}
    assert _sample(registry, 'count', 'miss') == before_count + 1
    assert _sample(registry, 'sum', 'miss') == before_sum + size
    assert _sample(registry, 'bucket', 'miss',
                   le='1024.0') == before_bucket + (size <= 1024)


def test_disabled_metrics_not_observed(blob_upload_app, monkeypatch):
    app, _, registry = blob_upload_app
    monkeypatch.setattr(metrics_utils, 'METRICS_ENABLED', False)
    before = _sample(registry, 'count', 'miss')
    response = testclient.TestClient(app).get('/upload_v2/blob',
                                              params={
                                                  'user_hash': 'test-user',
                                                  'blob_id': 'a' * 64,
                                                  'size_bytes': 1024,
                                              })
    assert response.status_code == 200
    assert _sample(registry, 'count', 'miss') == before
