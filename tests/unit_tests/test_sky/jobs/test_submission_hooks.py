"""Managed job submission hooks and their launch integration."""
import contextlib
from unittest import mock

import pytest

from sky import dag as dag_lib
from sky import task as task_lib
from sky.jobs.server import core as jobs_core
from sky.server import plugin_hooks
from sky.utils import controller_utils


@pytest.fixture(autouse=True)
def isolated_hooks(monkeypatch):
    monkeypatch.setattr(plugin_hooks, '_MANAGED_JOB_SUBMITTED_HOOKS', {})


def test_registration_replaces_callback():
    old = mock.Mock()
    new = mock.Mock()
    plugin_hooks.register_managed_job_submitted_hook('test', old)
    plugin_hooks.register_managed_job_submitted_hook('test', new)
    dag = dag_lib.Dag()
    plugin_hooks.fire_managed_job_submitted([1], dag, None, 'user')
    old.assert_not_called()
    new.assert_called_once_with([1], dag, None, 'user')


def test_registration_during_dispatch():
    added = mock.Mock()
    plugin_hooks.register_managed_job_submitted_hook(
        'register',
        lambda *args: plugin_hooks.register_managed_job_submitted_hook(
            'added', added))
    dag = dag_lib.Dag()
    plugin_hooks.fire_managed_job_submitted([1], dag, None, 'user')
    added.assert_not_called()
    plugin_hooks.fire_managed_job_submitted([2], dag, None, 'user')
    added.assert_called_once_with([2], dag, None, 'user')


@pytest.mark.parametrize('consolidation', [True, False])
@pytest.mark.parametrize('blob_id', ['uploaded-blob', None])
@pytest.mark.parametrize('raises', [False, True])
@pytest.mark.parametrize('translate_workdir', [False, True])
def test_launch_fires_submission_hooks(consolidation, blob_id, raises, tmp_path,
                                       translate_workdir):
    task = task_lib.Task(name='train', run='echo hello')
    task.workdir = str(tmp_path)
    dag = mock.Mock(spec=dag_lib.Dag)
    dag.name = 'train'
    dag.tasks = [task]
    dag.is_chain.return_value = True
    dag.is_job_group.return_value = False
    job_ids = [41, 42]
    hook = mock.Mock(
        side_effect=RuntimeError('hook failed') if raises else None)
    following_hook = mock.Mock()
    plugin_hooks.register_managed_job_submitted_hook('test', hook)
    plugin_hooks.register_managed_job_submitted_hook('following',
                                                     following_hook)

    def finish(*_args, **_kwargs):
        # Hooks run before controller startup, once for the entire sweep.
        hook.assert_called_once()
        submitted_dag = hook.call_args.args[1]
        assert submitted_dag is not dag
        assert hook.call_args.args == (job_ids, submitted_dag, blob_id,
                                       'request-user')
        following_hook.assert_called_once_with(job_ids, submitted_dag, blob_id,
                                               'request-user')
        assert submitted_dag.tasks[0].workdir == str(tmp_path)
        assert submitted_dag.pool is None
        assert dag.tasks[0].workdir == (None
                                        if translate_workdir else str(tmp_path))
        return job_ids, None

    with contextlib.ExitStack() as stack:

        def patch(obj, name, **kwargs):
            return stack.enter_context(mock.patch.object(obj, name, **kwargs))

        patch(jobs_core.dag_utils,
              'convert_entrypoint_to_dag',
              return_value=dag)
        patch(jobs_core.admin_policy_utils, 'apply', return_value=(dag, {}))
        patch(jobs_core.managed_job_utils,
              'is_consolidation_mode',
              return_value=consolidation)
        patch(jobs_core.dag_utils, 'dump_dag_to_yaml_str', return_value='')
        patch(jobs_core.dag_utils, 'maybe_infer_and_fill_dag_and_task_names')
        patch(jobs_core.dag_utils, 'fill_default_config_in_dag_for_job_launch')
        patch(jobs_core.dag_utils, 'dump_dag_to_yaml')
        patch(jobs_core.global_user_state,
              'cluster_with_name_exists',
              return_value=False)
        patch(jobs_core, '_warn_file_mounts_rolling_update')

        def stage_workdirs(dag_to_stage):
            if translate_workdir:
                return controller_utils.translate_local_file_mounts_to_two_hop(
                    dag_to_stage.tasks[0])
            return {}

        patch(jobs_core,
              '_upload_files_to_controller',
              side_effect=stage_workdirs)
        patch(jobs_core.controller_utils,
              'get_controller_resources',
              return_value=task.resources)
        submit = patch(jobs_core,
                       '_maybe_submit_job_locally',
                       return_value=job_ids if consolidation else None)
        remote = patch(jobs_core, '_submit_remotely', return_value=job_ids)
        patch(jobs_core.service_catalog_common,
              'get_modified_catalog_file_mounts',
              return_value={})
        patch(jobs_core.controller_utils,
              'shared_controller_vars_to_fill',
              return_value={})
        patch(jobs_core.common_utils,
              'get_user_hash',
              return_value='request-user')
        patch(jobs_core.common_utils, 'fill_template')
        patch(jobs_core.common,
              'with_server_user',
              side_effect=contextlib.nullcontext)
        patch(jobs_core.skypilot_config,
              'local_active_workspace_ctx',
              side_effect=lambda *args: contextlib.nullcontext())
        patch(jobs_core.skypilot_config,
              'remove_queue_name_from_config',
              side_effect=contextlib.nullcontext)
        patch(task_lib.Task, 'from_yaml', return_value=mock.Mock())
        patch(jobs_core, '_consolidated_launch', side_effect=finish)
        patch(jobs_core.execution, 'launch', side_effect=finish)
        warning = patch(plugin_hooks.logger, 'warning')

        # pylint: disable=protected-access
        result = jobs_core._launch(task,
                                   name=None,
                                   pool=None,
                                   num_jobs=2,
                                   stream_logs=False,
                                   file_mounts_blob_id=blob_id,
                                   parent_job_id=None,
                                   parent_task_id=None,
                                   root_job_id=None)

    assert result == (job_ids, None)
    assert submit.call_args.kwargs['file_mounts_blob_id'] == blob_id
    assert remote.call_count == (0 if consolidation else 1)
    if raises:
        warning.assert_called_once()
        assert 'hook failed' in warning.call_args.args[0]
    else:
        warning.assert_not_called()
