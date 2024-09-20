import importlib
import os
import sys

import pytest

import sky
from sky import sky_logging
from sky import skypilot_config
from sky.utils import policy_utils

logger = sky_logging.init_logger(__name__)

POLICY_PATH = os.path.join(os.path.dirname(os.path.dirname(sky.__file__)),
                           'examples', 'policy')


@pytest.fixture
def add_example_policy_paths():
    # Add to path to be able to import
    sys.path.append(os.path.join(POLICY_PATH, 'example_policy'))


def _load_task_and_apply_policy(config_path) -> sky.Dag:
    os.environ['SKYPILOT_CONFIG'] = config_path
    importlib.reload(skypilot_config)
    task = sky.Task.from_yaml(os.path.join(POLICY_PATH, 'task.yaml'))
    return policy_utils.apply(task)


def test_task_level_changes_policy(add_example_policy_paths):
    task = _load_task_and_apply_policy(
        os.path.join(POLICY_PATH, 'task_label_config.yaml'))
    assert 'local_user' in list(task.resources)[0].labels


def test_config_level_changes_policy(add_example_policy_paths):
    _load_task_and_apply_policy(
        os.path.join(POLICY_PATH, 'config_label_config.yaml'))
    assert 'local_user' in skypilot_config.get_nested(('aws', 'labels'), {})
