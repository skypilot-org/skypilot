import fcntl
import os
import subprocess
import tempfile
import time
from typing import List

import docker
import pytest

import sky
from sky import sky_logging

# Initialize logger at the top level
logger = sky_logging.init_logger(__name__)

# We need to import all the mock functions here, so that the smoke
# tests can access them.
from common_test_fixtures import aws_config_region
from common_test_fixtures import enable_all_clouds
from common_test_fixtures import mock_client_requests
from common_test_fixtures import mock_controller_accessible
from common_test_fixtures import mock_job_table_no_job
from common_test_fixtures import mock_job_table_one_job
from common_test_fixtures import mock_queue
from common_test_fixtures import mock_redirect_log_file
from common_test_fixtures import mock_services_no_service
from common_test_fixtures import mock_services_one_service
from common_test_fixtures import mock_stream_utils
from common_test_fixtures import skyignore_dir

from sky.server import common as server_common

# Usage: use
#   @pytest.mark.slow
# to mark a test as slow and to skip by default.
# https://docs.pytest.org/en/latest/example/simple.html#control-skipping-of-tests-according-to-command-line-option

# By default, only run generic tests and cloud-specific tests for AWS and Azure,
# due to the cloud credit limit for the development account.
#
# A "generic test" tests a generic functionality (e.g., autostop) that
# should work on any cloud we support. The cloud used for such a test
# is controlled by `--generic-cloud` (typically you do not need to set it).
#
# To only run tests for a specific cloud (as well as generic tests), use
# --aws, --gcp, --azure, or --lambda.
#
# To only run tests for managed jobs (without generic tests), use
# --managed-jobs.
all_clouds_in_smoke_tests = [
    'aws', 'gcp', 'azure', 'lambda', 'cloudflare', 'ibm', 'scp', 'oci', 'do',
    'kubernetes', 'vsphere', 'cudo', 'fluidstack', 'paperspace', 'runpod',
    'vast', 'nebius'
]
default_clouds_to_run = ['aws', 'azure']

# Translate cloud name to pytest keyword. We need this because
# @pytest.mark.lambda is not allowed, so we use @pytest.mark.lambda_cloud
# instead.
cloud_to_pytest_keyword = {
    'aws': 'aws',
    'gcp': 'gcp',
    'azure': 'azure',
    'lambda': 'lambda_cloud',
    'cloudflare': 'cloudflare',
    'ibm': 'ibm',
    'scp': 'scp',
    'oci': 'oci',
    'kubernetes': 'kubernetes',
    'vsphere': 'vsphere',
    'runpod': 'runpod',
    'fluidstack': 'fluidstack',
    'cudo': 'cudo',
    'paperspace': 'paperspace',
    'do': 'do',
    'vast': 'vast',
    'runpod': 'runpod',
    'nebius': 'nebius'
}


def pytest_addoption(parser):
    # tests marked as `slow` will be skipped by default, use --runslow to run
    parser.addoption('--runslow',
                     action='store_true',
                     default=False,
                     help='run slow tests.')
    for cloud in all_clouds_in_smoke_tests:
        parser.addoption(f'--{cloud}',
                         action='store_true',
                         default=False,
                         help=f'Only run {cloud.upper()} tests.')
    parser.addoption('--managed-jobs',
                     action='store_true',
                     default=False,
                     help='Only run tests for managed jobs.')
    parser.addoption('--serve',
                     action='store_true',
                     default=False,
                     help='Only run tests for sky serve.')
    parser.addoption('--tpu',
                     action='store_true',
                     default=False,
                     help='Only run tests for TPU.')
    parser.addoption(
        '--generic-cloud',
        type=str,
        choices=all_clouds_in_smoke_tests,
        help='Cloud to use for generic tests. If the generic cloud is '
        'not within the clouds to be run, it will be reset to the first '
        'cloud in the list of the clouds to be run.')

    parser.addoption('--terminate-on-failure',
                     dest='terminate_on_failure',
                     action='store_true',
                     default=True,
                     help='Terminate test VMs on failure.')
    parser.addoption('--no-terminate-on-failure',
                     dest='terminate_on_failure',
                     action='store_false',
                     help='Do not terminate test VMs on failure.')
    parser.addoption(
        '--remote-server',
        action='store_true',
        default=False,
        help='Run tests against a remote server in Docker container.')


def pytest_configure(config):
    config.addinivalue_line('markers', 'slow: mark test as slow to run')
    config.addinivalue_line('markers',
                            'local: mark test to run only on local API server')
    for cloud in all_clouds_in_smoke_tests:
        cloud_keyword = cloud_to_pytest_keyword[cloud]
        config.addinivalue_line(
            'markers', f'{cloud_keyword}: mark test as {cloud} specific')

    pytest.terminate_on_failure = config.getoption('--terminate-on-failure')

    if config.getoption('--remote-server'):
        # Set xdist to use only 1 worker when remote-server is enabled
        config.option.numprocesses = 1


def _get_cloud_to_run(config) -> List[str]:
    cloud_to_run = []

    for cloud in all_clouds_in_smoke_tests:
        if config.getoption(f'--{cloud}'):
            if cloud == 'cloudflare':
                cloud_to_run.append(default_clouds_to_run[0])
            else:
                cloud_to_run.append(cloud)

    generic_cloud_option = config.getoption('--generic-cloud')
    if generic_cloud_option is not None and generic_cloud_option not in cloud_to_run:
        cloud_to_run.append(generic_cloud_option)

    if len(cloud_to_run) == 0:
        cloud_to_run = default_clouds_to_run

    return cloud_to_run


def pytest_collection_modifyitems(config, items):
    skip_marks = {}
    skip_marks['slow'] = pytest.mark.skip(reason='need --runslow option to run')
    skip_marks['managed_jobs'] = pytest.mark.skip(
        reason='skipped, because --managed-jobs option is set')
    skip_marks['serve'] = pytest.mark.skip(
        reason='skipped, because --serve option is set')
    skip_marks['tpu'] = pytest.mark.skip(
        reason='skipped, because --tpu option is set')
    skip_marks['local'] = pytest.mark.skip(
        reason='test requires local API server')
    for cloud in all_clouds_in_smoke_tests:
        skip_marks[cloud] = pytest.mark.skip(
            reason=f'tests for {cloud} is skipped, try setting --{cloud}')

    cloud_to_run = _get_cloud_to_run(config)
    generic_cloud = _generic_cloud(config)
    generic_cloud_keyword = cloud_to_pytest_keyword[generic_cloud]

    for item in items:
        if 'slow' in item.keywords and not config.getoption('--runslow'):
            item.add_marker(skip_marks['slow'])
        if 'local' in item.keywords and not server_common.is_api_server_local():
            item.add_marker(skip_marks['local'])
        if _is_generic_test(
                item) and f'no_{generic_cloud_keyword}' in item.keywords:
            item.add_marker(skip_marks[generic_cloud])
        for cloud in all_clouds_in_smoke_tests:
            cloud_keyword = cloud_to_pytest_keyword[cloud]
            if (cloud_keyword in item.keywords and cloud not in cloud_to_run):
                # Need to check both conditions as the first default cloud is
                # added to cloud_to_run when tested for cloudflare
                if config.getoption('--cloudflare') and cloud == 'cloudflare':
                    continue
                item.add_marker(skip_marks[cloud])

        if (not 'managed_jobs'
                in item.keywords) and config.getoption('--managed-jobs'):
            item.add_marker(skip_marks['managed_jobs'])
        if (not 'tpu' in item.keywords) and config.getoption('--tpu'):
            item.add_marker(skip_marks['tpu'])
        if (not 'serve' in item.keywords) and config.getoption('--serve'):
            item.add_marker(skip_marks['serve'])

    # Check if tests need to be run serially for Kubernetes and Lambda Cloud
    # We run Lambda Cloud tests serially because Lambda Cloud rate limits its
    # launch API to one launch every 10 seconds.
    # We run Kubernetes tests serially because the Kubernetes cluster may have
    # limited resources (e.g., just 8 cpus).
    serial_mark = pytest.mark.xdist_group(
        name=f'serial_{generic_cloud_keyword}')
    # Handle generic tests
    if generic_cloud in ['lambda']:
        for item in items:
            if (_is_generic_test(item) and
                    f'no_{generic_cloud_keyword}' not in item.keywords):
                item.add_marker(serial_mark)
                # Adding the serial mark does not update the item.nodeid,
                # but item.nodeid is important for pytest.xdist_group, e.g.
                #   https://github.com/pytest-dev/pytest-xdist/blob/master/src/xdist/scheduler/loadgroup.py
                # This is a hack to update item.nodeid
                item._nodeid = f'{item.nodeid}@serial_{generic_cloud_keyword}'
    # Handle generic cloud specific tests
    for item in items:
        if generic_cloud in ['lambda', 'kubernetes']:
            if generic_cloud_keyword in item.keywords:
                item.add_marker(serial_mark)
                item._nodeid = f'{item.nodeid}@serial_{generic_cloud_keyword}'  # See comment on item.nodeid above

    if config.option.collectonly:
        for item in items:
            full_name = item.nodeid
            marks = [mark.name for mark in item.iter_markers()]
            logger.info(f"Collected {full_name} with marks: {marks}")


def _is_generic_test(item) -> bool:
    for cloud in all_clouds_in_smoke_tests:
        if cloud_to_pytest_keyword[cloud] in item.keywords:
            return False
    return True


def _generic_cloud(config) -> str:
    generic_cloud_option = config.getoption('--generic-cloud')
    if generic_cloud_option is not None:
        return generic_cloud_option
    return _get_cloud_to_run(config)[0]


@pytest.fixture
def generic_cloud(request) -> str:
    return _generic_cloud(request.config)


@pytest.fixture(scope='session', autouse=True)
def setup_docker_container(request):
    """Setup Docker container for remote server testing if --remote-server is specified."""
    if not request.config.getoption('--remote-server'):
        yield
        return

    # Docker image and container names
    image_name = 'sky-server-image'
    container_name = 'sky-server-test'
    dockerfile_path = 'tests/Dockerfile_test'
    default_user = os.environ.get('USER', 'buildkite')

    # Create a lockfile in a temporary directory that all processes can access
    lock_file = os.path.join(tempfile.gettempdir(), 'sky_docker_setup.lock')

    # Flag to track if this worker created the container
    container_created_by_this_worker = False
    client = None

    with open(lock_file, 'w') as f:
        try:
            # Try to acquire an exclusive lock
            fcntl.flock(f, fcntl.LOCK_EX)

            # Initialize Docker client
            client = docker.from_env()

            # Check if container is already running (another worker might have started it)
            try:
                container = client.containers.get(container_name)
                logger.info(f'Container {container_name} is already running')
                fcntl.flock(f, fcntl.LOCK_UN)
                yield container
                return
            except docker.errors.NotFound:
                pass

            # Check if image exists, build if not
            image_exists = False
            try:
                client.images.get(image_name)
                image_exists = True
                logger.info(f'Docker image {image_name} already exists')
            except docker.errors.ImageNotFound:
                logger.info(f'Docker image {image_name} not found, building...')
                try:
                    # Create a temporary directory for Docker config to bypass credential stores
                    temp_docker_config = os.path.join(tempfile.gettempdir(),
                                                      'temp_docker_config')
                    os.makedirs(temp_docker_config, exist_ok=True)

                    # Save original Docker config
                    original_docker_config = os.environ.get('DOCKER_CONFIG')

                    # Set temporary Docker config
                    os.environ['DOCKER_CONFIG'] = temp_docker_config

                    # Create a minimal config.json without credential stores
                    config_json = os.path.join(temp_docker_config,
                                               'config.json')
                    with open(config_json, 'w') as f:
                        f.write('{"credsStore": ""}')

                    try:
                        client.images.build(
                            path='.',
                            dockerfile=dockerfile_path,
                            tag=image_name,
                            buildargs={'USERNAME': default_user})
                        image_exists = True
                        logger.info(
                            f'Successfully built Docker image {image_name}')
                    finally:
                        # Restore original Docker config
                        if original_docker_config:
                            os.environ['DOCKER_CONFIG'] = original_docker_config
                        else:
                            os.environ.pop('DOCKER_CONFIG', None)

                except Exception as e:
                    logger.error(f'Failed to build Docker image: {e}')
                    raise

            if not image_exists:
                raise Exception(
                    f'Failed to ensure Docker image {image_name} exists')

            # Remove existing container if it exists
            try:
                container = client.containers.get(container_name)
                container.remove(force=True)
            except docker.errors.NotFound:
                pass

            # Start new container
            logger.info(f'Starting Docker container {container_name}...')
            workspace_path = os.path.abspath(
                os.path.dirname(os.path.dirname(__file__)))
            container = client.containers.run(
                image_name,
                name=container_name,
                detach=True,
                volumes={
                    workspace_path: '/skypilot',
                    os.path.expanduser('~/.sky'): f'/home/{os.environ.get("USER", default_user)}/.sky',
                    os.path.expanduser('~/.aws'): f'/home/{os.environ.get("USER", default_user)}/.aws',
                    os.path.expanduser('~/.azure'): f'/home/{os.environ.get("USER", default_user)}/.azure',
                    os.path.expanduser('~/.config/gcloud'): f'/home/{os.environ.get("USER", default_user)}/.config/gcloud',
                },
                environment={
                    'USERNAME': os.environ.get('USER', default_user),
                    'SKYPILOT_DISABLE_USAGE_COLLECTION': '1'
                },
                platform='linux/amd64')

            # Mark that this worker created the container
            container_created_by_this_worker = True

            # Wait for container to be ready
            logger.info('Waiting for container to be ready...')
            max_retries = 30
            retry_count = 0
            while retry_count < max_retries:
                try:
                    exit_code = container.exec_run(
                        'pgrep -f "sky api"').exit_code
                    if exit_code == 0:
                        logger.info('Container is ready!')
                        break
                    retry_count += 1
                    time.sleep(1)
                except:
                    retry_count += 1
                    time.sleep(1)
            else:
                raise Exception('Container failed to start properly')

            # Release the lock after setup is complete
            fcntl.flock(f, fcntl.LOCK_UN)
            yield container

        except Exception as e:
            logger.error(f'Error in Docker setup: {e}')
            # Make sure to release the lock even if an error occurs
            fcntl.flock(f, fcntl.LOCK_UN)
            raise
        finally:
            # Cleanup after tests - this will run only for the process that created the container
            if container_created_by_this_worker and client is not None:
                logger.info(f'Cleaning up Docker container {container_name}...')
                try:
                    container = client.containers.get(container_name)
                    container.remove(force=True)
                except Exception as e:
                    logger.error(f'Error removing container: {e}')
