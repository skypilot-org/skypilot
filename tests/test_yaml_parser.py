import pathlib
import textwrap

import pytest
from sky.task import Task


def _create_config_file(config: str, config_file_path: pathlib.Path) -> None:
    config_file_path.open('w').write(config)


def test_empty_fields_task(tmp_path):
    config_path = tmp_path / 'config.yaml'
    _create_config_file(
        textwrap.dedent(f"""\
            name: task

            resources:

            workdir: examples/

            file_mounts:

            setup: echo "Running setup."

            num_nodes:

            run:
            """), config_path)
    task = Task.from_yaml(config_path)

    assert task.name == 'task'
    assert list(task.resources)[0].is_empty()
    assert task.file_mounts is None
    assert task.run is None
    assert task.setup == 'echo "Running setup."'
    assert task.num_nodes == 1
    assert task.workdir == 'examples/'


def test_invalid_fields_task(tmp_path):
    # Load from a config file
    config_path = tmp_path / 'config.yaml'
    _create_config_file(
        textwrap.dedent(f"""\
            name: task

            not_a_valid_field:
            """), config_path)
    with pytest.raises(AssertionError) as e:
        Task.from_yaml(config_path)
    assert 'Invalid task args' in e.value.args[0]


def test_empty_fields_resources(tmp_path):
    config_path = tmp_path / 'config.yaml'
    _create_config_file(
        textwrap.dedent(f"""\
            resources:
                cloud:
                region:
                accelerators: V100:1
                disk_size:
                use_spot:
                cpus: 32
            """), config_path)
    task = Task.from_yaml(config_path)

    resources = list(task.resources)[0]
    assert resources.cloud is None
    assert resources.region is None
    assert resources.accelerators == {'V100': 1}
    assert resources.disk_size is 256
    assert resources.use_spot is False
    assert resources.cpus == "32"


def test_invalid_fields_resources(tmp_path):
    config_path = tmp_path / 'config.yaml'
    _create_config_file(
        textwrap.dedent(f"""\
            resources:
                cloud: aws
                not_a_valid_field:
            """), config_path)
    with pytest.raises(AssertionError) as e:
        Task.from_yaml(config_path)
        assert 'Invalid task args' in e.value.args[0]


def test_empty_fields_storage(tmp_path):
    config_path = tmp_path / 'config.yaml'
    _create_config_file(
        textwrap.dedent(f"""\
            file_mounts:
                /mystorage:
                    name: sky-dataset
                    source:
                    store:
                    persistent:
            """), config_path)
    task = Task.from_yaml(config_path)

    storage = task.storage_mounts['/mystorage']
    assert storage.name == 'sky-dataset'
    assert storage.source is None
    assert len(storage.stores) == 0
    assert storage.persistent is True


def test_invalid_fields_storage(tmp_path):
    config_path = tmp_path / 'config.yaml'
    _create_config_file(
        textwrap.dedent(f"""\
            file_mounts:
                /datasets-storage:
                    name: sky-dataset
                    not_a_valid_field:
            """), config_path)
    with pytest.raises(AssertionError) as e:
        Task.from_yaml(config_path)
        assert 'Invalid task args' in e.value.args[0]
