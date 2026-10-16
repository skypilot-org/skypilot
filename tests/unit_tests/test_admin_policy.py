import contextlib
import copy
import importlib
import io
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from typing import Iterator, Optional, Tuple
from unittest import mock

import pytest
import requests

import sky
from sky import backends
from sky import exceptions
from sky import models
from sky import sky_logging
from sky import skypilot_config
from sky.backends import backend_utils
from sky.jobs.server import core as jobs_core
from sky.server import versions
from sky.server.requests import request_names
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


@pytest.fixture
def add_volumes_policy_cls(add_example_policy_paths):
    """Fixture to provide AddVolumesPolicy class after path setup."""
    # Import here because example_policy is not on sys.path until
    # add_example_policy_paths fixture adds it.
    from example_policy.skypilot_policy import AddVolumesPolicy
    return AddVolumesPolicy


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
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
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
    with admin_policy_utils.apply_and_use_config_in_current_request(
            task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH):
        assert 'app' in skypilot_config.get_nested(
            ('kubernetes', 'custom_metadata', 'labels'),
            {}), ('label should be set')


def test_add_labels_conditional_policy(add_example_policy_paths, task):
    task = _load_task(task,
                      os.path.join(POLICY_PATH, 'add_labels_conditional.yaml'))
    with admin_policy_utils.apply_and_use_config_in_current_request(
            task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH):
        assert 'app' in skypilot_config.get_nested(
            ('kubernetes', 'custom_metadata', 'labels'),
            {}), ('label should be set')
    with admin_policy_utils.apply_and_use_config_in_current_request(
            task, request_name=request_names.AdminPolicyRequestName.VALIDATE):
        assert 'app' not in skypilot_config.get_nested(
            ('kubernetes', 'custom_metadata', 'labels'),
            {}), ('label should not be set for validate request')


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

        with admin_policy_utils.apply_and_use_config_in_current_request(
                dag,
                request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH
        ):
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

    resources = list(dag.tasks[0].resources)

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
        user_request = sky.UserRequest(
            task=fresh_task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
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
        user_request = sky.UserRequest(
            task=fresh_task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
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
        user_request = sky.UserRequest(
            task=fresh_task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
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
        fresh_task.file_mounts = {'/existing/mount': '/local/path'}
        user_request = sky.UserRequest(
            task=fresh_task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
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
        user_request = sky.UserRequest(
            task=fresh_task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
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
        user_request = sky.UserRequest(
            task=fresh_task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
            skypilot_config=None,
            at_client_side=True)
        mr = policy.apply(user_request)
        mutated_user_request = sky.UserRequest(
            task=mr.task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
            skypilot_config=mr.skypilot_config,
            at_client_side=False)
        # Server accept the request if it is applied at client-side
        policy.apply(mutated_user_request)

    # Test server rejects request with mismatched policy version
    with mock.patch.dict(os.environ,
                         {'GOOGLE_APPLICATION_CREDENTIALS': test_creds_path}):
        fresh_task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
        fresh_task.envs['SKYPILOT_LOCAL_GCP_CREDENTIALS_SET'] = 'v0'
        user_request = sky.UserRequest(
            task=fresh_task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
            skypilot_config=None,
            at_client_side=False)
        # Should reject the request due to version mismatch
        with pytest.raises(RuntimeError,
                           match='Policy UseLocalGcpCredentialsPolicy at v0 '
                           'was applied at client-side but the server '
                           'requires v1 to be applied'):
            policy.apply(user_request)


def test_user_request_encode_decode(task):
    with mock.patch('sky.utils.common_utils.get_current_user',
                    return_value=models.User(id='123', name='test')):
        user_request = sky.UserRequest(
            task=task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
            skypilot_config=sky.Config(),
            at_client_side=False,
            user=models.User(id='123', name='test'))
        encoded_request = user_request.encode()
        decoded_request = sky.UserRequest.decode(encoded_request)
        assert repr(decoded_request.task) == repr(task)
        assert decoded_request.skypilot_config == sky.Config()
        assert decoded_request.at_client_side == False
        assert decoded_request.user == models.User(id='123', name='test')
        assert decoded_request.request_name == request_names.AdminPolicyRequestName.CLUSTER_LAUNCH


def test_user_request_encode_decode_with_client_version(task):
    """Test that client version fields are correctly serialized and
    deserialized."""
    with mock.patch('sky.utils.common_utils.get_current_user',
                    return_value=models.User(id='123', name='test')):
        user_request = sky.UserRequest(
            task=task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
            skypilot_config=sky.Config(),
            at_client_side=False,
            user=models.User(id='123', name='test'),
            client_api_version=31,
            client_version='1.0.0')
        encoded_request = user_request.encode()
        decoded_request = sky.UserRequest.decode(encoded_request)
        assert decoded_request.client_api_version == 31
        assert decoded_request.client_version == '1.0.0'

        # Test with None values (simulating client-side or old clients)
        user_request_no_version = sky.UserRequest(
            task=task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
            skypilot_config=sky.Config(),
            at_client_side=True,
            user=None,
            client_api_version=None,
            client_version=None)
        encoded_request = user_request_no_version.encode()
        decoded_request = sky.UserRequest.decode(encoded_request)
        assert decoded_request.client_api_version is None
        assert decoded_request.client_version is None


def test_apply_passes_client_version_from_contextvars(add_example_policy_paths,
                                                      task):
    """Test that admin_policy_utils.apply() passes client version info from
    contextvars to the UserRequest."""
    # Create a simple policy that captures the UserRequest
    captured_requests = []

    class CaptureVersionPolicy(sky.AdminPolicy):

        @classmethod
        def validate_and_mutate(cls, user_request):
            captured_requests.append(user_request)
            return sky.MutatedUserRequest(user_request.task,
                                          user_request.skypilot_config)

    # Set version info in contextvars (simulating what middleware does)
    versions.set_remote_api_version(31)
    versions.set_remote_version('1.0.0')

    try:
        with mock.patch('sky.utils.admin_policy_utils._get_policy_impl',
                        return_value=CaptureVersionPolicy()):
            with mock.patch('sky.utils.common_utils.get_current_user',
                            return_value=models.User(id='123', name='test')):
                os.environ[
                    skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = os.path.join(
                        POLICY_PATH, 'add_labels.yaml')
                importlib.reload(skypilot_config)

                admin_policy_utils.apply(task,
                                         request_name=request_names.
                                         AdminPolicyRequestName.CLUSTER_LAUNCH,
                                         at_client_side=False)

        assert len(captured_requests) == 1
        assert captured_requests[0].client_api_version == 31
        assert captured_requests[0].client_version == '1.0.0'
    finally:
        # Clean up contextvars
        versions.set_remote_api_version(None)
        versions.set_remote_version('unknown')


def test_apply_no_client_version_at_client_side(add_example_policy_paths, task):
    """Test that client version is None when at_client_side=True."""
    captured_requests = []

    class CaptureVersionPolicy(sky.AdminPolicy):

        @classmethod
        def validate_and_mutate(cls, user_request):
            captured_requests.append(user_request)
            return sky.MutatedUserRequest(user_request.task,
                                          user_request.skypilot_config)

    # Even if contextvars are set, client version should be None for
    # client-side
    versions.set_remote_api_version(31)
    versions.set_remote_version('1.0.0')

    try:
        with mock.patch('sky.utils.admin_policy_utils._get_policy_impl',
                        return_value=CaptureVersionPolicy()):
            os.environ[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = os.path.join(
                POLICY_PATH, 'add_labels.yaml')
            importlib.reload(skypilot_config)

            admin_policy_utils.apply(task,
                                     request_name=request_names.
                                     AdminPolicyRequestName.CLUSTER_LAUNCH,
                                     at_client_side=True)

        assert len(captured_requests) == 1
        assert captured_requests[0].client_api_version is None
        assert captured_requests[0].client_version is None
    finally:
        # Clean up contextvars
        versions.set_remote_api_version(None)
        versions.set_remote_version('unknown')


def test_apply_surfaces_resolved_active_workspace(add_example_policy_paths,
                                                  task):
    """The resolved active workspace is surfaced to the policy's config.

    The active workspace can be resolved into a thread-local context (e.g. by
    the server-side workspace resolver) rather than being written into the
    config file. Verify that admin policies still receive it as
    `active_workspace` instead of `None`.
    """
    captured_requests = []

    class CaptureWorkspacePolicy(sky.AdminPolicy):

        @classmethod
        def validate_and_mutate(cls, user_request):
            captured_requests.append(user_request)
            return sky.MutatedUserRequest(user_request.task,
                                          user_request.skypilot_config)

    with mock.patch('sky.utils.admin_policy_utils._get_policy_impl',
                    return_value=CaptureWorkspacePolicy()):
        # add_labels.yaml does not set active_workspace, so without the fix
        # the policy would receive `active_workspace: None`.
        os.environ[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = os.path.join(
            POLICY_PATH, 'add_labels.yaml')
        importlib.reload(skypilot_config)

        # Simulate the server-side resolver setting the active workspace in a
        # thread-local context rather than in the config file.
        with skypilot_config.local_active_workspace_ctx('team-a'):
            dag, mutated_config = admin_policy_utils.apply(
                task,
                request_name=request_names.AdminPolicyRequestName.
                CLUSTER_LAUNCH,
                at_client_side=True)

    assert len(captured_requests) == 1
    assert captured_requests[0].skypilot_config.get(
        'active_workspace') == 'team-a', (
            'Policy should receive the resolved active workspace')
    assert mutated_config.get('active_workspace') == 'team-a'


def test_reject_old_clients_policy(add_example_policy_paths, task):
    """Test RejectOldClientsPolicy rejects old clients."""
    from example_policy.skypilot_policy import RejectOldClientsPolicy

    policy = RejectOldClientsPolicy()

    # Test that old API version is rejected
    user_request = sky.UserRequest(
        task=task,
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
        skypilot_config=sky.Config(),
        at_client_side=False,
        client_api_version=20,  # Below MIN_REQUIRED_API_VERSION (25)
        client_version='0.5.0')
    with pytest.raises(RuntimeError, match='Client API version 20'):
        policy.apply(user_request)

    # Test that None client_api_version is rejected (very old clients)
    user_request_no_version = sky.UserRequest(
        task=task,
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
        skypilot_config=sky.Config(),
        at_client_side=False,
        client_api_version=None,
        client_version=None)
    with pytest.raises(RuntimeError, match='Client version information is not'):
        policy.apply(user_request_no_version)

    # Test that new API version is accepted
    user_request_new = sky.UserRequest(
        task=task,
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
        skypilot_config=sky.Config(),
        at_client_side=False,
        client_api_version=30,  # Above MIN_REQUIRED_API_VERSION (25)
        client_version='1.0.0')
    result = policy.apply(user_request_new)
    assert result.task is not None

    # Test that client-side application is allowed (no version check)
    user_request_client_side = sky.UserRequest(
        task=task,
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
        skypilot_config=sky.Config(),
        at_client_side=True,
        client_api_version=None,
        client_version=None)
    result = policy.apply(user_request_client_side)
    assert result.task is not None


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
    # 30s budget: cold-start of a uvicorn subprocess (interpreter + fastapi
    # + sky imports) routinely exceeds the prior 5s on CI runners.
    timeout_s = 30.0
    while time.time() - start_time < timeout_s:
        try:
            response = requests.get(f'http://localhost:{port}', timeout=0.5)
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
            f'Policy server on port {port} failed to start within '
            f'{timeout_s:.0f} seconds')
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


def test_add_volumes_policy(add_example_policy_paths, task):
    """Test AddVolumesPolicy using the standard integration path.

    This test uses _load_task_and_apply_policy to test the complete admin
    policy integration, matching the pattern used by other tests like
    test_use_spot_for_all_gpus_policy.
    """
    dag, _ = _load_task_and_apply_policy(
        task, os.path.join(POLICY_PATH, 'add_volumes.yaml'))
    assert dag.tasks[0].volumes['/mnt/data0'] == 'pvc0'


def test_add_volumes_policy_server_side(add_volumes_policy_cls, task):
    """Test AddVolumesPolicy server-side execution.

    This test verifies that the AddVolumesPolicy correctly adds volumes
    when executed on the server side (at_client_side=False).
    """
    policy = add_volumes_policy_cls()
    user_request = sky.UserRequest(
        task=task,
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
        skypilot_config=sky.Config(),
        at_client_side=False,
        user=models.User(id='test-user-id', name='test-user'))

    mutated_request = policy.apply(user_request)

    assert mutated_request.task.volumes['/mnt/data0'] == 'pvc0'
    assert len(mutated_request.task.volumes) == 1


def test_add_volumes_policy_skips_controller_task_server_side(
        add_volumes_policy_cls, task):
    """Test that AddVolumesPolicy skips controller tasks on server side.

    Controller tasks (managed job/serve controllers) should not have volumes
    added to avoid mounting organization volumes on job controllers.
    """
    with mock.patch.object(task, 'is_controller_task', return_value=True):
        policy = add_volumes_policy_cls()
        user_request = sky.UserRequest(
            task=task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
            skypilot_config=sky.Config(),
            at_client_side=False,
            user=models.User(id='test-user-id', name='test-user'))

        mutated_request = policy.apply(user_request)

        assert len(mutated_request.task.volumes) == 0


def test_add_volumes_policy_with_existing_volumes_server_side(
        add_volumes_policy_cls, task):
    """Test AddVolumesPolicy behavior when task already has volumes.

    The set_volumes method should overwrite existing volumes as designed.
    """
    task.set_volumes({'/mnt/existing': 'existing-volume'})

    policy = add_volumes_policy_cls()
    user_request = sky.UserRequest(
        task=task,
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
        skypilot_config=sky.Config(),
        at_client_side=False,
        user=models.User(id='test-user-id', name='test-user'))

    mutated_request = policy.apply(user_request)

    assert mutated_request.task.volumes['/mnt/data0'] == 'pvc0'
    assert '/mnt/existing' not in mutated_request.task.volumes
    assert len(mutated_request.task.volumes) == 1


def test_slurm_filesystem_routing_policy(add_example_policy_paths, task):
    """Verify SlurmFilesystemRoutingPolicy request filtering and caching.

    - Non-OPTIMIZE and client-side requests are no-ops (no SSH).
    - OPTIMIZE sets slurm.allowed_clusters; SSH results are cached so a
      second apply() call does not re-invoke SSH.
    """
    import example_policy.skypilot_policy as slurm_policy

    config_path = os.path.join(POLICY_PATH, 'slurm_filesystem_routing.yaml')

    slurm_policy._filesystem_cache.clear()

    task = sky.Task()
    task.update_envs({'SKYPILOT_REQUIRED_FILESYSTEMS': '/data/mnist,/scratch'})

    os.environ[skypilot_config.ENV_VAR_SKYPILOT_CONFIG] = config_path
    importlib.reload(skypilot_config)

    # --- Part 1: non-OPTIMIZE and client-side requests are no-ops ---
    with mock.patch('example_policy.skypilot_policy._check_paths_via_ssh',
                    return_value=True) as m:
        for request_name in [
                request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
                request_names.AdminPolicyRequestName.CLUSTER_EXEC,
                request_names.AdminPolicyRequestName.VALIDATE,
        ]:
            admin_policy_utils.apply(task, request_name=request_name)

        # Client-side OPTIMIZE should also be a no-op.
        admin_policy_utils.apply(
            task,
            request_name=request_names.AdminPolicyRequestName.OPTIMIZE,
            at_client_side=True)

        assert m.call_count == 0, (
            'SSH must not be called for non-OPTIMIZE or client-side requests')

    # --- Part 2: OPTIMIZE sets slurm.allowed_clusters; SSH results cached ---
    slurm_policy._filesystem_cache.clear()

    with mock.patch.object(sky.clouds.Slurm,
                           'existing_allowed_clusters',
                           return_value=['cluster-a', 'cluster-b']):
        with mock.patch('example_policy.skypilot_policy._check_paths_via_ssh',
                        side_effect=lambda cluster, paths: cluster ==
                        'cluster-a') as mock_ssh:
            # First OPTIMIZE: cache miss -> SSH called once per cluster.
            _, config1 = admin_policy_utils.apply(
                task,
                request_name=request_names.AdminPolicyRequestName.OPTIMIZE)
            assert mock_ssh.call_count == 2, (
                'SSH must be called once per cluster on first OPTIMIZE')
            assert config1.get_nested(('slurm', 'allowed_clusters'), None) == [
                'cluster-a'
            ], ('Only cluster-a has all paths; cluster-b must be filtered out')

            # Second OPTIMIZE: cache hit -> no additional SSH calls.
            admin_policy_utils.apply(
                task,
                request_name=request_names.AdminPolicyRequestName.OPTIMIZE)
            assert mock_ssh.call_count == 2, (
                'SSH must NOT be called again (cache hit)')

    required = frozenset(['/data/mnist', '/scratch'])
    assert ('cluster-a', required) in slurm_policy._filesystem_cache
    assert ('cluster-b', required) in slurm_policy._filesystem_cache


def test_add_volumes_policy_server_side_vs_client_side(add_volumes_policy_cls,
                                                       task):
    """Test consistency between server-side and client-side execution.

    The AddVolumesPolicy should produce the same volume configuration
    whether executed on client side or server side.
    """
    policy = add_volumes_policy_cls()

    client_user_request = sky.UserRequest(
        task=sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml')),
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
        skypilot_config=sky.Config(),
        at_client_side=True)

    client_mutated_request = policy.apply(client_user_request)

    server_user_request = sky.UserRequest(
        task=sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml')),
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
        skypilot_config=sky.Config(),
        at_client_side=False,
        user=models.User(id='test-user-id', name='test-user'))

    server_mutated_request = policy.apply(server_user_request)

    assert (client_mutated_request.task.volumes ==
            server_mutated_request.task.volumes)
    assert client_mutated_request.task.volumes['/mnt/data0'] == 'pvc0'
    assert server_mutated_request.task.volumes['/mnt/data0'] == 'pvc0'


def test_mutated_user_request_log_redacts_secrets(add_example_policy_paths,
                                                  task, tmp_path):
    """The mutated-request debug log must not carry the API server token.

    MutatedUserRequest holds the whole SkyPilot config, so logging it renders
    the config as a dict repr -- a different path from the YAML config dumps,
    and one that leaked the token into CI logs until it was redacted too.
    """
    token = 'sky_eyJhbGciOiJIUzI1NiJ9.CANARYTOKENVALUE.sig'
    config_path = tmp_path / 'config.yaml'
    config_path.write_text('admin_policy: example_policy.AddLabelsPolicy\n'
                           'api_server:\n'
                           '  endpoint: https://api.example.com\n'
                           f'  service_account_token: {token}\n')

    # sky loggers do not propagate to root, so caplog cannot see them; attach
    # a handler to the emitting logger directly.
    emitter = logging.getLogger('sky.utils.admin_policy_utils')
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    previous_level = emitter.level
    emitter.addHandler(handler)
    emitter.setLevel(logging.DEBUG)
    try:
        _load_task_and_apply_policy(task, str(config_path))
    finally:
        emitter.removeHandler(handler)
        emitter.setLevel(previous_level)

    logged = stream.getvalue()
    assert 'Mutated user request:' in logged, (
        f'expected the debug line to be emitted, got: {logged!r}')
    assert token not in logged, 'the API token reached a log line'
    assert '<redacted>' in logged, (
        'the token should be replaced, not dropped, so the log still shows '
        'that the field was set')


_ROUTED_WS = 'ws-a'
_USER = models.User(id='u1', name='alice')


class _PinWorkspacePolicy(sky.AdminPolicy):

    @classmethod
    def validate_and_mutate(cls, user_request):
        config = user_request.skypilot_config
        config.set_nested(('active_workspace',), _ROUTED_WS)
        return sky.MutatedUserRequest(user_request.task, config)


class _PinMissingWorkspacePolicy(sky.AdminPolicy):

    @classmethod
    def validate_and_mutate(cls, user_request):
        config = user_request.skypilot_config
        config.set_nested(('active_workspace',), 'ws-missing')
        return sky.MutatedUserRequest(user_request.task, config)


_LABELS = ('kubernetes', 'custom_metadata', 'labels')


class _CountingPinPolicy(sky.AdminPolicy):
    """Pins ws-a and counts its runs in a label; records every input."""
    inputs = []

    @classmethod
    def validate_and_mutate(cls, user_request):
        config = user_request.skypilot_config
        cls.inputs.append((user_request.request_name, copy.deepcopy(config)))
        labels = dict(config.get_nested(_LABELS, {}))
        labels['policy-runs'] = str(int(labels.get('policy-runs', '0')) + 1)
        config.set_nested(_LABELS, labels)
        config.set_nested(('active_workspace',), _ROUTED_WS)
        return sky.MutatedUserRequest(user_request.task, config)


class _DropWorkspacePolicy(sky.AdminPolicy):

    @classmethod
    def validate_and_mutate(cls, user_request):
        config = user_request.skypilot_config
        config.pop('active_workspace', None)
        return sky.MutatedUserRequest(user_request.task, config)


@pytest.fixture
def workspaces_config(tmp_path, monkeypatch):
    config_path = tmp_path / 'config.yaml'
    config_path.write_text('workspaces:\n'
                           '  ws-a: {}\n'
                           '  ws-w: {}\n'
                           '  ws-x: {}\n')
    monkeypatch.setenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG,
                       str(config_path))
    importlib.reload(skypilot_config)
    yield
    monkeypatch.delenv(skypilot_config.ENV_VAR_SKYPILOT_CONFIG, raising=False)
    importlib.reload(skypilot_config)


@pytest.fixture
def server_policy(workspaces_config):
    """Installs a server policy; returns the permission and cluster mocks."""
    policy = mock.Mock()
    policy.cls = _PinWorkspacePolicy
    with mock.patch('sky.utils.admin_policy_utils._get_policy_impl',
                    side_effect=lambda _: policy.cls()), \
            mock.patch('sky.utils.common_utils.get_current_user',
                       return_value=_USER), \
            mock.patch('sky.workspaces.core.check_workspace_permission'
                      ) as check, \
            mock.patch('sky.global_user_state.get_cluster_workspace',
                       return_value=None) as cluster_workspace:
        policy.check = check
        policy.cluster_workspace = cluster_workspace
        yield policy


@pytest.fixture
def policy_logs():
    # sky loggers do not propagate to root, so caplog cannot see them.
    emitter = logging.getLogger('sky.utils.admin_policy_utils')
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    emitter.addHandler(handler)
    try:
        yield stream
    finally:
        emitter.removeHandler(handler)


@contextlib.contextmanager
def _request_in(shape: str) -> Iterator[None]:
    """Enters the request workspace the way the executor would."""
    if shape == 'default':
        # Nothing named a workspace and the resolver picked 'default'.
        yield
    elif shape == 'resolver':
        # The per-user resolver picked a preferred workspace.
        with skypilot_config.local_active_workspace_ctx('ws-x'):
            yield
    else:
        # `-w ws-x`.
        assert shape == 'explicit', shape
        with skypilot_config.override_skypilot_config(
            {'active_workspace': 'ws-x'}):
            yield


def _options(cluster_name: Optional[str]) -> sky.admin_policy.RequestOptions:
    return sky.admin_policy.RequestOptions(cluster_name=cluster_name,
                                           idle_minutes_to_autostop=None,
                                           down=False,
                                           dryrun=False)


@pytest.mark.parametrize('shape,request_ws', [('default', 'default'),
                                              ('resolver', 'ws-x'),
                                              ('explicit', 'ws-x')])
def test_server_policy_routes_user_request(server_policy, policy_logs, task,
                                           shape, request_ws):
    with _request_in(shape):
        with admin_policy_utils.apply_and_use_config_in_current_request(
                task,
                request_name=request_names.AdminPolicyRequestName.
                CLUSTER_LAUNCH,
                request_options=_options('new-cluster')):
            assert skypilot_config.get_active_workspace() == _ROUTED_WS
            # Worker threads have no thread-local; they read the config.
            seen = []
            thread = threading.Thread(target=lambda: seen.append(
                skypilot_config.get_active_workspace()))
            thread.start()
            thread.join()
            assert seen == [_ROUTED_WS]
        assert skypilot_config.get_active_workspace() == request_ws
    server_policy.check.assert_called_once_with(_USER,
                                                _ROUTED_WS,
                                                action='write')
    assert ("INFO Using workspace 'ws-a' (source: admin policy)."
            in policy_logs.getvalue())


@pytest.mark.parametrize('request_name', [
    request_names.AdminPolicyRequestName.JOBS_LAUNCH_CONTROLLER,
    request_names.AdminPolicyRequestName.SERVE_LAUNCH_CONTROLLER,
    request_names.AdminPolicyRequestName.JOBS_LAUNCH_CLUSTER,
    request_names.AdminPolicyRequestName.SERVE_LAUNCH_REPLICA,
    request_names.AdminPolicyRequestName.SERVE_UPDATE,
])
def test_controller_and_update_requests_keep_their_workspace(
        server_policy, policy_logs, task, request_name):
    with skypilot_config.override_skypilot_config({'active_workspace': 'ws-w'}):
        _, config = admin_policy_utils.apply(task, request_name=request_name)
    assert config['active_workspace'] == 'ws-w'
    server_policy.check.assert_not_called()
    logged = policy_logs.getvalue()
    assert (f"WARNING The admin policy set workspace 'ws-a' for request "
            f"{request_name}, but it stays in workspace 'ws-w'" in logged)
    assert 'source: admin policy' not in logged


@pytest.mark.parametrize('request_name', [
    request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
    request_names.AdminPolicyRequestName.CLUSTER_EXEC,
])
@pytest.mark.parametrize('shape,moved', [('explicit', True), ('default', True),
                                         ('cluster', False)])
def test_existing_cluster_keeps_its_recorded_workspace(server_policy,
                                                       policy_logs, task,
                                                       request_name, shape,
                                                       moved):
    server_policy.cluster_workspace.return_value = 'ws-w'
    ctx = (skypilot_config.override_skypilot_config({
        'active_workspace': 'ws-w'
    }) if shape == 'cluster' else _request_in(shape))
    with ctx:
        with admin_policy_utils.apply_and_use_config_in_current_request(
                task,
                request_name=request_name,
                request_options=_options('existing-cluster')):
            assert skypilot_config.get_active_workspace() == 'ws-w'
    server_policy.cluster_workspace.assert_called_once_with('existing-cluster')
    logged = policy_logs.getvalue()
    assert ("existing cluster 'existing-cluster' is in workspace 'ws-w'"
            in logged)
    if moved:
        # Moving the request needs write access, like any routing.
        server_policy.check.assert_called_once_with(_USER,
                                                    'ws-w',
                                                    action='write')
        assert ("INFO Using workspace 'ws-w' (source: workspace of existing "
                "cluster 'existing-cluster')." in logged)
    else:
        server_policy.check.assert_not_called()
    assert 'source: admin policy' not in logged


def test_move_to_existing_cluster_without_access_names_the_cluster(
        server_policy, task):
    server_policy.cluster_workspace.return_value = 'ws-w'
    server_policy.check.side_effect = exceptions.PermissionDeniedError('denied')
    with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                       match=("workspace 'ws-w' \\(source: workspace of "
                              "existing cluster 'existing-cluster'\\): user "
                              "'alice' has no write access")):
        admin_policy_utils.apply(
            task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_EXEC,
            request_options=_options('existing-cluster'))


def test_unknown_policy_workspace_is_rejected(server_policy, task):
    server_policy.cls = _PinMissingWorkspacePolicy
    with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                       match='Workspace ws-missing set by the admin policy '
                       'does not exist'):
        admin_policy_utils.apply(
            task, request_name=request_names.AdminPolicyRequestName.JOBS_LAUNCH)
    server_policy.check.assert_not_called()


def test_routing_without_write_access_is_rejected(server_policy, task):
    server_policy.check.side_effect = exceptions.PermissionDeniedError('denied')
    with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                       match=("workspace 'ws-a' \\(source: admin policy\\): "
                              "user 'alice' has no write access")):
        with admin_policy_utils.apply_and_use_config_in_current_request(
                task,
                request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH
        ):
            pytest.fail('the request must not run')


def test_validate_routes_without_checking_access(server_policy, task):
    # The pre-check runs with the identity the client sent; the request itself
    # is checked with the authenticated user.
    server_policy.check.side_effect = exceptions.PermissionDeniedError('denied')
    _, config = admin_policy_utils.apply(
        task, request_name=request_names.AdminPolicyRequestName.VALIDATE)
    assert config['active_workspace'] == _ROUTED_WS
    server_policy.check.assert_not_called()


def test_validate_keeps_the_workspace_when_the_policy_names_none(
        server_policy, task):
    # A controller validates before every relaunch; a policy changed to a
    # missing workspace must not fail it.
    server_policy.cls = _PinMissingWorkspacePolicy
    with skypilot_config.local_active_workspace_ctx('ws-x'):
        _, config = admin_policy_utils.apply(
            task, request_name=request_names.AdminPolicyRequestName.VALIDATE)
    assert config['active_workspace'] == 'ws-x'
    server_policy.check.assert_not_called()


def test_dropped_workspace_is_normalised(server_policy, task):
    server_policy.cls = _DropWorkspacePolicy
    with skypilot_config.local_active_workspace_ctx('ws-x'):
        _, config = admin_policy_utils.apply(
            task, request_name=request_names.AdminPolicyRequestName.JOBS_LAUNCH)
    assert config['active_workspace'] == 'ws-x'
    server_policy.check.assert_not_called()


def test_client_side_apply_is_unchanged(server_policy, policy_logs, task):
    with admin_policy_utils.apply_and_use_config_in_current_request(
            task,
            request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
            request_options=_options('existing-cluster'),
            at_client_side=True):
        assert skypilot_config.get_active_workspace() == _ROUTED_WS
    server_policy.cls = _DropWorkspacePolicy
    _, config = admin_policy_utils.apply(
        task,
        request_name=request_names.AdminPolicyRequestName.CLUSTER_LAUNCH,
        at_client_side=True)
    # The client sends what its policy returned; the server resolves it.
    assert 'active_workspace' not in config
    server_policy.check.assert_not_called()
    server_policy.cluster_workspace.assert_not_called()
    assert 'source: admin policy' not in policy_logs.getvalue()


def _controller_record(cluster_name: str) -> dict:
    handle = mock.MagicMock(spec=backends.CloudVmRayResourceHandle)
    handle.cluster_name = cluster_name
    handle.launched_resources = mock.MagicMock(unsafe=True)
    handle.launched_resources.assert_launchable.return_value.cloud.\
        get_user_identities.return_value = None
    return {'handle': handle, 'workspace': 'default', 'owner': None}


def _launch_managed_job(consolidation: bool,
                        parent_job_id: Optional[int] = None) -> dict:
    """Runs jobs launch under the installed policy; returns what it saw."""
    task = sky.Task(name='train', run='echo hi')
    seen = {'controller_entry_ws': [], 'controller_ws': []}
    controller = _controller_record('sky-jobs-controller-1')

    def execution_launch(*_args, cluster_name, _request_name, **_kwargs):
        seen['controller_entry_ws'].append(
            skypilot_config.get_active_workspace())
        # The policy part of execution.launch, then the owner check that
        # provisioning an existing controller runs.
        with admin_policy_utils.apply_and_use_config_in_current_request(
                sky.Task(name='controller', run='true'),
                request_name=_request_name,
                request_options=_options(cluster_name)):
            seen['controller_ws'].append(skypilot_config.get_active_workspace())
            # pylint: disable=protected-access
            backend_utils._check_owner_identity_with_record(
                cluster_name, controller)
        return 1, controller['handle']

    def consolidated_launch(*_args, **_kwargs):
        seen['controller_ws'].append(skypilot_config.get_active_workspace())
        return [7], None

    def controller_vars(*_args, local_user_config, **_kwargs):
        # Runs inside the launch, after the records are written.
        seen['snapshot'] = copy.deepcopy(local_user_config)
        seen['launch_labels'] = skypilot_config.get_nested(_LABELS, None)
        threads = []
        thread = threading.Thread(target=lambda: threads.append(
            skypilot_config.get_active_workspace()))
        thread.start()
        thread.join()
        seen['thread_ws'] = threads[0]
        return {}

    parent = mock.Mock(workspace='ws-w',
                       is_job_group=True,
                       root_job_id=None,
                       tree_root_job_id=parent_job_id)
    with contextlib.ExitStack() as stack:

        def patch(obj, name, **kwargs):
            return stack.enter_context(mock.patch.object(obj, name, **kwargs))

        patch(jobs_core.managed_job_utils,
              'is_consolidation_mode',
              return_value=consolidation)
        local_record = patch(jobs_core.managed_job_state,
                             'set_job_info_without_job_id',
                             return_value=7)
        patch(jobs_core.managed_job_state, 'set_pending')
        patch(jobs_core.managed_job_state,
              'next_dynamic_task_index',
              return_value=1)
        seen['parent_rows'] = patch(jobs_core.managed_job_state,
                                    'get_job_info_row',
                                    return_value=parent)
        patch(jobs_core.managed_job_state,
              'get_status',
              return_value=jobs_core.managed_job_state.ManagedJobStatus.RUNNING)
        backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
        backend.set_job_info_without_job_id.return_value = [7]
        patch(jobs_core.backend_utils,
              'is_controller_accessible',
              return_value=controller['handle'])
        patch(jobs_core.backend_utils,
              'get_backend_from_handle',
              return_value=backend)
        patch(jobs_core.backend_utils,
              'get_task_resources_str',
              return_value='')
        patch(jobs_core.global_user_state,
              'cluster_with_name_exists',
              return_value=False)
        patch(jobs_core.service_catalog_common,
              'get_modified_catalog_file_mounts',
              return_value={})
        patch(jobs_core, '_upload_files_to_controller', return_value={})
        patch(jobs_core.controller_utils,
              'get_controller_resources',
              return_value=task.resources)
        patch(jobs_core.controller_utils,
              'shared_controller_vars_to_fill',
              side_effect=controller_vars)
        patch(jobs_core.common_utils, 'fill_template')
        patch(jobs_core.common,
              'with_server_user',
              side_effect=contextlib.nullcontext)
        patch(sky.Task, 'from_yaml', return_value=mock.Mock())
        patch(jobs_core,
              '_consolidated_launch',
              side_effect=consolidated_launch)
        patch(jobs_core.execution, 'launch', side_effect=execution_launch)
        jobs_core.launch(task, stream_logs=False, parent_job_id=parent_job_id)

    record = (local_record
              if consolidation else backend.set_job_info_without_job_id)
    seen['record_ws'] = record.call_args.kwargs['workspace']
    seen['after_ws'] = skypilot_config.get_active_workspace()
    return seen


@pytest.mark.parametrize('consolidation', [True, False])
def test_jobs_launch_records_the_routed_workspace(server_policy, tmp_path,
                                                  monkeypatch, consolidation):
    monkeypatch.setenv('HOME', str(tmp_path))
    seen = _launch_managed_job(consolidation)
    assert seen['record_ws'] == _ROUTED_WS
    assert seen['snapshot']['active_workspace'] == _ROUTED_WS
    # Worker threads of the launch see the routed workspace too.
    assert seen['thread_ws'] == _ROUTED_WS
    assert seen['after_ws'] == 'default'


def test_jobs_launch_keeps_the_controller_in_default(server_policy, policy_logs,
                                                     tmp_path, monkeypatch):
    """A routed jobs launch brings up and uses the controller in default.

    The request itself is in default, so without the routed workspace
    installed the controller launch's local_active_workspace_ctx('default')
    is a no-op, the policy moves the controller launch, and the owner check
    fails.
    """
    monkeypatch.setenv('HOME', str(tmp_path))
    seen = _launch_managed_job(consolidation=False)
    assert seen['record_ws'] == _ROUTED_WS
    # Once to bring the controller up, once to submit to it. The nested
    # local_active_workspace_ctx('default') took effect before the policy ran.
    assert seen['controller_entry_ws'] == ['default', 'default']
    assert seen['controller_ws'] == ['default', 'default']
    assert ("WARNING The admin policy set workspace 'ws-a' for request "
            "jobs.launch_controller, but it stays in workspace 'default'"
            in policy_logs.getvalue())


def test_jobs_launch_applies_the_policy_once_per_request(
        server_policy, tmp_path, monkeypatch):
    """Each request's policy sees the same input as without routing.

    Only the workspace of the launch changes; the policy's other mutations go
    to the controller snapshot and are not applied again.
    """
    monkeypatch.setenv('HOME', str(tmp_path))
    server_policy.cls = _CountingPinPolicy
    _CountingPinPolicy.inputs = []
    with skypilot_config.override_skypilot_config({'active_workspace': 'ws-x'}):
        original = skypilot_config.resolved_config()
        seen = _launch_managed_job(consolidation=False)
    controller_input = copy.deepcopy(original)
    controller_input['active_workspace'] = 'default'
    names = request_names.AdminPolicyRequestName
    assert _CountingPinPolicy.inputs == [
        (names.JOBS_LAUNCH, original),
        (names.JOBS_LAUNCH_CONTROLLER, controller_input),
        (names.JOBS_LAUNCH_CONTROLLER, controller_input),
    ]
    assert seen['snapshot'].get_nested(_LABELS, None) == {'policy-runs': '1'}
    assert seen['launch_labels'] is None
    assert seen['record_ws'] == _ROUTED_WS


def test_attached_job_keeps_its_group_workspace(server_policy, policy_logs,
                                                tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    seen = _launch_managed_job(consolidation=True, parent_job_id=5)
    seen['parent_rows'].assert_called_with(5)
    assert seen['record_ws'] == 'ws-w'
    assert seen['snapshot']['active_workspace'] == 'ws-w'
    assert ("WARNING The admin policy set workspace 'ws-a' for request "
            "jobs.launch, but it stays in workspace 'ws-w': parent job 5 is in "
            "workspace 'ws-w'." in policy_logs.getvalue())
