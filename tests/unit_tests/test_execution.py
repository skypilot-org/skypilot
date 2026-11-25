"""Unit tests for execution internals."""

from unittest import mock

import pytest

from sky import backends
from sky import clouds
from sky import dag as dag_lib
from sky import execution
from sky import resources as resources_lib
from sky import task as task_lib


def _execute_dag(task,
                 backend,
                 handle,
                 *,
                 jobs_controller,
                 stages,
                 dryrun=False):
    dag = dag_lib.Dag()
    dag.add(task)
    return execution._execute_dag(
        dag,
        dryrun=dryrun,
        stream_logs=False,
        handle=handle,
        backend=backend,
        retry_until_up=False,
        optimize_target=execution.common.OptimizeTarget.COST,
        stages=stages,
        cluster_name=None,
        detach_setup=False,
        no_setup=False,
        clone_disk_from=None,
        skip_unnecessary_provisioning=False,
        resize=False,
        _quiet_optimizer=True,
        _is_launched_by_jobs_controller=jobs_controller,
        _is_launched_by_sky_serve_controller=False,
        _extra_launch_context={})


@pytest.mark.parametrize(
    'jobs_controller,expected_feature',
    [(False, clouds.CloudImplementationFeatures.AUTODOWN), (True, None)],
)
def test_managed_job_autodown_does_not_constrain_cloud_selection(
        monkeypatch, jobs_controller, expected_feature):
    resource = resources_lib.Resources(cloud=clouds.AWS(),
                                       autostop={
                                           'idle_minutes': 10,
                                           'down': True
                                       })
    task = task_lib.Task().set_resources(resource)
    task.best_resources = resource
    backend = backends.CloudVmRayBackend()
    backend.register_info = mock.MagicMock()
    backend.provision = mock.MagicMock(return_value=(None, False))

    _execute_dag(task,
                 backend,
                 None,
                 jobs_controller=jobs_controller,
                 stages=[execution.Stage.PROVISION],
                 dryrun=True)

    features = backend.register_info.call_args.kwargs['requested_features']
    if expected_feature is None:
        assert clouds.CloudImplementationFeatures.AUTODOWN not in features
        assert clouds.CloudImplementationFeatures.AUTOSTOP not in features
    else:
        assert expected_feature in features


@pytest.mark.parametrize('cloud,supported', [(clouds.AWS(), True),
                                             (clouds.Verda(), False)])
def test_managed_job_autodown_is_applied_after_cloud_selection(
        monkeypatch, cloud, supported):
    resource = resources_lib.Resources(cloud=cloud,
                                       instance_type='test-instance',
                                       autostop={
                                           'idle_minutes': 10,
                                           'down': True
                                       })
    task = task_lib.Task().set_resources(resource)
    task.best_resources = resource
    backend = backends.CloudVmRayBackend()
    backend.register_info = mock.MagicMock()
    backend.set_autostop = mock.MagicMock()

    class FakeHandle:
        launched_resources = resource

    monkeypatch.setattr(backends, 'CloudVmRayResourceHandle', FakeHandle)
    _execute_dag(task,
                 backend,
                 FakeHandle(),
                 jobs_controller=True,
                 stages=[execution.Stage.PRE_EXEC])

    assert backend.set_autostop.called is supported


@pytest.mark.parametrize('resources', [
    [resources_lib.Resources(autostop={
        'idle_minutes': 10,
        'down': True
    })],
    [
        resources_lib.Resources(cloud=clouds.Verda(),
                                autostop={
                                    'idle_minutes': 10,
                                    'down': True
                                }),
        resources_lib.Resources(cloud=clouds.AWS(),
                                autostop={
                                    'idle_minutes': 10,
                                    'down': True
                                }),
    ],
])
def test_managed_job_implicit_and_fallback_candidates_remain_feasible(
        resources):
    task = task_lib.Task().set_resources(resources)
    task.best_resources = resources[0]
    backend = backends.CloudVmRayBackend()
    backend.register_info = mock.MagicMock()
    backend.provision = mock.MagicMock(return_value=(None, False))

    _execute_dag(task,
                 backend,
                 None,
                 jobs_controller=True,
                 stages=[execution.Stage.PROVISION],
                 dryrun=True)

    features = backend.register_info.call_args.kwargs['requested_features']
    assert clouds.CloudImplementationFeatures.AUTODOWN not in features
    assert clouds.CloudImplementationFeatures.AUTOSTOP not in features


def test_unsupported_managed_job_autodown_still_persists_hooks(monkeypatch):
    hooks = [{'events': ['down'], 'run': 'echo cleanup'}]
    resource = resources_lib.Resources(cloud=clouds.Verda(),
                                       instance_type='test-instance',
                                       autostop={
                                           'idle_minutes': 10,
                                           'down': True
                                       },
                                       hooks=hooks)
    task = task_lib.Task().set_resources(resource)
    task.best_resources = resource
    backend = backends.CloudVmRayBackend()
    backend.register_info = mock.MagicMock()
    backend.set_autostop = mock.MagicMock()

    class FakeHandle:
        cluster_name = 'worker'
        launched_resources = resource

    normalized_hooks = resource.hooks
    assert normalized_hooks is not None
    hooks_only_args = {'idle_minutes': -1, 'hooks': normalized_hooks}
    compute_args = mock.MagicMock(return_value=hooks_only_args)
    monkeypatch.setattr(backends, 'CloudVmRayResourceHandle', FakeHandle)
    monkeypatch.setattr(execution,
                        '_compute_set_autostop_args_for_hooks_only_relaunch',
                        compute_args)

    handle = FakeHandle()
    _execute_dag(task,
                 backend,
                 handle,
                 jobs_controller=True,
                 stages=[execution.Stage.PRE_EXEC])

    compute_args.assert_called_once_with('worker', normalized_hooks)
    backend.set_autostop.assert_called_once_with(handle, **hooks_only_args)
