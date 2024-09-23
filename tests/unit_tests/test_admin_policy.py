import importlib
import os
import sys
from typing import Optional, Tuple

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


def _load_task_and_apply_policy(
    config_path: str,
    idle_minutes_to_autostop: Optional[int] = None
) -> Tuple[sky.Dag, skypilot_config.Config]:
    os.environ['SKYPILOT_CONFIG'] = config_path
    importlib.reload(skypilot_config)
    task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
    return admin_policy_utils.apply(
        task,
        request_options=sky.admin_policy.RequestOptions(
            cluster_name='test',
            cluster_running=False,
            idle_minutes_to_autostop=idle_minutes_to_autostop,
            down=False,
            dryrun=False,
        ))


def test_task_level_changes_policy(add_example_policy_paths):
    dag, _ = _load_task_and_apply_policy(
        os.path.join(POLICY_PATH, 'task_label_config.yaml'))
    assert 'local_user' in list(dag.tasks[0].resources)[0].labels


def test_config_level_changes_policy(add_example_policy_paths):
    _load_task_and_apply_policy(
        os.path.join(POLICY_PATH, 'config_label_config.yaml'))
    print(skypilot_config._dict)
    assert 'local_user' in skypilot_config.get_nested(('gcp', 'labels'), {})


def test_reject_all_policy(add_example_policy_paths):
    with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                       match='Reject all policy'):
        _load_task_and_apply_policy(
            os.path.join(POLICY_PATH, 'reject_all_config.yaml'))


def test_enforce_autostop_policy(add_example_policy_paths):
    _load_task_and_apply_policy(os.path.join(POLICY_PATH,
                                             'enforce_autostop.yaml'),
                                idle_minutes_to_autostop=10)
    with pytest.raises(exceptions.UserRequestRejectedByPolicy,
                       match='Autostop/down must be set'):
        _load_task_and_apply_policy(os.path.join(POLICY_PATH,
                                                 'enforce_autostop.yaml'),
                                    idle_minutes_to_autostop=None)
