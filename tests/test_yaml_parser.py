import pathlib
import textwrap

import pytest

from sky.task import Task


def _create_config_file(config: str, tmp_path: pathlib.Path) -> str:
    config_path = tmp_path / 'config.yaml'
    config_path.open('w', encoding='utf-8').write(config)
    return config_path


def test_empty_fields_task(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            name: task
            resources:
            workdir: examples/
            file_mounts:
            setup: echo "Running setup."
            num_nodes:
            run:
              # commented out, empty run
            """), tmp_path)
    task = Task.from_yaml(config_path)

    assert task.name == 'task'
    assert list(task.resources)[0].is_empty()
    assert task.file_mounts is None
    assert task.run is None
    assert task.setup == 'echo "Running setup."'
    assert task.num_nodes == 1
    assert task.workdir == 'examples/'


def test_invalid_fields_task(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            name: task

            not_a_valid_field:
            """), tmp_path)
    with pytest.raises(AssertionError) as e:
        Task.from_yaml(config_path)
    assert 'Invalid task args' in e.value.args[0]


def test_empty_fields_resources(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            resources:
                cloud:
                region:
                accelerators: V100:1
                disk_size:
                use_spot:
                cpus: 32
            """), tmp_path)
    task = Task.from_yaml(config_path)

    resources = list(task.resources)[0]
    assert resources.cloud is None
    assert resources.region is None
    assert resources.accelerators == {'V100': 1}
    assert resources.disk_size is 256
    assert resources.use_spot is False
    assert resources.cpus == '32'


def test_invalid_fields_resources(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            resources:
                cloud: aws
                not_a_valid_field:
            """), tmp_path)
    with pytest.raises(AssertionError) as e:
        Task.from_yaml(config_path)
    assert 'Invalid resource args' in e.value.args[0]


def test_empty_fields_storage(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            file_mounts:
                /mystorage:
                    name: sky-dataset
                    source:
                    store:
                    persistent:
            """), tmp_path)
    task = Task.from_yaml(config_path)

    storage = task.storage_mounts['/mystorage']
    assert storage.name == 'sky-dataset'
    assert storage.source is None
    assert len(storage.stores) == 0
    assert storage.persistent is True


def test_invalid_fields_storage(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            file_mounts:
                /datasets-storage:
                    name: sky-dataset
                    not_a_valid_field:
            """), tmp_path)
    with pytest.raises(AssertionError) as e:
        Task.from_yaml(config_path)
    assert 'Invalid storage args' in e.value.args[0]


def test_invalid_envs_key(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            envs:
                invalid_env_$_key: value
            """), tmp_path)
    with pytest.raises(ValueError) as e:
        Task.from_yaml(config_path)
    assert 'does not match any of the regexes:' in e.value.args[0]


def test_invalid_envs_type(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            envs:
                - env_key1: abc
                - env_key2: abc
            """), tmp_path)
    with pytest.raises(ValueError) as e:
        Task.from_yaml(config_path)
    assert 'is not of type \'dict\'' in e.value.args[0]


def test_invalid_empty_envs(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            envs:
                env_key1: abc
                env_key2:
            """), tmp_path)
    with pytest.raises(ValueError) as e:
        Task.from_yaml(config_path)
    assert 'Environment variable \'env_key2\' is None.' in e.value.args[0]
