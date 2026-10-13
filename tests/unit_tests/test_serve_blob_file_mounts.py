"""Serve replica launches re-resolve the client's upload blob.

In consolidation mode without a cloud bucket, a service or pool task keeps its
workdir and file mount sources as paths inside the client's upload blob on the
API server. A blob backend may keep blobs in shared storage and extract them to
a host-local cache, and on a multi-replica API server the controller, and every
launch request it sends, can run on a different replica than the one that
handled the original request. The extracted paths then do not exist there.

These tests model that with a backend that extracts blobs into a per-host cache;
`_HostCacheBlobStorage.move_to_new_host` empties the cache, as on a replica that
has never resolved the blob.
"""
# pylint: disable=invalid-name,protected-access,redefined-outer-name
import contextlib
import pathlib
import shutil
from typing import Any, Dict, List, Tuple
from unittest import mock

import jinja2
import pytest

from sky import serve
from sky.client import sdk
from sky.serve import replica_managers
from sky.server import common as server_common
from sky.server.blob import blob_storage
from sky.server.requests import payloads
from sky.skylet import constants
from sky.utils import thread_utils
from sky.utils import yaml_utils

_USER = 'abcd1234'
_BLOB_ID = 'b' * 64


class _HostCacheBlobStorage(blob_storage.BlobStorage):
    """Keeps blobs in a shared store and extracts them to a host-local cache.

    The cache path of a blob is the same on every host, but its contents only
    exist on a host that has resolved the blob.
    """

    def __init__(self, root: pathlib.Path):
        self._shared = root / 'shared'
        self._cache = root / 'cache'
        self.resolved: List[Tuple[str, str]] = []

    def move_to_new_host(self) -> None:
        shutil.rmtree(self._cache, ignore_errors=True)

    def blobs_dir(self, user_id: str) -> pathlib.Path:
        return self._shared / user_id

    def user_roots(self, user_id: str) -> List[pathlib.Path]:
        return [self._shared / user_id, self._cache / user_id]

    def resolve_blob_to_dir(self, user_id: str, blob_id: str) -> str:
        self.resolved.append((user_id, blob_id))
        shared = self._shared / user_id / blob_id
        if not shared.is_dir():
            raise FileNotFoundError(f'Blob not found: {shared}.')
        cached = self._cache / user_id / blob_id
        if not cached.is_dir():
            shutil.copytree(shared, cached)
        return str(cached)

    async def blob_exists(self, user_id: str, blob_id: str) -> bool:
        return (self._shared / user_id / blob_id).is_dir()

    @contextlib.asynccontextmanager
    async def acquire_upload_lock(self, user_id: str, blob_id: str):
        del user_id, blob_id
        yield None

    async def store_blob(self, user_id: str, blob_id: str,
                         staging_dir: pathlib.Path) -> None:
        raise NotImplementedError

    def delete_blob(self, user_id: str, blob_id: str) -> None:
        raise NotImplementedError

    def list_blob_ids(self, user_id: str) -> List[Tuple[str, float]]:
        del user_id
        return []

    def release_stale_uploads(self, user_id: str) -> None:
        pass

    def list_users(self) -> List[str]:
        return []

    def reset_on_startup(self) -> None:
        pass


@pytest.fixture
def blob_env(tmp_path, monkeypatch):
    """A client upload stored as a blob, and the task the client sent."""
    storage = _HostCacheBlobStorage(tmp_path / 'blobs')
    monkeypatch.setattr(blob_storage, '_blob_storage', storage)
    monkeypatch.setattr(server_common, 'API_SERVER_CLIENT_DIR',
                        tmp_path / 'clients')
    monkeypatch.setenv(constants.USER_ID_ENV_VAR, _USER)

    # The client's paths only exist on the client. The upload stores each
    # source under its absolute client path inside the blob.
    client_workdir = tmp_path / 'client' / 'workdir'
    client_file = client_workdir / 'hello.txt'
    blob_workdir = (tmp_path / 'blobs' / 'shared' / _USER / _BLOB_ID /
                    str(client_workdir).lstrip('/'))
    blob_workdir.mkdir(parents=True)
    (blob_workdir / 'hello.txt').write_text('hello')

    client_task = {
        'workdir': str(client_workdir),
        'file_mounts': {
            '/data/hello.txt': str(client_file)
        },
        'file_mounts_mapping': {
            str(client_workdir): str(client_workdir),
            str(client_file): str(client_file),
        },
        'pool': {
            'workers': 1
        },
        'run': 'cat /data/hello.txt',
    }
    return storage, yaml_utils.dump_yaml_str(client_task)


def _pool_apply_on_api_server(client_task_yaml: str) -> Dict[str, Any]:
    body = payloads.JobsPoolApplyBody(task=client_task_yaml,
                                      pool_name='pool',
                                      mode=serve.UpdateMode.ROLLING,
                                      file_mounts_blob_id=_BLOB_ID)
    return body.to_kwargs()


class _FakeApiServer:
    """Stands in for the API server that a controller's `sdk.launch` talks to.

    It handles /launch the way the server does: translate the task's mounts
    (re-resolving the blob when one is passed), then validate the task as
    `execution.launch` does before provisioning.
    """

    def __init__(self, storage: _HostCacheBlobStorage):
        self._storage = storage
        self.launch_bodies: List[payloads.LaunchBody] = []
        self.launch_errors: List[Exception] = []

    def request(self, method: str, path: str, json=None, **kwargs):
        del method, kwargs
        response = mock.MagicMock()
        response.status_code = 200
        response.headers = {'X-Skypilot-Request-ID': 'req-1'}
        if path == '/launch':
            # The launch request may be handled by yet another replica.
            self._storage.move_to_new_host()
            body = payloads.LaunchBody(**json)
            self.launch_bodies.append(body)
            try:
                dag = body.to_kwargs()['task']
                for task in dag.tasks:
                    task.validate()
            except Exception as e:  # pylint: disable=broad-except
                self.launch_errors.append(e)
        return response

    def stream_and_get(self, request_id):
        del request_id
        if self.launch_errors:
            raise self.launch_errors[-1]


def _launch_replica_from_controller(tmp_path: pathlib.Path,
                                    storage: _HostCacheBlobStorage,
                                    service_yaml: str,
                                    blob_id) -> _FakeApiServer:
    server = _FakeApiServer(storage)
    with mock.patch.object(server_common, 'check_server_healthy_or_start_fn'), \
         mock.patch.object(server_common, 'make_authenticated_request',
                           side_effect=server.request), \
         mock.patch.object(sdk, 'stream_and_get',
                           side_effect=server.stream_and_get), \
         mock.patch.object(replica_managers, 'terminate_cluster'):
        replica_managers.launch_cluster(
            replica_id=1,
            yaml_content=service_yaml,
            cluster_name='pool-1',
            log_file=str(tmp_path / 'replica-1.log'),
            replica_to_request_id=thread_utils.ThreadSafeDict(),
            replica_to_launch_cancelled=thread_utils.ThreadSafeDict(),
            max_retry=1,
            file_mounts_blob_id=blob_id)
    return server


def test_pool_apply_passes_blob_id(blob_env):
    storage, client_task_yaml = blob_env
    kwargs = _pool_apply_on_api_server(client_task_yaml)
    assert kwargs['file_mounts_blob_id'] == _BLOB_ID
    # The task's sources now point into this host's extract of the blob.
    task = kwargs['task']
    cache_dir = storage.resolve_blob_to_dir(_USER, _BLOB_ID)
    assert task.workdir.startswith(cache_dir)
    assert task.file_mounts['/data/hello.txt'].startswith(cache_dir)


def test_replica_launch_on_another_host_resolves_blob(tmp_path, blob_env):
    """The regression: the API server replica that handled `pool apply`
    extracted the blob, but the controller and its launch requests run on
    other replicas, where the extracted paths do not exist."""
    storage, client_task_yaml = blob_env
    kwargs = _pool_apply_on_api_server(client_task_yaml)
    service_yaml = yaml_utils.dump_yaml_str(kwargs['task'].to_yaml_config())

    # The controller runs on another replica.
    storage.move_to_new_host()
    server = _launch_replica_from_controller(tmp_path, storage, service_yaml,
                                             _BLOB_ID)

    assert not server.launch_errors, server.launch_errors
    assert len(server.launch_bodies) == 1
    assert server.launch_bodies[0].file_mounts_blob_id == _BLOB_ID
    # The replica that handled the launch extracted the blob itself.
    workdir = pathlib.Path(kwargs['task'].workdir)
    assert (workdir / 'hello.txt').read_text() == 'hello'


def test_replica_launch_without_blob_id_fails_on_another_host(
        tmp_path, blob_env):
    """Without the blob id, nothing re-extracts the blob on the new host and
    the launch fails the way the regression did."""
    storage, client_task_yaml = blob_env
    kwargs = _pool_apply_on_api_server(client_task_yaml)
    service_yaml = yaml_utils.dump_yaml_str(kwargs['task'].to_yaml_config())

    storage.move_to_new_host()
    with pytest.raises(RuntimeError, match='Failed to launch'):
        _launch_replica_from_controller(tmp_path, storage, service_yaml, None)


def test_replica_launch_without_local_sources_drops_blob_id(tmp_path, blob_env):
    """Once the task has no local sources (e.g. a later version whose sources
    went through a bucket), the launch does not carry the blob."""
    storage, _ = blob_env
    service_yaml = yaml_utils.dump_yaml_str({'run': 'echo hi'})
    server = _launch_replica_from_controller(tmp_path, storage, service_yaml,
                                             _BLOB_ID)
    assert not server.launch_errors, server.launch_errors
    assert server.launch_bodies[0].file_mounts_blob_id is None
    assert not storage.resolved


@pytest.mark.parametrize('blob_id', [None, _BLOB_ID])
def test_controller_template_passes_blob_id(blob_id):
    template_path = (pathlib.Path(constants.__file__).parents[1] / 'templates' /
                     'sky-serve-controller.yaml.j2')
    rendered = jinja2.Template(template_path.read_text()).render(
        service_name='pool',
        remote_task_yaml_path='~/task.yaml',
        local_task_yaml_path='/tmp/task.yaml',
        controller_log_file='~/controller.log',
        entrypoint='entry',
        user_hash=_USER,
        file_mounts_blob_id=blob_id,
        consolidation_mode_job_id=1,
        modified_catalogs={},
        local_to_controller_file_mounts={},
        local_user_config_path=None,
        plugin_wheel_file_mounts={},
        local_plugins_config_path=None,
        cloud_dependencies_installation_commands=[],
        controller_envs={},
        use_tls=False,
        sky_python_cmd='python',
    )
    run = yaml_utils.safe_load(rendered)['run']
    if blob_id is None:
        assert '--file-mounts-blob-id' not in run
    else:
        assert f'--file-mounts-blob-id {blob_id}' in run
