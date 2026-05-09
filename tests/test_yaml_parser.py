import os
import pathlib
import textwrap

import pytest

from sky.exceptions import InvalidSkyPilotConfigError
from sky.task import Task
from sky.utils import dag_utils
from sky.utils import yaml_utils


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


def test_gpu_resources(tmp_path):
    config_path = _create_config_file(
        textwrap.dedent("""\
            resources:
                gpus: V100:1
            """), tmp_path)
    task = Task.from_yaml(config_path)

    resources = list(task.resources)[0]
    assert resources.accelerators == {'V100': 1}


def test_both_gpus_and_accelerators_raises_config_error(tmp_path):
    """Aliased and canonical names cannot both be specified in config."""
    config_path = _create_config_file(
        textwrap.dedent("""\
            resources:
                accelerators: V100:1
                gpus: [T4:4, V100:8]
            """), tmp_path)

    with pytest.raises(InvalidSkyPilotConfigError) as e:
        Task.from_yaml(config_path)
    assert e.value.args[
        0] == "Cannot specify both gpus and accelerators in config."


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
    assert not storage.stores
    assert storage.persistent


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


def test_replace_envs_in_workdir(tmpdir, tmp_path):
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            envs:
                env_key1: {tmpdir}
            workdir: $env_key1
            """), tmp_path)
    task = Task.from_yaml(config_path)
    assert task.workdir == tmpdir


def test_anyof_error_points_at_inner_problem(tmp_path):
    """Schema errors inside an anyOf/oneOf should name the real issue.

    Previously the validator surfaced the outer 'is not valid under any of
    the given schemas' message and truncated the JSON path at the anyOf
    boundary, which gave users no clue what was wrong. With
    jsonschema.exceptions.best_match, the inner type-mismatch error should
    be reported instead.
    """
    config_path = _create_config_file(
        textwrap.dedent("""\
            resources:
                cpus: 2
                autostop:
                    idle_minutes: "ten"
            """), tmp_path)
    with pytest.raises(InvalidSkyPilotConfigError) as e:
        Task.from_yaml(config_path)
    msg = e.value.args[0]
    # Must not fall back to the outer anyOf message.
    assert 'not valid under any of the given schemas' not in msg
    # Must name the real issue: idle_minutes expected an integer.
    assert 'integer' in msg
    assert 'ten' in msg


def test_file_mounts_relative_dest_is_accepted(tmp_path):
    """Relative file_mount destinations are resolved against ~/sky_workdir
    at provision time (see cloud_vm_ray_backend.py). Validation must not
    reject them.
    """
    src = tmp_path / 'local.txt'
    src.write_text('hi')
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            run: echo hi
            file_mounts:
                relative/dest: {src}
            """), tmp_path)
    task = Task.from_yaml(config_path)
    task.expand_and_validate_file_mounts()
    assert 'relative/dest' in task.file_mounts


def test_file_mounts_empty_task_is_not_triggered_by_file_mounts(tmp_path):
    # Sanity: an absolute destination still works.
    src = tmp_path / 'local.txt'
    src.write_text('hi')
    config_path = _create_config_file(
        textwrap.dedent(f"""\
            run: echo hi
            file_mounts:
                /remote/dest: {src}
            """), tmp_path)
    task = Task.from_yaml(config_path)
    assert '/remote/dest' in task.file_mounts


def test_duplicate_top_level_key_rejected(tmp_path):
    """Duplicate YAML keys were silently accepted (PyYAML default), with
    the later value winning. That masked real user typos.
    """
    config_path = _create_config_file(
        textwrap.dedent("""\
            name: first
            name: second
            run: echo hi
            """), tmp_path)
    with pytest.raises(ValueError) as e:
        Task.from_yaml(config_path)
    msg = str(e.value)
    assert 'name' in msg
    assert 'duplicate' in msg.lower()


def test_check_no_duplicate_keys_handles_non_scalar_key():
    """Non-scalar mapping keys (e.g. `? [a, b]`) are legal YAML but not
    hashable; the duplicate-key walker must skip them rather than raise
    a bare TypeError from `key in seen`. (PyYAML's own safe_load will
    still reject the document downstream; we only care that our walker
    is robust.)
    """

    # Should not raise TypeError.
    yaml_utils.check_no_duplicate_keys(
        textwrap.dedent("""\
        ? [a, b]
        : 1
        run: echo hi
        """))


def test_check_no_duplicate_keys_silent_on_malformed_yaml():
    """`check_no_duplicate_keys` uses `yaml.compose_all`, which can itself
    raise on malformed YAML. We swallow that so the regular `safe_load`
    parser produces the user-facing error, not our helper.
    """

    # Unclosed bracket — compose_all would raise. check_no_duplicate_keys
    # should swallow and return silently.
    yaml_utils.check_no_duplicate_keys('name: [unclosed\nrun: echo hi\n')


def test_duplicate_nested_key_rejected(tmp_path):
    """Duplicate file_mount destinations (which is also a duplicate YAML
    key) should be rejected so the user learns about the conflict.
    """
    config_path = _create_config_file(
        textwrap.dedent("""\
            run: echo hi
            file_mounts:
              /remote: /etc/hosts
              /remote: /etc/passwd
            """), tmp_path)
    with pytest.raises(ValueError) as e:
        Task.from_yaml(config_path)
    assert 'duplicate' in str(e.value).lower()


def test_binary_entrypoint_raises_friendly_error(tmp_path):
    """A non-UTF-8 file passed as an entrypoint used to leak a raw
    UnicodeDecodeError traceback from deep inside PyYAML. Users should
    see a short, actionable error instead.
    """
    config_path = tmp_path / 'binary.yaml'
    config_path.write_bytes(b'\x00\x01\x02\x03\xff\xfe')
    # Use the DAG loader path to mirror `sky launch <file>`.
    with pytest.raises(ValueError) as e:
        dag_utils.load_chain_dag_from_yaml(str(config_path))
    msg = str(e.value)
    assert 'UTF-8' in msg
    assert os.path.basename(str(config_path)) in msg


def test_null_body_yaml_does_not_crash(tmp_path):
    """A YAML file containing only `null` (or `---\\n---`) previously
    produced an internal AssertionError ('tasks: []') that leaked a
    traceback. Users should get a clear error instead.
    """
    config_path = _create_config_file('null\n', tmp_path)
    with pytest.raises(ValueError) as e:
        dag_utils.load_chain_dag_from_yaml(str(config_path))
    # Must not be an AssertionError traceback; must explain the problem.
    assert 'empty' in str(e.value).lower() or 'no task' in str(e.value).lower()


def test_multiple_unknown_fields_separator(tmp_path):
    """Multiple typos at the same level should not be concatenated together.

    Previously the error read '...did you mean X?Instead of...' with no
    whitespace separator between the two suggestions.
    """
    config_path = _create_config_file(
        textwrap.dedent("""\
            setups: echo hello
            env:
                FOO: bar
            """), tmp_path)
    with pytest.raises(InvalidSkyPilotConfigError) as e:
        Task.from_yaml(config_path)
    msg = e.value.args[0]
    # Both suggestions must be present...
    assert "did you mean 'setup'" in msg
    assert "did you mean 'envs'" in msg
    # ...and they must be separated by at least whitespace or a newline
    # so the message is readable. The old broken form was '...?Instead'.
    assert '?Instead' not in msg
