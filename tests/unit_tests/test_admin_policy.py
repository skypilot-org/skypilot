import contextlib
import copy
import importlib
import os
import subprocess
import sys
import tempfile
import time
from typing import Iterator, Optional, Tuple
from unittest import mock

import pytest
import requests

import sky
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.utils import admin_policy_utils
from sky.utils import common_utils
from sky.utils import config_utils

logger = sky_logging.init_logger(__name__)

POLICY_PATH = os.path.join(os.path.dirname(os.path.dirname(sky.__file__)),
                           'examples', 'admin_policy')
if not os.path.exists(POLICY_PATH):
    # This is used for GitHub Actions, as we copy the examples to the package.
    POLICY_PATH = os.path.join(os.path.dirname(__file__), 'examples',
                               'admin_policy')


@pytest.fixture
def add_example_policy_paths(monkeypatch):
    # Patch sys.path in fixture scope to avoid interven the global path
    test_path = copy.copy(sys.path)
    test_path.append(os.path.join(POLICY_PATH, 'example_policy'))
    with monkeypatch.context() as m:
        m.setattr(sys, 'path', test_path)
        yield


@pytest.fixture
def task():
    return sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))


def _load_task(task: sky.Task, config_path: str) -> sky.Task:
    os.environ[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = config_path
    importlib.reload(skypilot_config)
    return task


def _load_task_and_apply_policy(
    task: sky.Task,
    config_path: str,
    idle_minutes_to_autostop: Optional[int] = None,
) -> Tuple[sky.Dag, config_utils.Config]:
    os.environ[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = config_path
    importlib.reload(skypilot_config)
    return admin_policy_utils.apply(
        task,
        request_options=sky.admin_policy.RequestOptions(
            cluster_name='test',
            idle_minutes_to_autostop=idle_minutes_to_autostop,
            down=False,
            dryrun=False,
        ))


def test_use_spot_for_all_gpus_policy(add_example_policy_paths, task):
    dag, _ = _load_task_and_apply_policy(
        task, os.path.join(POLICY_PATH, 'use_spot_for_gpu.yaml'))
    assert not any(r.use_spot for r in dag.tasks[0].resources), (
        'use_spot should be False as GPU is not specified')

    task.set_resources([
        sky.Resources(cloud='gcp', accelerators={'A100': 1}),
        sky.Resources(accelerators={'L4': 1})
    ])
    dag, _ = _load_task_and_apply_policy(
        task, os.path.join(POLICY_PATH, 'use_spot_for_gpu.yaml'))
    assert all(
        r.use_spot for r in dag.tasks[0].resources), 'use_spot should be True'

    task.set_resources([
        sky.Resources(accelerators={'A100': 1}),
        sky.Resources(accelerators={'L4': 1}, use_spot=True),
        sky.Resources(cpus='2+'),
    ])
    dag, _ = _load_task_and_apply_policy(
        task, os.path.join(POLICY_PATH, 'use_spot_for_gpu.yaml'))
    for r in dag.tasks[0].resources:
        if r.accelerators:
            assert r.use_spot, 'use_spot should be True'
        else:
            assert not r.use_spot, 'use_spot should be False'


def test_add_labels_policy(add_example_policy_paths, task):
    task = _load_task(task, os.path.join(POLICY_PATH, 'add_labels.yaml'))
    with admin_policy_utils.apply_and_use_config_in_current_request(task):
        assert 'app' in skypilot_config.get_nested(
            ('kubernetes', 'custom_metadata', 'labels'),
            {}), ('label should be set')


def test_reject_all_policy(add_example_policy_paths, task):
    with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                       match='Reject all policy'):
        _load_task_and_apply_policy(
            task, os.path.join(POLICY_PATH, 'reject_all.yaml'))


def test_enforce_autostop_policy(add_example_policy_paths, task):

    def _gen_cluster_record(status: sky.ClusterStatus, autostop: int) -> dict:
        return {
            'name': 'test',
            'status': status,
            'autostop': autostop,
        }

    with mock.patch('sky.status', return_value=''):
        # Cluster does not exist
        with mock.patch('sky.get', return_value=[]):
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=10)

            with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                               match='Autostop/down must be set'):
                _load_task_and_apply_policy(task,
                                            os.path.join(
                                                POLICY_PATH,
                                                'enforce_autostop.yaml'),
                                            idle_minutes_to_autostop=None)

        # Cluster is stopped
        with mock.patch('sky.get',
                        return_value=[
                            _gen_cluster_record(sky.ClusterStatus.STOPPED, 10)
                        ]):
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=10)
            with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                               match='Autostop/down must be set'):
                _load_task_and_apply_policy(task,
                                            os.path.join(
                                                POLICY_PATH,
                                                'enforce_autostop.yaml'),
                                            idle_minutes_to_autostop=None)

        # Cluster is running but autostop is not set
        with mock.patch(
                'sky.get',
                return_value=[_gen_cluster_record(sky.ClusterStatus.UP, -1)]):
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=10)
            with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                               match='Autostop/down must be set'):
                _load_task_and_apply_policy(task,
                                            os.path.join(
                                                POLICY_PATH,
                                                'enforce_autostop.yaml'),
                                            idle_minutes_to_autostop=None)

        # Cluster is init but autostop is not set
        with mock.patch(
                'sky.get',
                return_value=[_gen_cluster_record(sky.ClusterStatus.INIT, -1)]):
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=10)
            with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                               match='Autostop/down must be set'):
                _load_task_and_apply_policy(task,
                                            os.path.join(
                                                POLICY_PATH,
                                                'enforce_autostop.yaml'),
                                            idle_minutes_to_autostop=None)

        # Cluster is running and autostop is set
        with mock.patch(
                'sky.get',
                return_value=[_gen_cluster_record(sky.ClusterStatus.UP, 10)]):
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=10)
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=None)


def test_dynamic_kubernetes_contexts_policy(add_example_policy_paths, task):
    with mock.patch(
            'sky.provision.kubernetes.utils.get_all_kube_context_names',
            return_value=['kind-skypilot', 'kind-skypilot2', 'kind-skypilot3']):
        dag, config = _load_task_and_apply_policy(
            task,
            os.path.join(POLICY_PATH,
                         'dynamic_kubernetes_contexts_update.yaml'))

        assert config.get_nested(
            ('kubernetes', 'allowed_contexts'),
            None) == ['kind-skypilot', 'kind-skypilot2'
                     ], 'Kubernetes allowed contexts should be updated'

        with admin_policy_utils.apply_and_use_config_in_current_request(dag):
            assert skypilot_config.get_nested(
                ('kubernetes', 'allowed_contexts'),
                None) == ['kind-skypilot', 'kind-skypilot2'
                         ], 'Global skypilot config should be updated'
        assert skypilot_config.get_nested(
            ('kubernetes', 'allowed_contexts'),
            None) is None, ('Global skypilot config should be restored '
                            'after request')


def test_set_max_autostop_idle_minutes_policy(add_example_policy_paths, task):
    # Test with task that has no specific autostop configuration
    dag, _ = _load_task_and_apply_policy(
        task, os.path.join(POLICY_PATH, 'set_max_autostop_idle_minutes.yaml'))
    for resource in dag.tasks[0].resources:
        assert resource.autostop_config is not None
        assert resource.autostop_config.enabled is True
        assert resource.autostop_config.idle_minutes == 10

    # Test with resources having different autostop configurations
    task.set_resources([
        # Resource with autostop over limit (should be capped at 10 minutes)
        sky.Resources(cpus='16+', autostop={
            'idle_minutes': 20,
            'down': True
        }),
    ])

    dag, _ = _load_task_and_apply_policy(
        task, os.path.join(POLICY_PATH, 'set_max_autostop_idle_minutes.yaml'))

    resources = dag.tasks[0].resources

    assert resources[0].autostop_config is not None
    assert resources[0].autostop_config.enabled is True
    assert resources[0].autostop_config.idle_minutes == 10
    assert resources[0].autostop_config.down is True  # default


def test_use_local_gcp_credentials_policy(add_example_policy_paths, task):
    """Test UseLocalGcpCredentialsPolicy for various scenarios."""
    from example_policy.client_policy import UseLocalGcpCredentialsPolicy

    test_creds_path = '/path/to/service-account.json'
    policy = UseLocalGcpCredentialsPolicy()
    with mock.patch.dict(os.environ,
                         {'GOOGLE_APPLICATION_CREDENTIALS': test_creds_path}):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        fresh_task.run = None
        user_request = sky.UserRequest(task=fresh_task,
                                       skypilot_config=None,
                                       at_client_side=True)
        mutated_request = policy.apply(user_request)

        # Check that the credentials file is mounted
        expected_mount_path = ('~/.config/gcloud/'
                               'application_default_credentials.json')
        assert expected_mount_path in mutated_request.task.file_mounts
        assert (mutated_request.task.file_mounts[expected_mount_path] ==
                test_creds_path)

        # Check that the gcloud auth command is set as the run command
        expected_auth_cmd = ('gcloud auth activate-service-account '
                             '--key-file ~/.config/gcloud/'
                             'application_default_credentials.json')
        assert mutated_request.task.run == expected_auth_cmd

    # task.run has existing command
    with mock.patch.dict(os.environ,
                         {'GOOGLE_APPLICATION_CREDENTIALS': test_creds_path}):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        original_run_cmd = 'echo "hello world"'
        fresh_task.run = original_run_cmd
        user_request = sky.UserRequest(task=fresh_task,
                                       skypilot_config=None,
                                       at_client_side=True)
        mutated_request = policy.apply(user_request)

        # Check that the gcloud auth command is prepended
        expected_auth_cmd = ('gcloud auth activate-service-account '
                             '--key-file ~/.config/gcloud/'
                             'application_default_credentials.json')
        expected_full_cmd = f'{expected_auth_cmd} && {original_run_cmd}'
        assert mutated_request.task.run == expected_full_cmd

    env_without_creds = {
        k: v
        for k, v in os.environ.items()
        if k != 'GOOGLE_APPLICATION_CREDENTIALS'
    }
    with mock.patch.dict(os.environ, env_without_creds, clear=True):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        original_run_cmd = fresh_task.run
        user_request = sky.UserRequest(task=fresh_task,
                                       skypilot_config=None,
                                       at_client_side=True)
        mutated_request = policy.apply(user_request)

        # Check that the entire gcloud directory is mounted
        assert '~/.config/gcloud' in mutated_request.task.file_mounts
        assert (mutated_request.task.file_mounts['~/.config/gcloud'] ==
                '~/.config/gcloud')

        # Check that the run command is unchanged
        assert mutated_request.task.run == original_run_cmd

    with mock.patch.dict(os.environ,
                         {'GOOGLE_APPLICATION_CREDENTIALS': test_creds_path}):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        fresh_task.set_file_mounts({'/existing/mount': '/local/path'})
        user_request = sky.UserRequest(task=fresh_task,
                                       skypilot_config=None,
                                       at_client_side=True)
        mutated_request = policy.apply(user_request)

        # Check that both existing and new mounts are present
        assert '/existing/mount' in mutated_request.task.file_mounts
        expected_mount_path = ('~/.config/gcloud/'
                               'application_default_credentials.json')
        assert expected_mount_path in mutated_request.task.file_mounts
        assert len(mutated_request.task.file_mounts) == 2

    with mock.patch.dict(os.environ,
                         {'GOOGLE_APPLICATION_CREDENTIALS': test_creds_path}):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        user_request = sky.UserRequest(task=fresh_task,
                                       skypilot_config=None,
                                       at_client_side=False)
        # Should reject the request if it is not applied at client-side
        with pytest.raises(RuntimeError,
                           match='Policy UseLocalGcpCredentialsPolicy was not '
                           'applied at client-side'):
            policy.apply(user_request)

    with mock.patch.dict(os.environ,
                         {'GOOGLE_APPLICATION_CREDENTIALS': test_creds_path}):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        user_request = sky.UserRequest(task=fresh_task,
                                       skypilot_config=None,
                                       at_client_side=True)
        mr = policy.apply(user_request)
        mutated_user_request = sky.UserRequest(
            task=mr.task,
            skypilot_config=mr.skypilot_config,
            at_client_side=False)
        # Server accept the request if it is applied at client-side
        policy.apply(mutated_user_request)

    # Test server rejects request with mismatched policy version
    with mock.patch.dict(os.environ,
                         {'GOOGLE_APPLICATION_CREDENTIALS': test_creds_path}):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        fresh_task.envs['SKYPILOT_LOCAL_GCP_CREDENTIALS_SET'] = 'v0'
        user_request = sky.UserRequest(task=fresh_task,
                                       skypilot_config=None,
                                       at_client_side=False)
        # Should reject the request due to version mismatch
        with pytest.raises(RuntimeError,
                           match='Policy UseLocalGcpCredentialsPolicy at v0 '
                           'was applied at client-side but the server '
                           'requires v1 to be applied'):
            policy.apply(user_request)


def test_restful_policy(add_example_policy_paths, task):
    """Test RestfulAdminPolicy for various scenarios."""

    # Test successful request and response
    with mock.patch('requests.post') as mock_post:
        mutated_task = sky.Task(name='test-task-mutated')
        mutated_task.set_resources([sky.Resources(cpus='4+')])
        mutated_config = sky.Config()
        mutated_request = sky.MutatedUserRequest(task=mutated_task,
                                                 skypilot_config=mutated_config)
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = mutated_request.encode()

        dag, _ = _load_task_and_apply_policy(
            task, os.path.join(POLICY_PATH, 'restful_policy.yaml'))

        mock_post.assert_called_once()

        assert dag.tasks[0].name == 'test-task-mutated'
        assert len(dag.tasks[0].resources) == 1

    # Test network error
    with mock.patch('requests.post') as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError(
            'Connection failed')

        with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                           match='Failed to call admin policy URL '
                           'http://localhost:8080: Connection failed'):
            _load_task_and_apply_policy(
                task, os.path.join(POLICY_PATH, 'restful_policy.yaml'))

    # Test HTTP error status
    with mock.patch('requests.post') as mock_post:
        mock_post.return_value.raise_for_status.side_effect = (
            requests.exceptions.HTTPError('404 Client Error'))

        with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                           match='Failed to call admin policy URL '
                           'http://localhost:8080: 404 Client Error'):
            _load_task_and_apply_policy(
                task, os.path.join(POLICY_PATH, 'restful_policy.yaml'))

    # Test policy that adds resources and modifies configuration
    with mock.patch('requests.post') as mock_post:
        mock_post.return_value.raise_for_status.return_value = None

        mutated_task = sky.Task(name='test-task')
        mutated_task.set_resources([
            sky.Resources(cpus='2+', use_spot=True),
            sky.Resources(accelerators={'V100': 1})
        ])
        mutated_task.run = 'echo "Policy modified command"'

        mutated_config = sky.Config()
        mutated_config.set_nested(('aws', 'use_spot'), True)

        mutated_request = sky.MutatedUserRequest(task=mutated_task,
                                                 skypilot_config=mutated_config)
        mock_post.return_value.json.return_value = mutated_request.encode()

        dag, config = _load_task_and_apply_policy(
            task, os.path.join(POLICY_PATH, 'restful_policy.yaml'))

        assert dag.tasks[0].run == 'echo "Policy modified command"'
        assert config.get_nested(('aws', 'use_spot'), False) is True


@contextlib.contextmanager
def _policy_server(policy: str) -> Iterator[str]:
    port = common_utils.find_free_port(start_port=8080)
    env = os.environ.copy()
    # Clear the SKYPILOT_CONFIG to avoid conflicts with the test environment
    env.pop('SKYPILOT_CONFIG', None)
    # Add the example_policy to the PYTHONPATH
    pypath = os.path.join(POLICY_PATH, 'example_policy')
    if env.get('PYTHONPATH'):
        pypath = pypath + ':' + env['PYTHONPATH']
    env['PYTHONPATH'] = pypath
    proc = subprocess.Popen(
        f'python {POLICY_PATH}/example_server/dynamic_policy_server.py --port {port} --policy {policy}',
        shell=True,
        env=env)
    start_time = time.time()
    server_ready = False
    while time.time() - start_time < 5.0:
        try:
            response = requests.get(f'http://localhost:{port}', timeout=0.1)
            if response.status_code == 200:
                server_ready = True
                break
        except (requests.exceptions.RequestException,
                requests.exceptions.ConnectionError):
            pass
        time.sleep(0.1)

    if not server_ready:
        proc.terminate()
        raise RuntimeError(
            f'Policy server on port {port} failed to start within 5 seconds')
    try:
        yield f'http://localhost:{port}'
    finally:
        proc.terminate()


def test_restful_policy_server(add_example_policy_paths, task):
    with _policy_server('DoNothingPolicy') as url, \
        tempfile.NamedTemporaryFile() as temp_file:
        temp_file.write(f'admin_policy: {url}'.encode('utf-8'))
        temp_file.flush()

        _load_task_and_apply_policy(task, temp_file.name)

    with _policy_server('AddLabelsPolicy') as url, \
        tempfile.NamedTemporaryFile() as temp_file:
        temp_file.write(f'admin_policy: {url}'.encode('utf-8'))
        temp_file.flush()

        _, config = _load_task_and_apply_policy(task, temp_file.name)
        assert 'app' in config.get_nested(
            ('kubernetes', 'custom_metadata', 'labels'),
            {}), ('label should be set')

    with _policy_server('DoNothingPolicy') as url, \
        tempfile.NamedTemporaryFile() as temp_file:
        temp_file.write(f'admin_policy: {url}/set_autostop'.encode('utf-8'))
        temp_file.flush()

        dag, _ = _load_task_and_apply_policy(task, temp_file.name)
        for r in dag.tasks[0].resources:
            assert r.autostop_config is not None
            assert r.autostop_config.enabled is True
            assert r.autostop_config.idle_minutes == 10

    with _policy_server('RejectAllPolicy') as url, \
        tempfile.NamedTemporaryFile() as temp_file:
        temp_file.write(f'admin_policy: {url}/'.encode('utf-8'))
        temp_file.flush()

        # Verify the exception from REST policy server is exposed to user.
        with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                           match='Reject all policy'):
            _load_task_and_apply_policy(task, temp_file.name)
