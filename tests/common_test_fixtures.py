import base64
import os
import pickle
import tempfile
import time

import fastapi
from fastapi import testclient
import pandas as pd
import pytest
import requests

import sky
from sky import cli
from sky import global_user_state
from sky import sky_logging
from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
from sky.clouds.service_catalog import vsphere_catalog
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.serve import serve_state
from sky.server import common as server_common
from sky.server.requests import executor
from sky.server.requests import requests as api_requests
from sky.server.server import app
from sky.skylet import constants
from sky.utils import controller_utils
from sky.utils import message_utils
from sky.utils import registry

logger = sky_logging.init_logger("sky.pytest")


@pytest.fixture
def aws_config_region(monkeypatch: pytest.MonkeyPatch) -> str:
    from sky import skypilot_config
    region = 'us-east-2'
    if skypilot_config.loaded():
        ssh_proxy_command = skypilot_config.get_nested(
            ('aws', 'ssh_proxy_command'), None)
        if isinstance(ssh_proxy_command, dict) and ssh_proxy_command:
            region = list(ssh_proxy_command.keys())[0]
    return region


@pytest.fixture
def mock_client_requests(monkeypatch: pytest.MonkeyPatch, mock_queue,
                         mock_stream_utils, mock_redirect_log_file) -> None:
    """Fixture to mock HTTP requests using FastAPI's TestClient."""
    # This fixture automatically replaces `requests.get` and `requests.post`
    # with mocked versions that route requests through a FastAPI TestClient.
    # It is used to simulate server responses for testing purposes without
    # making actual HTTP requests.
    client = testclient.TestClient(app)
    original_requests_get = requests.get
    original_requests_post = requests.post

    def _execute_request(path: str, method: str,
                         response: fastapi.Response) -> None:
        request_id = server_common.get_request_id(response)
        request_obj = api_requests.get_request(request_id)
        # To have the mock effective, we do not start the backend request
        # workers, i.e. the requests placed in the database will not be
        # executed automatically. We manually call the executor._wrapper
        # here to execute the request.
        if request_obj is not None:
            ignore_return_value = mock_queue.get(request_id)
            if ignore_return_value is None and path == '/optimize':
                ignore_return_value = True
            logger.info(
                f'Mocking {method} request to {path} with request_id {request_id}, '
                f'running executor._wrapper(\'{request_id}\', {ignore_return_value})'
            )
            executor._request_execution_wrapper(request_id, ignore_return_value)

    def mock_http_request(method: str, url, *args, **kwargs):
        if method == 'GET':
            mock_func = client.get
            original_func = original_requests_get
        elif method == 'POST':
            mock_func = client.post
            original_func = original_requests_post
        else:
            raise ValueError(f'Unsupported method: {method}')
        if server_common.get_server_url() in url:
            logger.info(f'Mocking {method} request to {url} through TestClient')
            path = url.replace(server_common.get_server_url(), "")
            # Remove stream parameter as it's not supported by TestClient
            stream = kwargs.pop('stream', False)
            # Extract and format query parameters
            if 'params' in kwargs:
                kwargs['params'] = {
                    k: v for k, v in kwargs['params'].items() if v is not None
                }
            response = mock_func(path, *args, **kwargs)
            if not any(
                    path.startswith(p)
                    for p in ['/api/get', '/api/stream', '/api/status']):
                # These paths do not need to be executed
                _execute_request(path, method, response)
            # If streaming is requested, wrap the response content in an iterator
            if stream:
                content = response.content

                def iter_content(chunk_size=None):
                    # Yield the entire content as one chunk since this is a test
                    if not content:
                        yield None
                    yield content

                # Add iter_content method to response
                response.iter_content = iter_content

            return response
        else:
            return original_func(url, *args, **kwargs)

    # Mock `requests.post` to use TestClient.post
    def mock_post(url, *args, **kwargs):
        # Convert full URL to path for TestClient
        return mock_http_request('POST', url, *args, **kwargs)

    # Mock `requests.get` to use TestClient.get
    def mock_get(url, *args, **kwargs):
        return mock_http_request('GET', url, *args, **kwargs)

    # Use monkeypatch to replace `requests.post` and `requests.get`
    monkeypatch.setattr(requests, "post", mock_post)
    monkeypatch.setattr(requests, "get", mock_get)


@pytest.fixture
def enable_all_clouds(monkeypatch, request, mock_client_requests):
    """Create mock context managers for cloud configurations."""
    enabled_clouds = request.param if hasattr(request, 'param') else None
    if enabled_clouds is None:
        enabled_clouds = list(registry.CLOUD_REGISTRY.values())

    config_file = tempfile.NamedTemporaryFile(prefix='tmp_config_default',
                                              delete=False).name

    # Mock all the functions
    monkeypatch.setattr('sky.check.get_cached_enabled_clouds_or_refresh',
                        lambda *_, **__: enabled_clouds)
    monkeypatch.setattr('sky.check.check', lambda *_, **__: None)
    monkeypatch.setattr(
        'sky.clouds.service_catalog.aws_catalog._get_az_mappings',
        lambda *_, **__: pd.read_csv('tests/default_aws_az_mappings.csv'))
    monkeypatch.setattr('sky.backends.backend_utils.check_owner_identity',
                        lambda *_, **__: None)
    monkeypatch.setattr(
        'sky.clouds.utils.gcp_utils.list_reservations_for_instance_type_in_zone',
        lambda *_, **__: [])

    # Kubernetes mocks
    monkeypatch.setattr('sky.adaptors.kubernetes._load_config',
                        lambda *_, **__: None)
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.detect_gpu_label_formatter',
        lambda *_, **__: [kubernetes_utils.SkyPilotLabelFormatter, {}])
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.detect_accelerator_resource',
        lambda *_, **__: [True, []])
    monkeypatch.setattr('sky.provision.kubernetes.utils.check_instance_fits',
                        lambda *_, **__: [True, ''])
    monkeypatch.setattr('sky.provision.kubernetes.utils.get_spot_label',
                        lambda *_, **__: [None, None])
    monkeypatch.setattr('sky.clouds.kubernetes.kubernetes_utils.get_spot_label',
                        lambda *_, **__: [None, None])
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.is_kubeconfig_exec_auth',
        lambda *_, **__: [False, None])
    monkeypatch.setattr(
        'sky.clouds.kubernetes.Kubernetes.regions_with_offering',
        lambda *_, **__: [sky.clouds.Region('my-k8s-cluster-context')])

    # VSphere catalog mock
    monkeypatch.setattr(vsphere_catalog, '_LOCAL_CATALOG',
                        'tests/default_vsphere_vms.csv')

    # Mock quota checking for enabled clouds
    for cloud in enabled_clouds:
        if hasattr(cloud, 'check_quota_available'):
            monkeypatch.setattr(cloud, 'check_quota_available',
                                lambda *_, **__: True)

    # Environment variables
    monkeypatch.setattr(
        'sky.clouds.gcp.DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH', config_file)
    monkeypatch.setenv('OCI_CONFIG', config_file)


@pytest.fixture
def mock_job_table_no_job(monkeypatch):
    """Mock job table to return no jobs."""

    def mock_get_job_table(*_, **__):
        return 0, message_utils.encode_payload([]), ''

    monkeypatch.setattr(CloudVmRayBackend, 'run_on_head', mock_get_job_table)


@pytest.fixture
def mock_job_table_one_job(monkeypatch):
    """Mock job table to return one job."""

    def mock_get_job_table(*_, **__):
        current_time = time.time()
        job_data = {
            'job_id': '1',
            'job_name': 'test_job',
            'resources': 'test',
            'status': 'RUNNING',
            'submitted_at': current_time,
            'run_timestamp': str(current_time),
            'start_at': current_time,
            'end_at': current_time,
            'last_recovered_at': None,
            'recovery_count': 0,
            'failure_reason': '',
            'managed_job_id': '1',
            'task_id': 0,
            'task_name': 'test_task',
            'job_duration': 20,
        }
        return 0, message_utils.encode_payload([job_data]), ''

    monkeypatch.setattr(CloudVmRayBackend, 'run_on_head', mock_get_job_table)


@pytest.fixture
def mock_controller_accessible(monkeypatch):
    """Mock controller to be accessible."""

    def mock_is_controller_accessible(controller: controller_utils.Controllers,
                                      *_, **__):
        record = global_user_state.get_cluster_from_name(
            controller.value.cluster_name)
        return record['handle']  # type: ignore

    monkeypatch.setattr('sky.backends.backend_utils.is_controller_accessible',
                        mock_is_controller_accessible)


@pytest.fixture
def mock_services_no_service(monkeypatch):
    """Mock services to return no services."""

    def mock_get_services(*_, **__):
        return 0, message_utils.encode_payload(
            [], payload_type='service_status'), ''

    monkeypatch.setattr(CloudVmRayBackend, 'run_on_head', mock_get_services)


@pytest.fixture
def mock_services_one_service(monkeypatch):
    """Mock services to return one service."""

    def mock_get_services(*_, **__):
        service = {
            'name': 'test_service',
            'controller_job_id': 1,
            'uptime': 20,
            'status': serve_state.ServiceStatus.READY,
            'controller_port': 30001,
            'load_balancer_port': 30000,
            'endpoint': '4.3.2.1:30000',
            'policy': None,
            'requested_resources_str': '',
            'replica_info': [],
            'external_lb_info': [],
            'tls_encrypted': False,
        }
        return 0, message_utils.encode_payload(
            [{
                k: base64.b64encode(pickle.dumps(v)).decode('utf-8')
                for k, v in service.items()
            }],
            payload_type='service_status'), ''

    monkeypatch.setattr(CloudVmRayBackend, 'run_on_head', mock_get_services)


@pytest.fixture
def mock_queue(monkeypatch):
    """
    Mock for `sky.server.requests.queues.mp_queue.get_queue` to return an object
    with a `.put` method that stores (request_id, ignore_return_value) in a map.
    """

    # Define a mock queue object with a `.put` method
    class MockQueue:

        def __init__(self):
            # Store (request_id, ignore_return_value) pairs in a dictionary
            self.queue_map = {}

        def put(self, item):
            # Add to the map; item is assumed to be a tuple (request_id, ignore_return_value)
            request_id, ignore_return_value = item
            self.queue_map[request_id] = ignore_return_value

        def get(self, request_id):
            # Retrieve ignore_return_value for a given request_id
            return self.queue_map.get(request_id)

    # Create a MockQueue instance
    mock_queue_instance = MockQueue()

    # Mock `get_queue` to return the mock_queue_instance
    def mock_get_queue(schedule_type):
        return mock_queue_instance

    # Apply monkeypatch to replace `mp_queue.get_queue`
    monkeypatch.setattr("sky.server.requests.queues.mp_queue.get_queue",
                        mock_get_queue)

    # Return the mock_queue_instance for use in tests
    return mock_queue_instance


@pytest.fixture
def mock_redirect_log_file(monkeypatch):
    monkeypatch.setattr('sky.server.requests.executor._redirect_output',
                        lambda *_, **__: (None, None))
    monkeypatch.setattr('sky.server.requests.executor._restore_output',
                        lambda *_, **__: None)


@pytest.fixture
def mock_stream_utils(monkeypatch):
    # Patch out the original stream_utils.stream_response so it returns None
    # rather than a StreamingResponse—this ensures the call won't block.
    def _mock_empty_generator():
        yield b""

    def _mock_stream_response(*args, **kwargs):
        return fastapi.responses.StreamingResponse(
            _mock_empty_generator(),
            media_type="text/plain",
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no',
                'Transfer-Encoding': 'chunked'
            })

    monkeypatch.setattr("sky.server.stream_utils.stream_response",
                        _mock_stream_response)


@pytest.fixture
def skyignore_dir():
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create workdir
        dirs = ['remove_dir', 'dir', 'dir/subdir', 'dir/subdir/remove_dir']
        files = [
            'remove.py',
            'remove.sh',
            'remove.a',
            'keep.py',
            'remove.a',
            'dir/keep.txt',
            'dir/remove.sh',
            'dir/keep.a',
            'dir/remove.b',
            'dir/remove.a',
            'dir/subdir/keep.b',
            'dir/subdir/remove.py',
        ]
        for dir_name in dirs:
            os.makedirs(os.path.join(temp_dir, dir_name), exist_ok=True)
        for file_path in files:
            full_path = os.path.join(temp_dir, file_path)
            with open(full_path, 'w') as f:
                f.write('test content')

        # Create symlinks
        os.symlink(os.path.join(temp_dir, 'keep.py'),
                   os.path.join(temp_dir, 'ln-keep.py'))
        os.symlink(os.path.join(temp_dir, 'dir/keep.py'),
                   os.path.join(temp_dir, 'ln-dir-keep.py'))
        os.symlink(os.path.join(temp_dir, 'keep.py'),
                   os.path.join(temp_dir, 'dir/subdir/ln-keep.py'))

        # Symlinks for folders
        os.symlink(os.path.join(temp_dir, 'dir/subdir/ln-folder'),
                   os.path.join(temp_dir, 'ln-folder'))

        # Create empty directories
        os.makedirs(os.path.join(temp_dir, 'empty-folder'))

        # Create skyignore file
        skyignore_content = """
        # Current directory
        /remove.py
        /remove_dir
        /*.a
        /dir/*.b
        # Pattern match for all subdirectories
        *.sh
        remove.a
        """
        skyignore_path = os.path.join(temp_dir, constants.SKY_IGNORE_FILE)
        with open(skyignore_path, 'w', encoding='utf-8') as f:
            f.write(skyignore_content)

        yield temp_dir
