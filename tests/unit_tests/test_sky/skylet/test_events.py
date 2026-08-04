"""Tests for provider-aware skylet autodown execution."""

from unittest import mock

import pytest

from sky import clouds
from sky import provision
from sky.adaptors import runpod as runpod_adaptor
from sky.skylet import autostop_lib
from sky.skylet import configs
from sky.skylet import events
from sky.skylet import runtime_utils


class _FakeCloud:
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT

    def __init__(self, uses_ray: bool = False) -> None:
        self._uses_ray = uses_ray

    def uses_ray(self) -> bool:
        return self._uses_ray


@pytest.fixture
def isolated_autostop_storage(tmp_path, monkeypatch):
    database_dir = tmp_path / 'skylet-config'
    database_dir.mkdir()
    monkeypatch.setattr(configs, '_DB_PATH', None)
    monkeypatch.setattr(
        runtime_utils, 'get_runtime_dir_path',
        lambda relative_path: str(database_dir / relative_path.lstrip('/')))
    monkeypatch.setattr(autostop_lib, '_AUTOSTOP_CONFIG_LOCK_PATH',
                        str(tmp_path / 'autostop-config.lock'))


def _store_config(strategy: autostop_lib.AutodownExecutionStrategy,
                  *,
                  down: bool = True,
                  cluster_hash: str = 'cluster-hash',
                  generation: int = 7):
    autostop_lib.set_autostop(
        idle_minutes=10,
        backend=events.cloud_vm_ray_backend.CloudVmRayBackend.NAME,
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=down,
        cluster_hash=cluster_hash,
        generation=generation,
        execution_strategy=strategy,
    )
    return autostop_lib.get_autostop_config()


def _configure_event(monkeypatch,
                     *,
                     provider_name: str,
                     max_workers: int = 0,
                     uses_ray: bool = False,
                     provisioner_version: clouds.ProvisionerVersion = clouds.
                     ProvisionerVersion.SKYPILOT):
    cluster_config = {
        'cluster_name': 'cluster-on-cloud',
        'max_workers': max_workers,
        'provider': {
            'region': 'test-region'
        },
    }
    cloud = _FakeCloud(uses_ray=uses_ray)
    cloud.PROVISIONER_VERSION = provisioner_version
    monkeypatch.setattr(events.yaml_utils, 'read_yaml',
                        lambda _path: cluster_config)
    monkeypatch.setattr(events.cluster_utils, 'get_provider_name',
                        lambda _config: provider_name)
    monkeypatch.setattr(events.registry.CLOUD_REGISTRY, 'from_str',
                        lambda _provider_name: cloud)
    monkeypatch.setattr(autostop_lib, 'set_autostopping_started', mock.Mock())
    subprocess_run = mock.Mock()
    monkeypatch.setattr(events.subprocess, 'run', subprocess_run)
    monkeypatch.setattr(events.cloud_vm_ray_backend,
                        'write_ray_up_script_with_patched_launch_hash_fn',
                        lambda *_args, **_kwargs: '/tmp/ray-up.py')
    terminate_instances = mock.Mock()
    stop_instances = mock.Mock()
    monkeypatch.setattr(provision, 'terminate_instances', terminate_instances)
    monkeypatch.setattr(provision, 'stop_instances', stop_instances)
    terminate_current_pod = mock.Mock()
    monkeypatch.setattr(runpod_adaptor, 'terminate_current_pod',
                        terminate_current_pod)
    for variable in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
                     'AWS_SESSION_TOKEN', 'AWS_SHARED_CREDENTIALS_FILE',
                     'AWS_CONFIG_FILE'):
        monkeypatch.setenv(variable, 'test-value')

    stop_event = object.__new__(events.StopEvent)
    execute_hook = mock.Mock()
    stop_event._execute_hook_if_present = execute_hook
    stop_event._replace_yaml_for_stopping = mock.Mock()
    return (stop_event, execute_hook, subprocess_run, terminate_instances,
            stop_instances, terminate_current_pod)


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_runpod_head_with_server_fallback_invokes_current_pod_termination(
        monkeypatch):
    config = _store_config(
        autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK)
    (stop_event, execute_hook, _, terminate_instances, _,
     terminate_current_pod) = _configure_event(monkeypatch,
                                               provider_name='runpod')

    stop_event._stop_cluster(config)

    execute_hook.assert_called_once_with(config)
    terminate_current_pod.assert_called_once_with()
    terminate_instances.assert_not_called()
    stored = autostop_lib.get_autostop_config()
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.HEAD_TEARDOWN_STARTED)
    assert stored.cluster_hash == 'cluster-hash'
    assert stored.generation == 7


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_server_only_prepares_head_without_partial_provider_teardown(
        monkeypatch):
    config = _store_config(autostop_lib.AutodownExecutionStrategy.SERVER_ONLY)
    (stop_event, execute_hook, subprocess_run, terminate_instances,
     stop_instances, terminate_current_pod) = _configure_event(
         monkeypatch,
         provider_name='aws',
         max_workers=2,
         uses_ray=True,
     )

    stop_event._stop_cluster(config)

    execute_hook.assert_called_once_with(config)
    subprocess_run.assert_called_once()
    terminate_instances.assert_not_called()
    stop_instances.assert_not_called()
    terminate_current_pod.assert_not_called()
    stored = autostop_lib.get_autostop_config()
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
    assert stored.error_summary is None


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_server_only_preparation_failure_records_sanitized_error(monkeypatch):
    config = _store_config(autostop_lib.AutodownExecutionStrategy.SERVER_ONLY)
    (stop_event, _, subprocess_run, terminate_instances, stop_instances,
     terminate_current_pod) = _configure_event(monkeypatch,
                                               provider_name='aws',
                                               uses_ray=True)
    subprocess_run.side_effect = RuntimeError(
        'AWS_SECRET_ACCESS_KEY=secret; provider response')

    stop_event._stop_cluster(config)

    terminate_instances.assert_not_called()
    stop_instances.assert_not_called()
    terminate_current_pod.assert_not_called()
    stored = autostop_lib.get_autostop_config()
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
    assert stored.error_summary == (
        'Head-side teardown preparation failed; server teardown required.')
    assert 'secret' not in stored.error_summary


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_server_only_avoids_legacy_ray_provider_teardown(monkeypatch):
    config = _store_config(autostop_lib.AutodownExecutionStrategy.SERVER_ONLY)
    (stop_event, execute_hook, subprocess_run, terminate_instances,
     stop_instances, _) = _configure_event(
         monkeypatch,
         provider_name='legacy-cloud',
         max_workers=2,
         provisioner_version=clouds.ProvisionerVersion.RAY_AUTOSCALER,
     )

    stop_event._stop_cluster(config)

    execute_hook.assert_called_once_with(config)
    stop_event._replace_yaml_for_stopping.assert_called_once()
    commands = [call.args[0] for call in subprocess_run.call_args_list]
    assert len(commands) == 1
    assert commands[0].endswith(' stop')
    assert all(' down ' not in command for command in commands)
    terminate_instances.assert_not_called()
    stop_instances.assert_not_called()
    assert (autostop_lib.get_autostop_config().durable_execution_state ==
            autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_head_with_server_fallback_keeps_multinode_worker_first_teardown(
        monkeypatch):
    config = _store_config(
        autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK)
    (stop_event, _, _, terminate_instances, _,
     terminate_current_pod) = _configure_event(monkeypatch,
                                               provider_name='aws',
                                               max_workers=2)

    stop_event._stop_cluster(config)

    assert terminate_instances.call_args_list == [
        mock.call(
            provider_name='aws',
            cluster_name_on_cloud='cluster-on-cloud',
            provider_config={'region': 'test-region'},
            worker_only=True,
        ),
        mock.call(
            provider_name='aws',
            cluster_name_on_cloud='cluster-on-cloud',
            provider_config={'region': 'test-region'},
        ),
    ]
    terminate_current_pod.assert_not_called()
    assert (autostop_lib.get_autostop_config().durable_execution_state ==
            autostop_lib.DurableAutodownState.HEAD_TEARDOWN_STARTED)


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_runpod_failure_requests_server_fallback_with_sanitized_error(
        monkeypatch):
    config = _store_config(
        autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK)
    (stop_event, _, _, terminate_instances, _,
     terminate_current_pod) = _configure_event(monkeypatch,
                                               provider_name='runpod')
    terminate_current_pod.side_effect = RuntimeError(
        'RUNPOD_API_KEY=secret; provider response body')

    stop_event._stop_cluster(config)

    terminate_instances.assert_not_called()
    stored = autostop_lib.get_autostop_config()
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
    assert stored.error_summary == (
        'RunPod head teardown failed; server teardown required.')
    assert 'secret' not in stored.error_summary
    assert 'response body' not in stored.error_summary


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_provider_failure_requests_server_fallback_with_sanitized_error(
        monkeypatch):
    config = _store_config(
        autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK)
    (stop_event, _, _, terminate_instances, _,
     _) = _configure_event(monkeypatch, provider_name='aws')
    terminate_instances.side_effect = RuntimeError(
        'AWS_SECRET_ACCESS_KEY=secret; raw provider response')

    stop_event._stop_cluster(config)

    stored = autostop_lib.get_autostop_config()
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
    assert stored.error_summary == (
        'Head-side provider teardown failed; server teardown required.')
    assert 'secret' not in stored.error_summary
    assert 'provider response' not in stored.error_summary


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_post_claim_config_read_failure_requests_server_fallback(monkeypatch):
    config = _store_config(
        autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK)
    (stop_event, _, _, terminate_instances, _,
     terminate_current_pod) = _configure_event(monkeypatch,
                                               provider_name='runpod')
    monkeypatch.setattr(events.yaml_utils, 'read_yaml',
                        mock.Mock(side_effect=RuntimeError('secret config')))

    stop_event._stop_cluster(config)

    terminate_instances.assert_not_called()
    terminate_current_pod.assert_not_called()
    stored = autostop_lib.get_autostop_config()
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
    assert stored.error_summary == (
        'Head-side teardown preparation failed; server teardown required.')
    assert 'secret' not in stored.error_summary


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_post_claim_new_provisioner_ray_failure_requests_server_fallback(
        monkeypatch):
    config = _store_config(
        autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK)
    (stop_event, _, subprocess_run, terminate_instances, _,
     terminate_current_pod) = _configure_event(
         monkeypatch,
         provider_name='runpod',
         uses_ray=True,
         provisioner_version=(
             clouds.ProvisionerVersion.RAY_PROVISIONER_SKYPILOT_TERMINATOR),
     )
    subprocess_run.side_effect = RuntimeError('RUNPOD_API_KEY=secret')

    stop_event._stop_cluster(config)

    terminate_instances.assert_not_called()
    terminate_current_pod.assert_not_called()
    stored = autostop_lib.get_autostop_config()
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
    assert stored.error_summary == (
        'Head-side teardown preparation failed; server teardown required.')
    assert 'secret' not in stored.error_summary


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_legacy_ray_provider_failure_requests_sanitized_server_fallback(
        monkeypatch):
    config = _store_config(
        autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK)
    (stop_event, _, subprocess_run, _, _, _) = _configure_event(
        monkeypatch,
        provider_name='legacy-cloud',
        provisioner_version=clouds.ProvisionerVersion.RAY_AUTOSCALER,
    )
    subprocess_run.side_effect = [
        None,
        RuntimeError('API_KEY=secret; raw provider response'),
    ]

    stop_event._stop_cluster(config)

    stored = autostop_lib.get_autostop_config()
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
    assert stored.error_summary == (
        'Head-side provider teardown failed; server teardown required.')
    assert 'secret' not in stored.error_summary


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_stale_durable_config_does_not_start_provider_teardown(monkeypatch):
    stale_config = _store_config(
        autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK,
        cluster_hash='stale-hash',
        generation=1,
    )
    _store_config(
        autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK,
        cluster_hash='current-hash',
        generation=2,
    )
    (stop_event, execute_hook, subprocess_run, terminate_instances, _,
     terminate_current_pod) = _configure_event(monkeypatch,
                                               provider_name='runpod')

    stop_event._stop_cluster(stale_config)

    execute_hook.assert_not_called()
    stop_event._replace_yaml_for_stopping.assert_not_called()
    subprocess_run.assert_not_called()
    terminate_instances.assert_not_called()
    terminate_current_pod.assert_not_called()
    stored = autostop_lib.get_autostop_config()
    assert stored.cluster_hash == 'current-hash'
    assert stored.generation == 2
    assert (stored.durable_execution_state ==
            autostop_lib.DurableAutodownState.ARMED)


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_cancelled_server_only_config_skips_all_head_preparation(monkeypatch):
    stale_config = _store_config(
        autostop_lib.AutodownExecutionStrategy.SERVER_ONLY,
        cluster_hash='cluster-hash',
        generation=7,
    )
    autostop_lib.set_autostop(
        idle_minutes=-1,
        backend=None,
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        cluster_hash='cluster-hash',
        generation=8,
        execution_strategy=autostop_lib.AutodownExecutionStrategy.SERVER_ONLY,
    )
    (stop_event, execute_hook, subprocess_run, terminate_instances,
     stop_instances,
     terminate_current_pod) = _configure_event(monkeypatch,
                                               provider_name='aws',
                                               uses_ray=True)

    stop_event._stop_cluster(stale_config)

    execute_hook.assert_not_called()
    stop_event._replace_yaml_for_stopping.assert_not_called()
    subprocess_run.assert_not_called()
    terminate_instances.assert_not_called()
    stop_instances.assert_not_called()
    terminate_current_pod.assert_not_called()


@pytest.mark.usefixtures('isolated_autostop_storage')
@pytest.mark.parametrize(
    ('strategy', 'provider_name'),
    [
        (autostop_lib.AutodownExecutionStrategy.SERVER_ONLY, 'aws'),
        (autostop_lib.AutodownExecutionStrategy.HEAD_WITH_SERVER_FALLBACK,
         'runpod'),
    ],
)
def test_cancelled_strict_new_provisioner_skips_all_head_side_effects(
        monkeypatch, strategy, provider_name):
    stale_config = _store_config(strategy,
                                 cluster_hash='cluster-hash',
                                 generation=7)
    autostop_lib.set_autostop(
        idle_minutes=-1,
        backend=None,
        wait_for=autostop_lib.AutostopWaitFor.JOBS_AND_SSH,
        down=True,
        cluster_hash='cluster-hash',
        generation=8,
        execution_strategy=strategy,
    )
    (stop_event, execute_hook, subprocess_run, terminate_instances,
     stop_instances, terminate_current_pod) = _configure_event(
         monkeypatch,
         provider_name=provider_name,
         uses_ray=True,
         provisioner_version=(
             clouds.ProvisionerVersion.RAY_PROVISIONER_SKYPILOT_TERMINATOR),
     )

    stop_event._stop_cluster(stale_config)

    execute_hook.assert_not_called()
    stop_event._replace_yaml_for_stopping.assert_not_called()
    subprocess_run.assert_not_called()
    terminate_instances.assert_not_called()
    stop_instances.assert_not_called()
    terminate_current_pod.assert_not_called()


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_legacy_head_credentials_preserves_provider_exception(monkeypatch):
    config = _store_config(
        autostop_lib.AutodownExecutionStrategy.LEGACY_HEAD_CREDENTIALS)
    (stop_event, _, _, terminate_instances, _,
     _) = _configure_event(monkeypatch, provider_name='aws')
    provider_error = RuntimeError('legacy provider error')
    terminate_instances.side_effect = provider_error

    with pytest.raises(RuntimeError) as error:
        stop_event._stop_cluster(config)

    assert error.value is provider_error
    assert (autostop_lib.get_autostop_config().durable_execution_state ==
            autostop_lib.DurableAutodownState.ARMED)


@pytest.mark.usefixtures('isolated_autostop_storage')
def test_non_down_autostop_ignores_durable_strategy(monkeypatch):
    config = _store_config(autostop_lib.AutodownExecutionStrategy.SERVER_ONLY,
                           down=False)
    (stop_event, _, _, terminate_instances, stop_instances,
     terminate_current_pod) = _configure_event(monkeypatch,
                                               provider_name='aws',
                                               max_workers=1)

    stop_event._stop_cluster(config)

    terminate_instances.assert_not_called()
    terminate_current_pod.assert_not_called()
    assert stop_instances.call_args_list == [
        mock.call(
            provider_name='aws',
            cluster_name_on_cloud='cluster-on-cloud',
            provider_config={'region': 'test-region'},
            worker_only=True,
        ),
        mock.call(
            provider_name='aws',
            cluster_name_on_cloud='cluster-on-cloud',
            provider_config={'region': 'test-region'},
        ),
    ]
    assert (autostop_lib.get_autostop_config().durable_execution_state ==
            autostop_lib.DurableAutodownState.UNSPECIFIED)
