import importlib
import os
import sys
from typing import Optional, Tuple
from unittest import mock

import pytest

import sky
from sky import exceptions
from sky import sky_logging
from sky import skypilot_config
from sky.utils import admin_policy_utils
from sky.utils import config_utils

logger = sky_logging.init_logger(__name__)

POLICY_PATH = os.path.join(os.path.dirname(os.path.dirname(sky.__file__)),
                           'examples', 'admin_policy')
if not os.path.exists(POLICY_PATH):
    # This is used for GitHub Actions, as we copy the examples to the package.
    POLICY_PATH = os.path.join(os.path.dirname(__file__), 'examples',
                               'admin_policy')


@pytest.fixture
def add_example_policy_paths():
    # Add to path to be able to import
    sys.path.append(os.path.join(POLICY_PATH, 'example_policy'))


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
    _, config = admin_policy_utils.apply(task)
    with skypilot_config.replace_skypilot_config(config):
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

    # Cluster does not exist
    with mock.patch('sky.status', return_value=[]):
        _load_task_and_apply_policy(task,
                                    os.path.join(POLICY_PATH,
                                                 'enforce_autostop.yaml'),
                                    idle_minutes_to_autostop=10)

        with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                           match='Autostop/down must be set'):
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=None)

    # Cluster is stopped
    with mock.patch(
            'sky.status',
            return_value=[_gen_cluster_record(sky.ClusterStatus.STOPPED, 10)]):
        _load_task_and_apply_policy(task,
                                    os.path.join(POLICY_PATH,
                                                 'enforce_autostop.yaml'),
                                    idle_minutes_to_autostop=10)
        with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                           match='Autostop/down must be set'):
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=None)

    # Cluster is running but autostop is not set
    with mock.patch(
            'sky.status',
            return_value=[_gen_cluster_record(sky.ClusterStatus.UP, -1)]):
        _load_task_and_apply_policy(task,
                                    os.path.join(POLICY_PATH,
                                                 'enforce_autostop.yaml'),
                                    idle_minutes_to_autostop=10)
        with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                           match='Autostop/down must be set'):
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=None)

    # Cluster is init but autostop is not set
    with mock.patch(
            'sky.status',
            return_value=[_gen_cluster_record(sky.ClusterStatus.INIT, -1)]):
        _load_task_and_apply_policy(task,
                                    os.path.join(POLICY_PATH,
                                                 'enforce_autostop.yaml'),
                                    idle_minutes_to_autostop=10)
        with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                           match='Autostop/down must be set'):
            _load_task_and_apply_policy(task,
                                        os.path.join(POLICY_PATH,
                                                     'enforce_autostop.yaml'),
                                        idle_minutes_to_autostop=None)

    # Cluster is running and autostop is set
    with mock.patch(
            'sky.status',
            return_value=[_gen_cluster_record(sky.ClusterStatus.UP, 10)]):
        _load_task_and_apply_policy(task,
                                    os.path.join(POLICY_PATH,
                                                 'enforce_autostop.yaml'),
                                    idle_minutes_to_autostop=10)
        _load_task_and_apply_policy(task,
                                    os.path.join(POLICY_PATH,
                                                 'enforce_autostop.yaml'),
                                    idle_minutes_to_autostop=None)


@mock.patch('sky.provision.kubernetes.utils.get_all_kube_context_names',
            return_value=['kind-skypilot', 'kind-skypilot2', 'kind-skypilot3'])
def test_dynamic_kubernetes_contexts_policy(add_example_policy_paths, task):
    dag, config = _load_task_and_apply_policy(
        task,
        os.path.join(POLICY_PATH, 'dynamic_kubernetes_contexts_update.yaml'))

    assert config.get_nested(
        ('kubernetes', 'allowed_contexts'),
        None) == ['kind-skypilot', 'kind-skypilot2'
                 ], 'Kubernetes allowed contexts should be updated'

    _, config = admin_policy_utils.apply(dag)
    with skypilot_config.replace_skypilot_config(config):
        assert skypilot_config.get_nested(
            ('kubernetes', 'allowed_contexts'),
            None) == ['kind-skypilot', 'kind-skypilot2'
                     ], 'Global skypilot config should be updated'
    assert skypilot_config.get_nested(
        ('kubernetes', 'allowed_contexts'),
        None) is None, 'Global skypilot config should be restored after request'


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
    with mock.patch.dict(os.environ,
                         {'GOOGLE_APPLICATION_CREDENTIALS': test_creds_path}):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        fresh_task.run = None
        user_request = sky.UserRequest(task=fresh_task,
                                       skypilot_config=None,
                                       at_client_side=True)
        mutated_request = UseLocalGcpCredentialsPolicy.validate_and_mutate(
            user_request)

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
        mutated_request = UseLocalGcpCredentialsPolicy.validate_and_mutate(
            user_request)

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
        mutated_request = UseLocalGcpCredentialsPolicy.validate_and_mutate(
            user_request)

        # Check that the entire gcloud directory is mounted
        assert '~/.config/gcloud' in mutated_request.task.file_mounts
        assert (mutated_request.task.file_mounts['~/.config/gcloud'] ==
                '~/.config/gcloud')

        # Check that the run command is unchanged
        assert mutated_request.task.run == original_run_cmd

    with mock.patch.dict(os.environ,
                         {'GOOGLE_APPLICATION_CREDENTIALS': test_creds_path}):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        fresh_task.file_mounts = {'/existing/mount': '/local/path'}
        user_request = sky.UserRequest(task=fresh_task,
                                       skypilot_config=None,
                                       at_client_side=True)
        mutated_request = UseLocalGcpCredentialsPolicy.validate_and_mutate(
            user_request)

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
        mutated_request = UseLocalGcpCredentialsPolicy.validate_and_mutate(
            user_request)
        # Should skip the policy at client-side
        assert mutated_request.task.file_mounts is None
