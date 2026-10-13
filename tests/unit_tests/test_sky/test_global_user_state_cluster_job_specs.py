"""Tests for recording the task YAML / entrypoint of cluster jobs.

The spec is recorded by the API server when a job is added to a cluster
(`sky launch` / `sky exec`) and read back for the dashboard's cluster job page.
"""
from unittest import mock

from sky import core
from sky import global_user_state
from sky import task as task_lib
from sky.backends import cloud_vm_ray_backend
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import yaml_utils
from sky.utils.db import db_utils


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setattr(
        global_user_state,
        '_db_manager',
        db_utils.DatabaseManager(
            'state',
            global_user_state.create_table,
            post_init_fn=lambda _: global_user_state._sqlite_supports_returning(
            ),
        ),
    )


class _MinimalHandle:
    """Just enough for global_user_state.add_or_update_cluster to pickle."""
    launched_resources = None


def _add_cluster(name: str) -> str:
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=_MinimalHandle(),
        requested_resources=set(),
        ready=True,
    )
    cluster_hash = global_user_state.get_cluster_hash(name)
    assert cluster_hash is not None
    return cluster_hash


def test_add_and_get_spec(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    global_user_state.add_cluster_job_spec('h1', 1, 'run: echo 1\n',
                                           'sky launch -c c task.yaml')
    global_user_state.add_cluster_job_spec('h1', 2, 'run: echo 2\n',
                                           'sky exec c "echo 2"')

    spec = global_user_state.get_cluster_job_spec('h1', 2)
    assert spec is not None
    assert spec['task_yaml'] == 'run: echo 2\n'
    assert spec['entrypoint'] == 'sky exec c "echo 2"'
    assert spec['submitted_at'] is not None
    # Unknown job, unknown cluster: jobs submitted before specs were recorded.
    assert global_user_state.get_cluster_job_spec('h1', 3) is None
    assert global_user_state.get_cluster_job_spec('h2', 1) is None


def test_same_job_id_overwrites(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    global_user_state.add_cluster_job_spec('h1', 1, 'old', 'old cmd')
    global_user_state.add_cluster_job_spec('h1', 1, 'new', 'new cmd')

    spec = global_user_state.get_cluster_job_spec('h1', 1)
    assert spec is not None
    assert (spec['task_yaml'], spec['entrypoint']) == ('new', 'new cmd')


def test_add_spec_failure_is_swallowed(tmp_path, monkeypatch):
    """Recording runs on the job submission path and must not fail it."""
    _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(global_user_state._db_manager, 'get_engine',
                        mock.Mock(side_effect=RuntimeError('db down')))
    monkeypatch.setattr(global_user_state.db_retries.time, 'sleep',
                        lambda *_: None)

    assert global_user_state.add_cluster_job_spec('h1', 1, 'y', 'c') is None


def test_terminate_drops_only_that_clusters_specs(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    hash_a = _add_cluster('a')
    hash_b = _add_cluster('b')
    global_user_state.add_cluster_job_spec(hash_a, 1, 'ya', 'ca')
    global_user_state.add_cluster_job_spec(hash_b, 1, 'yb', 'cb')

    # Stopping keeps the specs: the cluster and its job ids live on.
    global_user_state.remove_cluster('a', terminate=False)
    assert global_user_state.get_cluster_job_spec(hash_a, 1) is not None

    global_user_state.remove_cluster('a', terminate=True)
    assert global_user_state.get_cluster_job_spec(hash_a, 1) is None
    assert global_user_state.get_cluster_job_spec(hash_b, 1) is not None


def test_relaunched_cluster_does_not_see_old_specs(tmp_path, monkeypatch):
    """Job ids restart at 1 after `sky down` + relaunch under the same name."""
    _fresh_db(tmp_path, monkeypatch)
    old_hash = _add_cluster('c')
    global_user_state.add_cluster_job_spec(old_hash, 1, 'old', 'old cmd')
    global_user_state.remove_cluster('c', terminate=True)

    new_hash = _add_cluster('c')
    assert new_hash != old_hash
    assert core.cluster_job_spec('c', 1) is None


def test_core_cluster_job_spec(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    cluster_hash = _add_cluster('c')
    global_user_state.add_cluster_job_spec(cluster_hash, 1, 'run: x\n', 'cmd')

    spec = core.cluster_job_spec('c', 1)
    assert spec is not None
    assert spec['task_yaml'] == 'run: x\n'
    assert core.cluster_job_spec('c', 2) is None
    assert core.cluster_job_spec('no-such-cluster', 1) is None


def _record(task, cluster_hash='h1'):
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    handle = mock.Mock(cluster_name='c')
    with mock.patch.object(global_user_state,
                           'get_cluster_hash',
                           return_value=cluster_hash), \
         mock.patch.object(global_user_state,
                           'add_cluster_job_spec') as add_spec, \
         mock.patch.object(common_utils,
                           'get_current_command',
                           return_value='sky exec c task.yaml'):
        backend._record_job_spec(handle, 7, task)
    return add_spec


def test_backend_records_redacted_user_yaml():
    config = {
        'name': 'train',
        'run': 'echo hi',
        'envs': {
            'PLAIN': 'visible'
        },
        'secrets': {
            'TOKEN': 'hunter2'
        },
    }
    user_yaml = yaml_utils.dump_yaml_str(config)
    # from_yaml_config consumes the dict it is given.
    task = task_lib.Task.from_yaml_config(dict(config))
    task._user_specified_yaml = user_yaml

    add_spec = _record(task)

    add_spec.assert_called_once()
    cluster_hash, job_id, task_yaml, entrypoint = add_spec.call_args.args
    assert (cluster_hash, job_id, entrypoint) == ('h1', 7,
                                                  'sky exec c task.yaml')
    recorded = yaml_utils.safe_load(task_yaml)
    assert recorded['run'] == 'echo hi'
    assert recorded['envs'] == {'PLAIN': 'visible'}
    assert 'hunter2' not in task_yaml


def test_backend_skips_without_cluster_hash():
    task = task_lib.Task(run='echo hi')
    add_spec = _record(task, cluster_hash=None)
    add_spec.assert_not_called()


def test_backend_does_not_raise_when_building_spec_fails():
    task = task_lib.Task(run='echo hi')
    with mock.patch.object(task,
                           'to_yaml_config',
                           side_effect=RuntimeError('boom')):
        add_spec = _record(task)
    add_spec.assert_not_called()


def test_backend_records_env_and_secret_overrides():
    """`--env` / `--secret` overrides are not in the user's YAML file."""
    config = {
        'run': 'echo $LR',
        'envs': {
            'LR': '3e-4',
            'MODEL': 'm'
        },
        'secrets': {
            'TOKEN': 'from-file'
        },
    }
    user_yaml = yaml_utils.dump_yaml_str(config)
    task = task_lib.Task.from_yaml_config(dict(config))
    task._user_specified_yaml = user_yaml
    task.update_envs({'LR': '1e-3'})
    task.update_secrets({'EXTRA': 'from-cli'})

    add_spec = _record(task)

    task_yaml = add_spec.call_args.args[2]
    recorded = yaml_utils.safe_load(task_yaml)
    assert recorded['envs'] == {'LR': '1e-3', 'MODEL': 'm'}
    assert recorded['secrets'] == {'TOKEN': '<redacted>', 'EXTRA': '<redacted>'}
    assert 'from-file' not in task_yaml
    assert 'from-cli' not in task_yaml
