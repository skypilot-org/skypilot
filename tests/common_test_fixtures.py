import base64
import collections
import os
import pickle
import tempfile
import time
import unittest
import uuid

import boto3
import fastapi
from fastapi import testclient
import pandas as pd
import pytest
import requests

import sky
from sky import global_user_state
from sky import sky_logging
from sky.backends.cloud_vm_ray_backend import CloudVmRayBackend
from sky.catalog import vsphere_catalog
from sky.provision import common as provision_common
from sky.provision.aws import config as aws_config
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server import rest
from sky.server import versions
from sky.server.requests import executor
from sky.server.requests import requests as api_requests
from sky.server.server import app
from sky.skylet import constants
from sky.utils import annotations
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
    original_request = requests.request

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
        mock_func = client.request
        original_func = original_request
        if server_common.get_server_url() in url:
            logger.info(f'Mocking {method} request to {url} through TestClient')
            path = url.replace(server_common.get_server_url(), "")
            # Remove stream parameter as it's not supported by TestClient
            stream = kwargs.pop('stream', False)
            # Extract and format query parameters
            if 'params' in kwargs and kwargs['params'] is not None:
                kwargs['params'] = {
                    k: v for k, v in kwargs['params'].items() if v is not None
                }
            response = mock_func(method, path, *args, **kwargs)
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

            response.headers[server_constants.API_VERSION_HEADER] = str(
                server_constants.API_VERSION)
            response.headers[server_constants.VERSION_HEADER] = \
                versions.get_local_readable_version()
            return response
        else:
            return original_func(url, *args, **kwargs)

    # pylint: disable=protected-access
    monkeypatch.setattr(rest._session, "request", mock_http_request)


# Define helper functions at module level for pickleability
def get_cached_enabled_clouds_mock(enabled_clouds, *_, **__):
    return enabled_clouds


def dummy_function(*_, **__):
    return None


def get_az_mappings(*_, **__):
    return pd.read_csv('tests/default_aws_az_mappings.csv')


def list_empty_reservations(*_, **__):
    return []


def get_kubernetes_label_formatter(*_, **__):
    return [kubernetes_utils.SkyPilotLabelFormatter, {}]


def detect_accelerator_resource_mock(*_, **__):
    return [True, []]


def check_instance_fits_mock(*_, **__):
    return [True, '']


def get_spot_label_mock(*_, **__):
    return [None, None]


def is_kubeconfig_exec_auth_mock(*_, **__):
    return [False, None]


def regions_with_offering_mock(*_, **__):
    return [sky.clouds.Region('my-k8s-cluster-context')]


def check_quota_available_mock(*_, **__):
    return True


def mock_redirect_output(*_, **__):
    return (None, None)


def mock_restore_output(*_, **__):
    return None


@pytest.fixture
def enable_all_clouds(monkeypatch, request, mock_client_requests):
    """Create mock context managers for cloud configurations."""
    enabled_clouds = request.param if hasattr(request, 'param') else None
    if enabled_clouds is None:
        enabled_clouds = list(registry.CLOUD_REGISTRY.values())

    config_file = tempfile.NamedTemporaryFile(prefix='tmp_config_default',
                                              delete=False).name

    # Use a function that takes enabled_clouds as an argument
    def get_clouds_factory(*args, **kwargs):
        return get_cached_enabled_clouds_mock(enabled_clouds, *args, **kwargs)

    # Mock all the functions
    monkeypatch.setattr('sky.check.get_cached_enabled_clouds_or_refresh',
                        get_clouds_factory)
    monkeypatch.setattr('sky.check.check_capability', dummy_function)
    monkeypatch.setattr('sky.catalog.aws_catalog._get_az_mappings',
                        get_az_mappings)
    monkeypatch.setattr('sky.backends.backend_utils.check_owner_identity',
                        dummy_function)
    monkeypatch.setattr(
        'sky.clouds.utils.gcp_utils.list_reservations_for_instance_type_in_zone',
        list_empty_reservations)

    # Kubernetes mocks
    monkeypatch.setattr('sky.adaptors.kubernetes._load_config', dummy_function)
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.detect_gpu_label_formatter',
        get_kubernetes_label_formatter)
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.detect_accelerator_resource',
        detect_accelerator_resource_mock)
    monkeypatch.setattr('sky.provision.kubernetes.utils.check_instance_fits',
                        check_instance_fits_mock)
    monkeypatch.setattr('sky.provision.kubernetes.utils.get_spot_label',
                        get_spot_label_mock)
    monkeypatch.setattr('sky.clouds.kubernetes.kubernetes_utils.get_spot_label',
                        get_spot_label_mock)
    monkeypatch.setattr(
        'sky.provision.kubernetes.utils.is_kubeconfig_exec_auth',
        is_kubeconfig_exec_auth_mock)
    monkeypatch.setattr(
        'sky.clouds.kubernetes.Kubernetes.regions_with_offering',
        regions_with_offering_mock)

    # VSphere catalog mock
    monkeypatch.setattr(vsphere_catalog, '_LOCAL_CATALOG',
                        'tests/default_vsphere_vms.csv')

    # Mock quota checking for enabled clouds
    for cloud in enabled_clouds:
        if hasattr(cloud, 'check_quota_available'):
            monkeypatch.setattr(cloud, 'check_quota_available',
                                check_quota_available_mock)

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

    def mock_get_job_table(*args, **__):
        # The third argument is the cmd.
        cmd = args[2]
        # Return no pools for the job table.
        if 'get_service_status_encoded' in cmd:
            return 0, message_utils.encode_payload([]), ''
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
            'workspace': 'default',
            'task_id': 0,
            'task_name': 'test_task',
            'job_duration': 20,
            'priority': constants.DEFAULT_PRIORITY,
            'pool': None,
            'current_cluster_name': None,
            'job_id_on_pool_cluster': None,
            'pool_hash': None,
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
def mock_services_no_service_grpc(monkeypatch):
    """Mock services to return no services."""

    def mock_get_services_grpc(*_, **__):
        return serve_utils.GetServiceStatusResponseConverter.to_proto([])

    monkeypatch.setattr('sky.backends.backend_utils.invoke_skylet_with_retries',
                        mock_get_services_grpc)


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
def mock_services_one_service_grpc(monkeypatch):
    """Mock services to return one services."""

    def mock_get_services_grpc(*_, **__):
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
            'tls_encrypted': False,
        }
        return serve_utils.GetServiceStatusResponseConverter.to_proto([{
            k: base64.b64encode(pickle.dumps(v)).decode('utf-8')
            for k, v in service.items()
        }])

    monkeypatch.setattr('sky.backends.backend_utils.invoke_skylet_with_retries',
                        mock_get_services_grpc)


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
            # Add to the map; item is assumed to be a tuple (request_id, ignore_return_value, retryable)
            request_id, ignore_return_value, _ = item
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
                        mock_redirect_output)
    monkeypatch.setattr('sky.server.requests.executor._restore_output',
                        mock_restore_output)


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
def mock_aws_backend(monkeypatch):
    """Mock AWS backend for basic AWS testing operations."""
    # Create a Subnet class to match what SkyPilot expects
    Subnet = collections.namedtuple(
        'Subnet',
        ['subnet_id', 'vpc_id', 'availability_zone', 'map_public_ip_on_launch'])

    # Mock subnet and VPC discovery
    def mock_get_subnet_and_vpc_id(*args, **kwargs):
        # Get the region from kwargs
        region = kwargs.get('region', 'us-east-1')

        # Create a subnet in the requested region
        ec2 = boto3.resource('ec2', region_name=region)

        # Create VPC and subnet in the requested region
        vpc = ec2.create_vpc(CidrBlock='10.0.0.0/16')
        subnet = ec2.create_subnet(VpcId=vpc.id,
                                   CidrBlock='10.0.0.0/24',
                                   AvailabilityZone=f"{region}a")

        # Configure subnet to map public IPs
        ec2.meta.client.modify_subnet_attribute(
            SubnetId=subnet.id, MapPublicIpOnLaunch={'Value': True})

        # Store subnet object
        subnet_obj = Subnet(subnet_id=subnet.id,
                            vpc_id=vpc.id,
                            availability_zone=f"{region}a",
                            map_public_ip_on_launch=True)

        return ([subnet_obj], vpc.id)

    # Mock security groups
    def mock_get_or_create_vpc_security_group(*args, **kwargs):
        # Return a mock security group
        return unittest.mock.Mock(id="sg-12345678", group_name="test-sg")

    # Mock IAM role
    def mock_configure_iam_role(*args, **kwargs):
        return {'Name': 'skypilot-test-role'}

    def mock_wait_instances(region, cluster_name_on_cloud, state):
        # Always return successfully without waiting
        return

    def mock_post_provision_runtime_setup(cloud_name, cluster_name,
                                          cluster_yaml, provision_record,
                                          custom_resource, log_dir):
        # Get region from the provision record
        region = provision_record.region

        # Create a head instance
        head_instance_id = f'i-{uuid.uuid4().hex[:8]}'

        # Create instance info for the head
        head_instance = provision_common.InstanceInfo(
            instance_id=head_instance_id,
            internal_ip='10.0.0.1',
            external_ip='192.168.1.1',
            tags={
                'Name': cluster_name.name_on_cloud,
                'ray-cluster-name': cluster_name.name_on_cloud,
                'ray-node-type': 'head'
            })

        # Create ClusterInfo
        instances = {head_instance_id: [head_instance]}
        cluster_info = provision_common.ClusterInfo(
            instances=instances,
            head_instance_id=head_instance_id,
            provider_name='aws',
            provider_config={
                'region': region,
                'use_internal_ips': False
            },
            ssh_user='ubuntu')

        return cluster_info

    def mock_execute(self, handle, task, detach_run, dryrun=False):
        # Return a fake job ID without attempting to SSH
        return 1234

    # Apply our mocks to the monkeypatch
    monkeypatch.setattr(aws_config, '_get_subnet_and_vpc_id',
                        mock_get_subnet_and_vpc_id)
    monkeypatch.setattr(aws_config, '_get_or_create_vpc_security_group',
                        mock_get_or_create_vpc_security_group)
    monkeypatch.setattr(aws_config, '_configure_iam_role',
                        mock_configure_iam_role)
    monkeypatch.setattr(sky.provision.aws, 'wait_instances',
                        mock_wait_instances)
    # Add mock for post_provision_runtime_setup
    monkeypatch.setattr(sky.provision.provisioner,
                        'post_provision_runtime_setup',
                        mock_post_provision_runtime_setup)
    # Add mock for _execute
    monkeypatch.setattr(sky.backends.cloud_vm_ray_backend.CloudVmRayBackend,
                        '_execute', mock_execute)


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


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset global state before each test."""
    annotations.is_on_api_server = True
    yield
