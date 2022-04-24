import pathlib
import pytest
import tempfile
import textwrap

import sky
from sky.backends import backend_utils

_TEST_YAML_PATHS = [
    'examples/minimal.yaml', 'examples/managed_spot.yaml',
    'examples/using_file_mounts.yaml', 'examples/resnet_app.yaml',
    'examples/multi_hostname.yaml', 'examples/storage_demo.yaml'
]


def _check_dict_same(d1, d2):
    """Check if two dicts are same."""
    for k, v in d1.items():
        if k not in d2:
            assert len(v) == 0, (k, v)
        if isinstance(v, dict):
            assert isinstance(d2[k], dict), (k, v, d2)
            _check_dict_same(v, d2[k])
        elif isinstance(v, str):
            assert v.lower() == d2[k].lower(), (k, v, d2[k])
        else:
            assert v == d2[k], (k, v, d2[k])


def _check_equivalent(yaml_path):
    """Check if the yaml is equivalent after load and dump again."""
    origin_task_config = backend_utils.read_yaml(yaml_path)

    task = sky.Task.from_yaml(yaml_path)
    new_task_config = task.to_yaml_config()
    _check_dict_same(origin_task_config, new_task_config)


def test_load_dump_yaml_config_equivalent():
    """Test if the yaml config is equivalent after load and dump again."""
    pathlib.Path('~/datasets').expanduser().mkdir(exist_ok=True)
    for yaml_path in _TEST_YAML_PATHS:
        _check_equivalent(yaml_path)


def test_spot_nonexist_strategy():
    """Test the nonexist recovery strategy."""
    task_yaml = textwrap.dedent("""\
        resources:
            cloud: aws
            use_spot: true
            spot_recovery: nonexist""")
    with tempfile.NamedTemporaryFile(mode='w') as f:
        f.write(task_yaml)
        f.flush()
        with pytest.raises(
                ValueError,
                match='is not supported. The strategy should be among'):
            sky.Task.from_yaml(f.name)
