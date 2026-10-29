"""Tests for sky.jobs.server.core."""
import re
from types import SimpleNamespace
from unittest import mock

import pytest

from sky import backends
from sky import dag as dag_lib
from sky import exceptions
from sky import resources as resources_lib
from sky import task as task_lib
from sky.jobs.server import core as jobs_core
from sky.jobs.state import ManagedJobStatus
from sky.server.requests import payloads
from sky.utils import dag_utils


def _forwarded_tail(tail):
    """Call ``jobs_core.tail_logs`` with ``tail`` (mocking out the controller
    restart / backend / runner) and return the ``tail`` value forwarded to
    ``tail_managed_job_logs``."""
    fake_backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    fake_runner = mock.MagicMock()
    fake_runner.tail_managed_job_logs.return_value = 0
    with mock.patch.object(jobs_core, '_maybe_restart_controller',
                           return_value=mock.MagicMock()), \
         mock.patch.object(jobs_core.backend_utils,
                           'get_backend_from_handle',
                           return_value=fake_backend), \
         mock.patch.object(jobs_core.managed_job_runner,
                           'current',
                           return_value=fake_runner):
        jobs_core.tail_logs(name=None,
                            job_id=1,
                            follow=False,
                            controller=False,
                            refresh=False,
                            tail=tail)
    fake_runner.tail_managed_job_logs.assert_called_once()
    return fake_runner.tail_managed_job_logs.call_args.kwargs['tail']


@pytest.mark.parametrize(
    ('given', 'expected'),
    [
        (0, None),  # dashboard download button's "all lines" sentinel
        (-1, None),  # `sky jobs logs --tail -1`
        (None, None),  # no tail -> all
        (200, 200),  # positive tail forwarded unchanged
        (5000, 5000),
    ])
def test_tail_logs_normalizes_non_positive_tail(given, expected):
    """A non-positive tail (0 / -1) means "all lines" and must be normalized
    to None before reaching the backward-seek tail reader (which asserts
    tail > 0). Otherwise the dashboard download (tail=0) raises
    AssertionError and produces an empty log."""
    assert _forwarded_tail(given) == expected


class TestCheckJobGroupAttachment:
    """`launch(parent_job_id=...)` pre-checks, and the consolidation-only rule."""

    @staticmethod
    def _run(parent_job_id,
             parent_task_id=None,
             *,
             explicit=False,
             consolidation=True,
             status=None,
             parent_workspace='default',
             parent_root=None,
             parent_execution='parallel',
             active_workspace='default',
             client_set_workspace=False,
             check_workspace_permission=None):
        status_enum = (None if status is None else
                       jobs_core.managed_job_state.ManagedJobStatus(status))
        parent_row = (None if status is None else
                      jobs_core.managed_job_state.JobInfoRow(
                          job_id=parent_job_id,
                          name='parent',
                          workspace=parent_workspace,
                          user_hash='u',
                          root_job_id=parent_root,
                          parent_job_id=None,
                          parent_task_id=None,
                          execution=parent_execution))
        with mock.patch.object(jobs_core.managed_job_utils,
                               'is_consolidation_mode',
                               return_value=consolidation), \
             mock.patch.object(jobs_core.managed_job_state, 'get_status',
                               return_value=status_enum), \
             mock.patch.object(jobs_core.managed_job_state,
                               'get_job_info_row',
                               return_value=parent_row), \
             mock.patch.object(jobs_core.skypilot_config,
                               'get_active_workspace',
                               return_value=active_workspace), \
             mock.patch.object(jobs_core, '_client_set_workspace',
                               return_value=client_set_workspace), \
             mock.patch.object(jobs_core.workspaces_core,
                               'check_workspace_permission',
                               side_effect=check_workspace_permission) \
                 as permission_check:
            result = jobs_core._check_job_group_attachment(
                parent_job_id, parent_task_id, explicit)
        TestCheckJobGroupAttachment.last_permission_calls = [
            (c.args[1], c.kwargs.get('action'))
            for c in permission_check.call_args_list
        ]
        return result

    def test_no_parent_records_nothing(self):
        assert self._run(None) == (None, None, None, None)

    def test_task_without_job_rejected(self):
        with pytest.raises(ValueError, match='requires parent_job_id'):
            self._run(None, parent_task_id=1)

    def test_running_parent_in_same_workspace_ok(self):
        assert self._run(42, 1, status='RUNNING') == (42, 1, 42, None)

    def test_root_comes_from_the_parent_row(self):
        # The parent's row decides the tree; the client sends no root.
        assert self._run(42, 1, status='RUNNING') == (42, 1, 42, None)
        # Parent is itself a dynamic member of 7: the child roots at 7.
        assert self._run(42, 1, status='RUNNING',
                         parent_root=7) == (42, 1, 7, None)

    def test_target_must_be_a_job_group_or_inside_one(self):
        # A job group: fine. A dynamic task of one (root set): fine, the
        # child joins the same tree. A plain top-level job: refused, since
        # its own nested launches stay top-level and the tree would be
        # inconsistent.
        assert self._run(42, status='RUNNING',
                         parent_execution='parallel') == (42, None, 42, None)
        assert self._run(57,
                         status='RUNNING',
                         parent_execution=None,
                         parent_root=42) == (57, None, 42, None)
        for explicit in (True, False):
            with pytest.raises(ValueError, match='not a job group'):
                self._run(7,
                          status='RUNNING',
                          parent_execution=None,
                          explicit=explicit)

    @pytest.mark.parametrize('status', ['PENDING', 'STARTING', 'RECOVERING'])
    def test_not_yet_running_parent_ok(self, status):
        # Anything that has not finished accepts new tasks.
        assert self._run(42, status=status) == (42, None, 42, None)

    @pytest.mark.parametrize(
        'status',
        ['SUCCEEDED', 'FAILED', 'FAILED_SETUP', 'CANCELLING', 'CANCELLED'])
    def test_finished_or_cancelling_parent_rejected(self, status):
        # Only a running job group accepts new tasks: a finished one has
        # nothing left to sweep them, a cancel in flight would orphan them.
        with pytest.raises(ValueError, match=status):
            self._run(42, status=status)

    def test_missing_parent_rejected(self):
        with pytest.raises(ValueError, match='no such managed job'):
            self._run(42, status=None)

    def test_nested_launch_inherits_the_parent_workspace(self):
        # The task's `sky jobs launch` names no workspace, so the request
        # resolved to the user's default; the child must still land in its
        # group's workspace. The check hands that workspace back for the
        # launch to run in.
        assert self._run(42,
                         status='RUNNING',
                         parent_workspace='team-a',
                         active_workspace='default',
                         client_set_workspace=False) == (42, None, 42, 'team-a')
        # Already in the parent's workspace: nothing to switch.
        assert self._run(42,
                         status='RUNNING',
                         parent_workspace='team-a',
                         active_workspace='team-a',
                         client_set_workspace=True) == (42, None, 42, None)

    def test_inheriting_a_workspace_requires_write_access_to_it(self):
        # The executor authorized the caller for the workspace the request
        # resolved to ('default'), not for the group's. Switching is a
        # launch in the group's workspace, so it needs write access there;
        # knowing a group id must not open a workspace the caller cannot
        # write to.
        assert self._run(42,
                         status='RUNNING',
                         parent_workspace='team-a',
                         active_workspace='default') == (42, None, 42, 'team-a')
        assert self.last_permission_calls == [('team-a', 'write')]

        def deny(user, workspace, action):  # pylint: disable=unused-argument
            raise jobs_core.exceptions.PermissionDeniedError(
                f'no access to {workspace}')

        with pytest.raises(jobs_core.exceptions.PermissionDeniedError,
                           match='team-a'):
            self._run(42,
                      status='RUNNING',
                      parent_workspace='team-a',
                      active_workspace='default',
                      check_workspace_permission=deny)
        # Already in the group's workspace: the executor's own check stands,
        # nothing extra is asked.
        self._run(42,
                  status='RUNNING',
                  parent_workspace='default',
                  active_workspace='default')
        assert self.last_permission_calls == []

    def test_explicit_other_workspace_rejected(self):
        # The request named a workspace and it is not the group's: an error,
        # not a silent switch.
        with pytest.raises(ValueError, match='workspace'):
            self._run(42,
                      status='RUNNING',
                      parent_workspace='team-a',
                      active_workspace='team-b',
                      client_set_workspace=True)

    def test_legacy_parent_counts_as_default_workspace(self):
        # JobInfoRow resolves a NULL workspace to 'default' (same as cancel),
        # so a legacy parent is checked, not exempt.
        assert self._run(42,
                         status='RUNNING',
                         parent_workspace='default',
                         active_workspace='default') == (42, None, 42, None)
        with pytest.raises(ValueError, match='workspace'):
            self._run(42,
                      status='RUNNING',
                      parent_workspace='default',
                      active_workspace='team-b',
                      client_set_workspace=True)

    def test_non_consolidation_drops_automatic_attachment(self):
        # A watcher's nested launch on a laptop setup keeps working, as a
        # top-level job, exactly as before dynamic job groups existed.
        assert self._run(42, 1, consolidation=False,
                         explicit=False) == (None, None, None, None)

    def test_non_consolidation_rejects_explicit_attachment(self):
        with pytest.raises(jobs_core.exceptions.NotSupportedError,
                           match='consolidation mode'):
            self._run(42, consolidation=False, explicit=True)


class TestResolveJobTask:
    """`sky jobs logs 39 2` / `sky jobs cancel 39 --task 2`: a dynamic task
    is addressed like the group's declared tasks."""

    _OWN = [{
        'task_id': 0,
        'task_name': 'trainer'
    }, {
        'task_id': 1,
        'task_name': 'watcher'
    }]

    def _run(self, task, *, for_cancel=False, own=None, member=57):
        with mock.patch.object(jobs_core.managed_job_state,
                               'get_managed_job_tasks',
                               return_value=self._OWN if own is None else own), \
             mock.patch.object(jobs_core.managed_job_state,
                               'get_dynamic_task_job_id',
                               return_value=member) as lookup:
            result = jobs_core._resolve_job_task(39,
                                                 task,
                                                 for_cancel=for_cancel)
        return result, lookup

    def test_declared_task_by_index_or_name_is_unchanged(self):
        assert self._run(1)[0] == (39, 1)
        assert self._run('1')[0] == (39, 1)  # numeric strings are indices
        assert self._run('watcher')[0] == (39, 'watcher')
        _, lookup = self._run(0)
        lookup.assert_not_called()

    def test_dynamic_task_by_index_or_name_is_its_own_job(self):
        # Index 2 continues the declared tasks 0 and 1; the whole member job is
        # tailed (task None).
        result, lookup = self._run(2)
        assert result == (57, None)
        lookup.assert_called_once_with(39, 2)
        result, lookup = self._run('eval-3')
        assert result == (57, None)
        lookup.assert_called_once_with(39, 'eval-3')

    def test_unknown_task_passes_through_for_logs(self):
        # The controller-side log reader answers an unknown task with
        # 'No task found matching ...' in the stream, which --no-follow and
        # SDK follow=False callers read; a server-side raise would reach
        # neither (smoke test_job_group_task_logs[_sdk] on a consolidation
        # server).
        assert self._run(9, member=None)[0] == (39, 9)
        assert self._run('nope', member=None)[0] == (39, 'nope')

    def test_unknown_task_raises_for_cancel(self):
        with pytest.raises(ValueError, match='no task 9'):
            self._run(9, member=None, for_cancel=True)
        with pytest.raises(ValueError, match="no task 'nope'"):
            self._run('nope', member=None, for_cancel=True)

    def test_missing_job_passes_through_for_logs_and_raises_for_cancel(self):
        assert self._run(2, own=[])[0] == (39, 2)
        with pytest.raises(ValueError, match='No managed job with ID 39'):
            self._run(2, own=[], for_cancel=True)

    def test_cancel_refuses_a_declared_task(self):
        # Declared tasks share the group's lifecycle.
        with pytest.raises(ValueError, match='not a dynamic task'):
            self._run(1, for_cancel=True)
        with pytest.raises(ValueError, match='not a dynamic task'):
            self._run('trainer', for_cancel=True)
        assert self._run(2, for_cancel=True)[0] == (57, None)


class TestCancelTaskArgument:

    def test_task_needs_exactly_one_job_id(self):
        for kwargs in ({
                'job_ids': [1, 2]
        }, {
                'job_ids': []
        }, {
                'job_ids': [1],
                'name': 'x'
        }, {
                'job_ids': [1],
                'all': True
        }):
            with pytest.raises(ValueError, match='exactly one job id'):
                jobs_core.cancel(task=2, **kwargs)

    def test_task_needs_consolidation_mode(self):
        with mock.patch.object(jobs_core.managed_job_utils,
                               'is_consolidation_mode',
                               return_value=False):
            with pytest.raises(jobs_core.exceptions.NotSupportedError,
                               match='consolidation mode'):
                jobs_core.cancel(job_ids=[39], task=2)


def test_cancel_names_the_active_workspace():
    # The controller formats the skip message with the workspace id; it has
    # no names, so the API server swaps in the name.
    fake_runner = mock.MagicMock()
    fake_runner.cancel_managed_jobs.return_value = (
        'No job to cancel. Job with ID 3 is skipped as they are not in the '
        "active workspace 'ws-0123456789ab'. Check the workspace of the job "
        'with: sky jobs queue')
    with mock.patch.object(jobs_core.backend_utils,
                           'is_controller_accessible',
                           return_value=mock.MagicMock(
                               is_grpc_enabled_with_flag=False)), \
         mock.patch.object(jobs_core.backend_utils,
                           'get_backend_from_handle',
                           return_value=mock.MagicMock(
                               spec=backends.CloudVmRayBackend)), \
         mock.patch.object(jobs_core.managed_job_runner,
                           'current',
                           return_value=fake_runner), \
         mock.patch.object(jobs_core.skypilot_config,
                           'get_active_workspace',
                           return_value='ws-0123456789ab'), \
         mock.patch.object(jobs_core.skypilot_config,
                           'get_workspace_name',
                           return_value='ml'), \
         mock.patch.object(jobs_core.logger, 'info') as info:
        jobs_core.cancel(job_ids=[3])
    message = info.call_args.args[0]
    assert "active workspace 'ml'" in message
    assert 'ws-0123456789ab' not in message


# ---------------------------------------------------------------------------
# jobs restart (`sky jobs launch --restart`)
# ---------------------------------------------------------------------------

_TOKEN_VAR = jobs_core.skylet_constants.SERVICE_ACCOUNT_TOKEN_ENV_VAR


def _dag_yaml(*tasks):
    """The compiled DAG YAML a jobs controller stores for these tasks."""
    with dag_lib.Dag() as dag:
        for task in tasks:
            dag.add(task)
        for upstream, downstream in zip(tasks, tasks[1:]):
            dag.add_edge(upstream, downstream)
    dag.name = tasks[0].name
    return dag_utils.dump_chain_dag_to_yaml_str(dag)


def _train_task(**kwargs):
    task = task_lib.Task(name='train',
                         run='python train.py',
                         envs={'LR': '1e-3'},
                         secrets={
                             'HF_TOKEN': 'hf',
                             _TOKEN_VAR: 'revoked-token'
                         },
                         **kwargs)
    task.set_resources(resources_lib.Resources(cpus='2+'))
    return task


def _record(status=ManagedJobStatus.FAILED,
            dag_yaml=None,
            job_id=5,
            name='train',
            pool=None,
            workspace='default'):
    """A stand-in for one task row of responses.ManagedJobRecord."""
    return SimpleNamespace(job_id=job_id,
                           task_id=0,
                           job_name=name,
                           status=status,
                           dag_yaml=(dag_yaml if dag_yaml is not None else
                                     _dag_yaml(_train_task())),
                           pool=pool,
                           workspace=workspace)


def _restart(records, *, active_workspace='default', **kwargs):
    """Runs jobs_core.restart over `records`; returns the launch() mock."""
    with mock.patch.object(jobs_core, 'queue_v2_api',
                           return_value=(records, len(records), {}, 0, [])), \
         mock.patch.object(jobs_core.skypilot_config,
                           'get_active_workspace',
                           return_value=active_workspace), \
         mock.patch.object(jobs_core, 'launch',
                           return_value=([99], None)) as mock_launch:
        kwargs.setdefault('job_id', 5)
        assert jobs_core.restart(**kwargs) == ([99], None)
    return mock_launch


def _launched_dag(mock_launch):
    mock_launch.assert_called_once()
    return mock_launch.call_args.args[0]


def test_generate_rerun_name_adds_a_random_suffix():
    first = jobs_core._generate_rerun_name('train')
    second = jobs_core._generate_rerun_name('train')
    assert re.fullmatch(r'train-rerun-[0-9a-f]{6}', first)
    # Concurrent restarts of one job must not share a name.
    assert first != second


def test_generate_rerun_name_does_not_stack_suffixes():
    name = jobs_core._generate_rerun_name('train-rerun-1a2b3c')
    assert re.fullmatch(r'train-rerun-[0-9a-f]{6}', name)


def test_restart_reruns_the_stored_spec_as_a_new_job():
    mock_launch = _restart([_record(pool='gpu-pool')])
    dag = _launched_dag(mock_launch)
    task = dag.tasks[0]
    assert task.run == 'python train.py'
    assert task.envs['LR'] == '1e-3'
    assert task.secrets['HF_TOKEN'].get_secret_value() == 'hf'
    # The original job's API token was revoked when it finished; launch()
    # mints a new one.
    assert _TOKEN_VAR not in task.secrets
    assert re.fullmatch(r'train-rerun-[0-9a-f]{6}', dag.name)
    assert task.name == dag.name
    assert mock_launch.call_args.kwargs == {
        'name': dag.name,
        'pool': 'gpu-pool'
    }


def test_restart_explicit_name_and_pool():
    mock_launch = _restart([_record(pool='gpu-pool')],
                           name='my-retry',
                           pool='other-pool')
    dag = _launched_dag(mock_launch)
    assert dag.name == 'my-retry'
    assert mock_launch.call_args.kwargs == {
        'name': 'my-retry',
        'pool': 'other-pool'
    }


def test_restart_applies_overrides():
    mock_launch = _restart([_record()],
                           resources_override={
                               'cloud': 'aws',
                               'accelerators': 'A100:8',
                               'ports': ['8080'],
                           },
                           env_overrides=[('LR', '1e-4')],
                           secrets_overrides=[('HF_TOKEN', 'new')],
                           num_nodes=2)
    task = _launched_dag(mock_launch).tasks[0]
    assert task.envs['LR'] == '1e-4'
    assert task.secrets['HF_TOKEN'].get_secret_value() == 'new'
    assert task.num_nodes == 2
    resources = list(task.resources)[0]
    assert str(resources.cloud) == 'AWS'
    assert resources.accelerators == {'A100': 8}
    assert resources.ports == ['8080']
    # Fields that were not overridden keep their stored value.
    assert resources.cpus == '2+'


def test_restart_multi_task_takes_env_but_not_resource_overrides():
    second = task_lib.Task(name='eval', run='python eval.py')
    yaml_str = _dag_yaml(_train_task(), second)
    records = [_record(dag_yaml=yaml_str), _record(dag_yaml=yaml_str)]

    dag = _launched_dag(_restart(records, env_overrides=[('LR', '1e-4')]))
    assert [t.envs.get('LR') for t in dag.tasks] == ['1e-4', '1e-4']
    # Task names are kept; only the job is renamed.
    assert [t.name for t in dag.tasks] == ['train', 'eval']

    with pytest.raises(ValueError, match='single-task'):
        _restart(records, resources_override={'accelerators': 'A100:8'})
    with pytest.raises(ValueError, match='single-task'):
        _restart(records, num_nodes=2)


@pytest.mark.parametrize('status', [ManagedJobStatus.RUNNING, 'PENDING'])
def test_restart_rejects_a_job_that_has_not_finished(status):
    with pytest.raises(ValueError, match='Only finished jobs'):
        _restart([_record(status=status)])


def test_restart_rejects_when_any_task_is_unfinished():
    records = [
        _record(status=ManagedJobStatus.SUCCEEDED),
        _record(status=ManagedJobStatus.RUNNING)
    ]
    with pytest.raises(ValueError, match='still RUNNING'):
        _restart(records)


def test_restart_accepts_string_status():
    _launched_dag(_restart([_record(status='FAILED_SETUP')]))


def test_restart_job_not_found():
    with pytest.raises(ValueError, match='not found'):
        _restart([])


def test_restart_needs_exactly_one_of_id_and_name():
    with pytest.raises(ValueError, match='exactly one'):
        jobs_core.restart()
    with pytest.raises(ValueError, match='exactly one'):
        jobs_core.restart(job_id=5, job_name='train')


def test_restart_by_name_uses_the_exact_match():
    # name_match is a substring match: 'train' also matches 'train-big'.
    records = [_record(job_id=5), _record(job_id=6, name='train-big')]
    mock_launch = _restart(records, job_id=None, job_name='train')
    assert re.fullmatch(r'train-rerun-[0-9a-f]{6}',
                        mock_launch.call_args.kwargs['name'])


def test_restart_by_name_rejects_an_ambiguous_name():
    records = [_record(job_id=5), _record(job_id=8)]
    with pytest.raises(ValueError, match=r'IDs: 5, 8'):
        _restart(records, job_id=None, job_name='train')


def test_restart_without_stored_spec():
    # Jobs submitted by a controller that predates restart support.
    record = _record()
    record.dag_yaml = None
    with pytest.raises(exceptions.NotSupportedError, match='not available'):
        _restart([record])


@pytest.mark.parametrize(('persistent', 'allowed'), [(False, False),
                                                     (True, True)])
def test_restart_and_temporary_storage(persistent, allowed):
    # Local files were uploaded to a temporary bucket, deleted when the job
    # finished; persistent storage is still there.
    storage = mock.MagicMock(persistent=persistent)
    dag = dag_utils.load_dag_from_yaml_str(_dag_yaml(_train_task()))
    dag.tasks[0].storage_mounts = {'/data': storage}
    with mock.patch.object(jobs_core.dag_utils,
                           'load_dag_from_yaml_str',
                           return_value=dag):
        if allowed:
            _launched_dag(_restart([_record()]))
        else:
            with pytest.raises(exceptions.NotSupportedError,
                               match='copied for that run only'):
                _restart([_record()])


def _restart_with(mutate):
    """Restarts a job whose stored DAG has been changed by `mutate`."""
    dag = dag_utils.load_dag_from_yaml_str(_dag_yaml(_train_task()))
    mutate(dag.tasks[0])
    with mock.patch.object(jobs_core.dag_utils,
                           'load_dag_from_yaml_str',
                           return_value=dag):
        return _restart([_record()])


@pytest.mark.parametrize(
    'mutate',
    [
        # Two-hop staging on a separate controller (no storage cloud): the
        # workdir became a file mount from a controller path.
        lambda t: t.set_file_mounts(
            {'~/sky_workdir': '~/.sky/tmp/file-mounts-abc/0'}),
        # Consolidation mode: the workdir stays a local path on the server.
        lambda t: setattr(t, 'workdir', '/tmp/blobs/abc/workdir'),
    ],
    ids=['two_hop_file_mount', 'consolidation_local_workdir'])
def test_restart_rejects_staged_local_files(mutate):
    with pytest.raises(exceptions.NotSupportedError,
                       match='copied for that run only'):
        _restart_with(mutate)


@pytest.mark.parametrize('mutate', [
    lambda t: t.set_file_mounts({'/data': 's3://my-bucket/data'}),
    lambda t: setattr(t, 'workdir', {
        'url': 'https://github.com/org/repo.git',
        'ref': 'abc123'
    }),
],
                         ids=['bucket_file_mount', 'git_workdir'])
def test_restart_allows_files_that_outlive_the_job(mutate):
    _launched_dag(_restart_with(mutate))


def test_restart_runs_in_the_original_workspace():
    with mock.patch.object(jobs_core, '_client_set_workspace',
                           return_value=False), \
         mock.patch.object(jobs_core.workspaces_core,
                           'check_workspace_permission') as permission, \
         mock.patch.object(jobs_core.skypilot_config,
                           'local_active_workspace_ctx') as workspace_ctx:
        _restart([_record(workspace='team-a')], active_workspace='default')
    permission.assert_called_once()
    assert permission.call_args.args[1] == 'team-a'
    workspace_ctx.assert_called_once_with('team-a')


def test_restart_into_a_requested_workspace():
    # `--workspace` names where the new job goes; the executor authorized it.
    with mock.patch.object(jobs_core, '_client_set_workspace',
                           return_value=True), \
         mock.patch.object(jobs_core.workspaces_core,
                           'check_workspace_permission') as permission, \
         mock.patch.object(jobs_core.skypilot_config,
                           'local_active_workspace_ctx') as workspace_ctx:
        _restart([_record(workspace='team-a')], active_workspace='team-b')
    permission.assert_not_called()
    workspace_ctx.assert_not_called()


@pytest.mark.parametrize(('fields', 'kept'), [
    (None, False),
    (['job_id', 'status'], False),
    (['job_id', 'dag_yaml'], True),
])
def test_queue_v2_returns_dag_yaml_only_when_requested(fields, kept):
    # The compiled DAG carries plaintext secrets.
    jobs = [{'job_id': 1, 'dag_yaml': 'secrets: {K: v}'}]

    @jobs_core._drop_unrequested_dag_yaml
    def fake_queue_v2(refresh, fields=None):
        del refresh, fields
        return [dict(j) for j in jobs], 1, {}, 1, []

    result, _, _, _, _ = fake_queue_v2(False, fields=fields)
    assert ('dag_yaml' in result[0]) == kept


def test_queue_v2_body_drops_dag_yaml_from_client_requests():
    body = payloads.JobsQueueV2Body(fields=['job_id', 'dag_yaml'])
    assert body.to_kwargs()['fields'] == ['job_id']
