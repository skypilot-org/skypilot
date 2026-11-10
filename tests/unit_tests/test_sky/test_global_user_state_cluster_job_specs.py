"""Tests for recording the task YAML / entrypoint of cluster jobs.

The spec is recorded by the API server when a job is added to a cluster
(`sky launch` / `sky exec`) and read back for the dashboard's cluster job page.
"""
from unittest import mock

from sky import core
from sky import global_user_state
from sky import resources as resources_lib
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


def _add_cluster(name: str, is_managed: bool = False) -> str:
    global_user_state.add_or_update_cluster(
        cluster_name=name,
        cluster_handle=_MinimalHandle(),
        requested_resources=set(),
        ready=True,
        is_managed=is_managed,
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
    assert set(spec) == {'task_yaml', 'entrypoint'}
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


def _record(task, cluster_hash='h1', cluster_name='c'):
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    handle = mock.Mock(cluster_name=cluster_name)
    with mock.patch.object(global_user_state,
                           'get_job_spec_cluster_hash',
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
                           'to_submitted_yaml_config',
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


def _task_from_user_yaml(config, **overrides):
    """A task loaded from a YAML file, as the CLI does, then overridden."""
    user_yaml = yaml_utils.dump_yaml_str(config)
    task = task_lib.Task.from_yaml_config(yaml_utils.safe_load(user_yaml),
                                          **overrides)
    task._user_specified_yaml = user_yaml
    return task


def test_submitted_yaml_keeps_unchanged_resources_as_written():
    config = {'resources': {'cpus': '1+', 'memory': '2+'}, 'run': 'echo'}
    task = _task_from_user_yaml(config)

    recorded = task.to_submitted_yaml_config()

    assert recorded['resources'] == {'cpus': '1+', 'memory': '2+'}


def test_submitted_yaml_applies_resource_overrides():
    """`sky exec c --cpus 2 task.yaml` runs with 2 CPUs, not the file's 1+."""
    config = {'resources': {'cpus': '1+'}, 'run': 'echo'}
    task = _task_from_user_yaml(config)
    task.set_resources_override({'cpus': '2'})

    recorded = task.to_submitted_yaml_config()

    assert recorded['resources']['cpus'] == '2'


def test_submitted_yaml_applies_resource_overrides_to_every_choice():
    config = {
        'resources': {
            'any_of': [{
                'cpus': '1+'
            }, {
                'cpus': '4+'
            }]
        },
        'run': 'echo',
    }
    task = _task_from_user_yaml(config)
    task.set_resources_override({'memory': '8'})

    recorded = task.to_submitted_yaml_config()

    choices = recorded['resources']['any_of']
    assert sorted(c['cpus'] for c in choices) == ['1+', '4+']
    assert all(c['memory'] == '8' for c in choices)


def test_submitted_yaml_adds_cli_secret_to_list_form_secrets():
    """`secrets: [ref]` in the file plus `--secret EXTRA=value`."""
    config = {'secrets': ['HF_TOKEN'], 'run': 'echo'}
    task = _task_from_user_yaml(config,
                                secrets_overrides=[('EXTRA', 'cli-value')])

    recorded = task.to_submitted_yaml_config()
    dumped = yaml_utils.dump_yaml_str(recorded)

    assert 'cli-value' not in dumped
    assert 'EXTRA' in dumped
    assert 'HF_TOKEN' in dumped
    # Still a valid task YAML.
    task_lib.Task.from_yaml_config(yaml_utils.safe_load(dumped))


def test_backend_skips_controllers():
    """Every managed job / service adds a job to its controller."""
    task = task_lib.Task(run='echo hi')
    for controller in ('sky-jobs-controller-abcd1234',
                       'sky-serve-controller-abcd1234'):
        add_spec = _record(task, cluster_name=controller)
        add_spec.assert_not_called()


def test_submitted_yaml_applies_name_and_num_nodes_overrides():
    """`sky exec c --name renamed --num-nodes 1` on a file saying otherwise."""
    config = {'name': 'train', 'num_nodes': 2, 'run': 'echo'}
    task = _task_from_user_yaml(config)
    task.name = 'renamed'
    task.num_nodes = 1

    recorded = task.to_submitted_yaml_config()

    assert recorded['name'] == 'renamed'
    assert recorded['num_nodes'] == 1


def test_submitted_yaml_keeps_unchanged_name_and_num_nodes():
    config = {'name': 'train', 'run': 'echo'}
    task = _task_from_user_yaml(config)

    recorded = task.to_submitted_yaml_config()

    assert recorded['name'] == 'train'
    # Not in the file, and the default was not overridden.
    assert 'num_nodes' not in recorded


def test_submitted_yaml_applies_git_workdir_override():
    """`--git-url` / `--git-ref` replace the workdir with a git repo."""
    config = {'workdir': '.', 'run': 'echo'}
    task = _task_from_user_yaml(config)
    task.update_workdir(None, 'https://github.com/org/repo.git', 'v1')

    recorded = task.to_submitted_yaml_config()

    assert recorded['workdir'] == {
        'url': 'https://github.com/org/repo.git',
        'ref': 'v1'
    }


def test_submitted_yaml_keeps_local_workdir_as_written():
    """On the API server a local workdir points at the uploaded copy."""
    config = {'workdir': '~/my-project', 'run': 'echo'}
    task = _task_from_user_yaml(config)
    task.workdir = '/home/server/.sky/api_server/clients/abc/workdir'

    recorded = task.to_submitted_yaml_config()

    assert recorded['workdir'] == '~/my-project'


def test_keeps_only_the_last_job_ids_per_cluster(tmp_path, monkeypatch):
    """A long-lived cluster running many `sky exec`s does not grow unbounded."""
    _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(global_user_state, 'MAX_JOB_SPECS_PER_CLUSTER', 3)
    for job_id in range(1, 6):
        global_user_state.add_cluster_job_spec('h1', job_id, f'y{job_id}', 'c')
    global_user_state.add_cluster_job_spec('h2', 1, 'other', 'c')

    kept = [
        job_id for job_id in range(1, 6)
        if global_user_state.get_cluster_job_spec('h1', job_id) is not None
    ]
    assert kept == [3, 4, 5]
    # Another cluster's specs are untouched.
    assert global_user_state.get_cluster_job_spec('h2', 1) is not None


def test_job_spec_cluster_hash_skips_controller_launched_clusters(
        tmp_path, monkeypatch):
    """A managed job's cluster or a pool worker is launched by a controller."""
    _fresh_db(tmp_path, monkeypatch)
    user_hash = _add_cluster('mine')
    _add_cluster('my-job-1', is_managed=True)

    assert global_user_state.get_job_spec_cluster_hash('mine') == user_hash
    assert global_user_state.get_job_spec_cluster_hash('my-job-1') is None
    assert global_user_state.get_job_spec_cluster_hash('missing') is None


def test_backend_skips_controller_launched_clusters(tmp_path, monkeypatch):
    """Jobs the jobs controller submits to the clusters it launched."""
    _fresh_db(tmp_path, monkeypatch)
    _add_cluster('my-job-1', is_managed=True)
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    with mock.patch.object(global_user_state,
                           'add_cluster_job_spec') as add_spec:
        backend._record_job_spec(mock.Mock(cluster_name='my-job-1'), 1,
                                 task_lib.Task(run='echo hi'))
    add_spec.assert_not_called()


def test_execute_records_the_spec_after_submitting_the_job():
    """Recording is best-effort and must not delay the job's start."""
    backend = cloud_vm_ray_backend.CloudVmRayBackend()
    task = task_lib.Task(run='echo hi')
    handle = mock.Mock(cluster_name='c', num_ips_per_node=1)
    handle.provision_runtime_metadata.run_started = False
    calls = mock.Mock()
    with mock.patch.object(backend, 'check_resources_fit_cluster',
                           return_value=resources_lib.Resources()), \
         mock.patch.object(cloud_vm_ray_backend.backend_utils,
                           'get_task_resources_str', return_value='1x'), \
         mock.patch.object(backend, '_add_job',
                           return_value=(7, '/logs')) as add_job, \
         mock.patch.object(backend,
                           '_execute_task_one_node') as execute_one_node, \
         mock.patch.object(backend, '_record_job_spec') as record_spec:
        calls.attach_mock(add_job, 'add_job')
        calls.attach_mock(execute_one_node, 'execute_task')
        calls.attach_mock(record_spec, 'record_job_spec')
        assert backend._execute(handle, task) == 7

    order = [c[0] for c in calls.mock_calls]
    assert order == ['add_job', 'execute_task', 'record_job_spec'], order


def test_submitted_yaml_writes_nothing_to_the_users_output(capsys):
    """Recording runs after "Job submitted" is printed to the user.

    Rebuilding `Resources` from the user's YAML would print the
    `autostop.hook` deprecation warning a second time, between "Job
    submitted" and "Waiting for task resources" in the launch output.
    """
    config = {
        'run': 'echo',
        'resources': {
            'cpus': '2+',
            'autostop': {
                'idle_minutes': 1,
                'hook': 'echo bye',
                'hook_timeout': 30
            },
        },
    }
    task = _task_from_user_yaml(config)
    capsys.readouterr()  # Drop the warning printed when the task is loaded.

    recorded = task.to_submitted_yaml_config()

    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == '', captured
    # The hook is kept, in its non-deprecated form.
    assert recorded['config']['hooks'][0]['run'] == 'echo bye'
    assert 'hook' not in recorded['resources']['autostop']


def test_submitted_yaml_does_not_rebuild_resources():
    """Building `Resources` can load catalogs and print warnings."""
    task = _task_from_user_yaml({'resources': {'cpus': '1+'}, 'run': 'echo'})
    task.set_resources_override({'cpus': '2'})
    with mock.patch.object(resources_lib.Resources,
                           'from_yaml_config') as from_yaml_config:
        recorded = task.to_submitted_yaml_config()
    from_yaml_config.assert_not_called()
    assert recorded['resources']['cpus'] == '2'
