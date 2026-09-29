"""Finite Modal lifetimes preserve admission time through delayed startup."""

import inspect
import pathlib
import shlex
import signal
import subprocess
import time
from types import SimpleNamespace
from unittest import mock

import jinja2
import psutil
import pytest
import yaml

from sky import clouds
from sky import exceptions
from sky import skypilot_config
from sky import task
from sky.client import sdk
from sky.provision import common
from sky.provision.modal import deadline
from sky.provision.modal import instance
from sky.provision.modal import modal_utils
from sky.server import constants as server_constants
from sky.server.requests import payloads
from sky.utils import config_utils
from sky.utils import dag_utils
from sky.utils import resources_utils


@pytest.mark.parametrize(
    'value',
    [True, False, '100', 0, -1,
     float('nan'), float('inf')])
def test_invalid_deadline(value):
    with pytest.raises(ValueError, match='finite Unix'):
        deadline.validate(value)


def test_rendered_task_binds_deadline(monkeypatch):
    monkeypatch.setattr(clouds.Modal, '_get_active_environment_name',
                        classmethod(lambda cls: 'test'))
    config = {
        'resources': {
            'infra': 'modal',
            'instance_type': '4CPU--16GB'
        },
        'config': {
            'modal': {
                'deadline': 200.75
            }
        }
    }
    value = task.Task.from_yaml_config(config)
    resource = next(iter(value.resources))
    clouds.Modal.check_features_are_supported(
        resource, {clouds.CloudImplementationFeatures.AUTODOWN})
    variables = clouds.Modal().make_deploy_resources_variables(
        resource, resources_utils.ClusterName('test', 'test'),
        clouds.Region('auto'), None, 1)
    template = pathlib.Path(
        __file__).parents[2] / 'sky/templates/modal-ray.yml.j2'
    rendered = yaml.safe_load(
        jinja2.Template(template.read_text()).render(**variables,
                                                     credentials={}))
    node = rendered['available_node_types']['ray_head_default']['node_config']
    assert node['Deadline'] == 200.75
    assert node['Timeout'] == 86400


@pytest.fixture
def provider(monkeypatch):
    sandbox = mock.Mock(object_id='sb-accepted')
    sandbox.poll.return_value = None
    sandbox.get_tags.return_value = {deadline.TAG: '200.75'}
    create = mock.Mock(return_value=sandbox)
    lookup = mock.Mock(return_value={})
    tunnel = mock.Mock(return_value=('host', 22))
    monkeypatch.setattr(
        instance, 'modal_adaptor',
        SimpleNamespace(modal=SimpleNamespace(Sandbox=SimpleNamespace(
            create=create))))
    monkeypatch.setattr(modal_utils, 'get_active_sandboxes_by_name', lookup)
    monkeypatch.setattr(modal_utils, 'get_app', lambda **_: 'app')
    monkeypatch.setattr(modal_utils, 'get_image', lambda _: 'image')
    monkeypatch.setattr(modal_utils, 'get_modal_env_secret', lambda: None)
    monkeypatch.setattr(modal_utils, 'get_ssh_tunnel', tunnel)
    monkeypatch.setattr(deadline.time, 'time', lambda: 100)
    config = common.ProvisionConfig(provider_config={},
                                    authentication_config={},
                                    docker_config={},
                                    node_config={
                                        'PublicKey': 'public',
                                        'Timeout': 86400,
                                        'Deadline': 200.75
                                    },
                                    count=1,
                                    tags={},
                                    resume_stopped_nodes=False,
                                    ports_to_open_on_launch=[])
    return sandbox, create, lookup, tunnel, config


def run(config):
    return instance.run_instances('auto', 'test', 'test-on-cloud', config)


def test_provider_recomputes_budget_after_preparation(provider, monkeypatch):
    sandbox, create, _, tunnel, config = provider
    monkeypatch.setattr(
        modal_utils, 'get_image', lambda _: monkeypatch.setattr(
            deadline.time, 'time', lambda: 130) or 'image')
    assert run(config).head_instance_id == sandbox.object_id
    assert create.call_args.kwargs['timeout'] == 70
    assert create.call_args.kwargs['tags'][deadline.TAG] == '200.75'
    assert create.call_args.args[-2] == '200.75'
    tunnel.assert_called_once_with(sandbox, timeout=70)


def test_expired_before_request_does_not_create(provider, monkeypatch):
    _, create, lookup, _, config = provider
    monkeypatch.setattr(deadline.time, 'time', lambda: 201)
    with pytest.raises(TimeoutError):
        run(config)
    create.assert_not_called()
    lookup.assert_not_called()


def test_expired_after_preparation_does_not_create(provider, monkeypatch):
    _, create, _, _, config = provider
    monkeypatch.setattr(
        modal_utils, 'get_image', lambda _: monkeypatch.setattr(
            deadline.time, 'time', lambda: 201) or 'image')
    with pytest.raises(TimeoutError):
        run(config)
    create.assert_not_called()


@pytest.mark.parametrize('failure', [TimeoutError, KeyboardInterrupt])
def test_accepted_id_is_logged_before_readiness_failure(provider, monkeypatch,
                                                        failure):
    sandbox, create, _, tunnel, config = provider
    logs = []
    monkeypatch.setattr(instance.logger, 'info', logs.append)
    tunnel.side_effect = failure
    with pytest.raises(failure):
        run(config)
    assert any(sandbox.object_id in message for message in logs)
    create.assert_called_once()
    # No success record, no retry, and no fabricated termination receipt.
    sandbox.terminate.assert_not_called()


def test_same_deadline_reuses_without_renewal(provider):
    sandbox, create, lookup, _, config = provider
    lookup.return_value = {sandbox.object_id: sandbox}
    record = run(config)
    assert record.created_instance_ids == []
    assert record.head_instance_id == sandbox.object_id
    create.assert_not_called()


@pytest.mark.parametrize('existing', [None, '201', '100'])
def test_existing_deadline_cannot_change(provider, existing):
    sandbox, create, lookup, _, config = provider
    sandbox.get_tags.return_value = {deadline.TAG: existing}
    lookup.return_value = {sandbox.object_id: sandbox}
    with pytest.raises(RuntimeError, match='Cannot change'):
        run(config)
    create.assert_not_called()


def test_delayed_entrypoint_does_not_start_ssh_or_setup(tmp_path):
    marker = tmp_path / 'started'
    result = subprocess.run(deadline.command(
        f'touch {shlex.quote(str(marker))}',
        time.time() - 1),
                            check=False)
    assert result.returncode == 124
    assert not marker.exists()


@pytest.mark.parametrize('cancel', [False, True])
def test_entrypoint_expiry_and_cancel_end_child_group(tmp_path, cancel):
    marker = tmp_path / 'started'
    script = (f'echo $$ > {shlex.quote(str(marker))}; sleep 30 & '
              f'echo $! >> {shlex.quote(str(marker))}; wait')
    start = time.monotonic()
    process = subprocess.Popen(deadline.command(script, time.time() + 0.7))
    try:
        while time.monotonic() - start < 2:
            if marker.exists() and len(marker.read_text().splitlines()) == 2:
                break
            time.sleep(0.01)
        children = [int(pid) for pid in marker.read_text().splitlines()]
        assert len(children) == 2
        if cancel:
            process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=3) == (143 if cancel else 124)
        assert time.monotonic() - start < 3
        for pid in children:
            try:
                assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_entrypoint_preserves_early_exit():
    result = subprocess.run(deadline.command('exit 7',
                                             time.time() + 10),
                            check=False)
    assert result.returncode == 7


def test_expiry_after_create_retains_identity_without_replay(
        provider, monkeypatch):
    sandbox, create, _, tunnel, config = provider
    logs = []
    monkeypatch.setattr(instance.logger, 'info', logs.append)

    def accepted(*args, **kwargs):
        monkeypatch.setattr(deadline.time, 'time', lambda: 201)
        return sandbox

    create.side_effect = accepted
    with pytest.raises(TimeoutError):
        run(config)
    assert any(sandbox.object_id in message for message in logs)
    create.assert_called_once()
    tunnel.assert_not_called()


def test_down_reconciles_accepted_but_not_ready_sandbox(provider):
    sandbox, create, lookup, _, _ = provider
    lookup.return_value = {sandbox.object_id: sandbox}
    instance.terminate_instances('test-on-cloud', {'environment_name': 'test'})
    sandbox.terminate.assert_called_once_with(wait=True)
    create.assert_not_called()


@pytest.mark.parametrize('api_version', [None, 24, 64, 65])
@pytest.mark.parametrize('operation', ['validate', 'optimize', '_launch'])
def test_deadline_rejected_before_old_peer_request(api_version, operation,
                                                   monkeypatch):
    dag = dag_utils.convert_entrypoint_to_dag(
        task.Task.from_yaml_config({
            'resources': {
                'infra': 'modal'
            },
            'config': {
                'modal': {
                    'deadline': 2000000000.75
                }
            },
        }))
    monkeypatch.setattr(sdk.versions, 'get_remote_api_version',
                        lambda: api_version)
    request = mock.Mock(side_effect=AssertionError('unexpected API request'))
    monkeypatch.setattr(sdk.server_common, 'make_authenticated_request',
                        request)
    validate = inspect.unwrap(sdk.validate)
    monkeypatch.setattr(sdk, 'validate', validate)
    args = (dag, 'deadline-test', None) if operation == '_launch' else (dag,)
    with pytest.raises(exceptions.APINotSupportedError,
                       match='Modal Sandbox deadlines.*API_VERSION'):
        inspect.unwrap(getattr(sdk, operation))(*args)
    request.assert_not_called()


@pytest.mark.parametrize('deadline_value', [None, 2000000000.75])
def test_deadline_compatible_peer_and_unselected_feature(
        deadline_value, monkeypatch):
    config = {} if deadline_value is None else {
        'modal': {
            'deadline': deadline_value
        }
    }
    dag = dag_utils.convert_entrypoint_to_dag(
        task.Task.from_yaml_config({
            'resources': {
                'infra': 'modal'
            },
            'config': config
        }))
    monkeypatch.setattr(
        sdk.versions, 'get_remote_api_version', lambda: None if deadline_value
        is None else server_constants.MIN_MODAL_SANDBOX_DEADLINE_API_VERSION)
    request = mock.Mock(side_effect=RuntimeError('supported server request'))
    monkeypatch.setattr(sdk.server_common, 'make_authenticated_request',
                        request)
    with pytest.raises(RuntimeError, match='supported server request'):
        inspect.unwrap(sdk.validate)(dag)
    request.assert_called_once()


@pytest.mark.parametrize('infra', ['modal', None])
def test_global_deadline_also_rejects_old_peer(infra, monkeypatch):
    dag = dag_utils.convert_entrypoint_to_dag(
        task.Task.from_yaml_config({'resources': {
            'infra': infra
        }}))
    monkeypatch.setattr(sdk.versions, 'get_remote_api_version', lambda: 65)
    request = mock.Mock(side_effect=AssertionError('unexpected API request'))
    monkeypatch.setattr(sdk.server_common, 'make_authenticated_request',
                        request)
    with skypilot_config.replace_skypilot_config(
            config_utils.Config({'modal': {
                'deadline': 2000000000.75
            }})):
        with pytest.raises(exceptions.APINotSupportedError,
                           match='Modal Sandbox deadlines'):
            inspect.unwrap(sdk.validate)(dag)
    request.assert_not_called()


@pytest.mark.parametrize('infra', ['kubernetes', 'aws'])
@pytest.mark.parametrize('api_version', [None, 64, 65])
def test_unrelated_global_deadline_allows_non_modal(infra, api_version,
                                                    monkeypatch):
    dag = dag_utils.convert_entrypoint_to_dag(
        task.Task.from_yaml_config({'resources': {
            'infra': infra
        }}))
    monkeypatch.setattr(sdk.versions, 'get_remote_api_version',
                        lambda: api_version)
    request = mock.Mock(side_effect=RuntimeError('supported server request'))
    monkeypatch.setattr(sdk.server_common, 'make_authenticated_request',
                        request)
    with skypilot_config.replace_skypilot_config(
            config_utils.Config({'modal': {
                'deadline': 2000000000.75
            }})):
        with pytest.raises(RuntimeError, match='supported server request'):
            inspect.unwrap(sdk.validate)(dag)
    request.assert_called_once()


@pytest.mark.parametrize('infra', ['modal', None])
@pytest.mark.parametrize('value', [None, 2000000000.75])
@pytest.mark.parametrize('peer', [None, 65, 66])
def test_client_guard_preserves_server_plugin_overrides(infra, value, peer,
                                                        monkeypatch):
    config = {'server_plugin': {'option': 'value'}}
    if value is not None:
        config['modal'] = {'deadline': value}
    dag = dag_utils.convert_entrypoint_to_dag(
        task.Task.from_yaml_config({
            'resources': {
                'infra': infra
            },
            'config': config
        }))
    monkeypatch.setattr(sdk.versions, 'get_remote_api_version', lambda: peer)
    request = mock.Mock(side_effect=RuntimeError('supported server request'))
    monkeypatch.setattr(sdk.server_common, 'make_authenticated_request',
                        request)
    if value is not None and peer != 66:
        with pytest.raises(exceptions.APINotSupportedError):
            inspect.unwrap(sdk.validate)(dag)
        request.assert_not_called()
    else:
        with pytest.raises(RuntimeError, match='supported server request'):
            inspect.unwrap(sdk.validate)(dag)
        request.assert_called_once()


@pytest.mark.parametrize('api_version', [None, 64, 65])
@pytest.mark.parametrize(
    'body_class',
    [payloads.LaunchBody, payloads.ExecBody, payloads.OptimizeBody])
def test_server_rejects_old_client_before_resource_work(api_version, body_class,
                                                        monkeypatch):
    raw = yaml.safe_dump({
        'resources': {
            'infra': 'modal'
        },
        'config': {
            'modal': {
                'deadline': 2000000000.75
            }
        }
    })
    if body_class is payloads.OptimizeBody:
        body = body_class(dag=raw,
                          request_options=None,
                          client_api_version=api_version)
    else:
        body = body_class(task=raw,
                          cluster_name='deadline-test',
                          client_api_version=api_version)
    mkdir = mock.Mock(side_effect=AssertionError('unexpected mount mutation'))
    monkeypatch.setattr(pathlib.Path, 'mkdir', mkdir)
    with pytest.raises(exceptions.APINotSupportedError,
                       match='Modal Sandbox deadlines'):
        body.to_kwargs()
    mkdir.assert_not_called()


def test_server_accepts_supported_client_before_provider(monkeypatch):
    raw = yaml.safe_dump({
        'resources': {
            'infra': 'modal'
        },
        'config': {
            'modal': {
                'deadline': 2000000000.75
            }
        }
    })
    body = payloads.LaunchBody(task=raw,
                               cluster_name='deadline-test',
                               client_api_version=server_constants.
                               MIN_MODAL_SANDBOX_DEADLINE_API_VERSION)
    mkdir = mock.Mock(side_effect=RuntimeError('compatible mount preparation'))
    monkeypatch.setattr(pathlib.Path, 'mkdir', mkdir)
    with pytest.raises(RuntimeError, match='compatible mount preparation'):
        body.to_kwargs()
    mkdir.assert_called_once()
