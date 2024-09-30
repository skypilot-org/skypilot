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

logger = sky_logging.init_logger(__name__)

POLICY_PATH = os.path.join(os.path.dirname(os.path.dirname(sky.__file__)),
                           'examples', 'admin_policy')


@pytest.fixture
def add_example_policy_paths():
    # Add to path to be able to import
    sys.path.append(os.path.join(POLICY_PATH, 'example_policy'))


@pytest.fixture
def task():
    return sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))


def _load_task_and_apply_policy(
    task: sky.Task,
    config_path: str,
    idle_minutes_to_autostop: Optional[int] = None,
) -> Tuple[sky.Dag, skypilot_config.Config]:
    os.environ['SKYPILOT_CONFIG'] = config_path
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
    dag, _ = _load_task_and_apply_policy(
        task, os.path.join(POLICY_PATH, 'add_labels.yaml'))
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


@mock.patch('sky.provision.kubernetes.utils.get_all_kube_config_context_names',
            return_value=['kind-skypilot', 'kind-skypilot2', 'kind-skypilot3'])
def test_dynamic_kubernetes_contexts_policy(add_example_policy_paths, task):
    _, config = _load_task_and_apply_policy(
        task,
        os.path.join(POLICY_PATH, 'dynamic_kubernetes_contexts_update.yaml'))

    assert config.get_nested(
        ('kubernetes', 'allowed_contexts'),
        None) == ['kind-skypilot', 'kind-skypilot2'
                 ], 'Kubernetes allowed contexts should be updated'

    assert skypilot_config.get_nested(
        ('kubernetes', 'allowed_contexts'),
        None) == ['kind-skypilot',
                  'kind-skypilot2'], 'Global skypilot config should be updated'
