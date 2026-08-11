"""Tests for Vast provisioning failure handling."""
# pylint: disable=protected-access

import logging
import traceback
from unittest import mock

import pytest

from sky import exceptions
from sky.provision import common
from sky.provision.vast import instance as vast_instance
from sky.provision.vast import utils as vast_utils
from sky.utils import resources_utils
from sky.utils import status_lib


def _instance(instance_id: str,
              status: str,
              ssh_port=None,
              machine_id=None,
              status_msg=None):
    return {
        'id': instance_id,
        'name': 'test-head',
        'status': status,
        'ssh_port': ssh_port,
        'machine_id': machine_id,
        'status_msg': status_msg,
    }


def _provision_config() -> common.ProvisionConfig:
    return common.ProvisionConfig(
        provider_config={'provision_timeout': 30},
        authentication_config={},
        docker_config={},
        node_config={
            'ImageId': 'vastai/base:0.0.2',
            'InstanceType': '1x-A100-4-8192',
            'DiskSize': 30,
            'Preemptible': False,
        },
        count=1,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )


def _launch_vast(network_tier: resources_utils.NetworkTier,
                 reliable_hosts: bool = False) -> str:
    return vast_utils.launch(
        name='test-head',
        instance_type='1x-A100-4-8192',
        region='US',
        disk_size=30,
        image_name='vastai/base:0.0.2',
        ports=None,
        preemptible=False,
        secure_only=False,
        reliable_hosts=reliable_hosts,
        network_tier=network_tier,
    )


def _mock_vast_sdk(monkeypatch):
    sdk = mock.MagicMock()
    sdk.search_offers.return_value = [{
        'id': 1,
        'machine_id': 2,
    }]
    sdk.create_instance.return_value = {'new_contract': '3'}
    sdk.show_instance.return_value = {'id': '3'}
    monkeypatch.setattr(vast_utils.vast, 'vast', lambda: sdk)
    return sdk


def test_launch_best_network_tier_filters_symmetric_bandwidth(monkeypatch):
    sdk = _mock_vast_sdk(monkeypatch)

    assert _launch_vast(resources_utils.NetworkTier.BEST) == '3'

    query = sdk.search_offers.call_args.kwargs['query']
    assert 'inet_down>=1000' in query
    assert 'inet_up>=1000' in query


def test_launch_standard_network_tier_preserves_reliable_host_filter(
        monkeypatch):
    sdk = _mock_vast_sdk(monkeypatch)

    _launch_vast(resources_utils.NetworkTier.STANDARD, reliable_hosts=True)

    query = sdk.search_offers.call_args.kwargs['query']
    assert query.count('inet_down>=1000') == 1
    assert 'inet_up>=1000' not in query


@pytest.mark.parametrize('registry_key', ['login', 'image_login'])
def test_launch_rejects_direct_registry_login_override_before_offer_query(
        monkeypatch, registry_key):
    sdk = _mock_vast_sdk(monkeypatch)

    with pytest.raises(ValueError, match='SKYPILOT_DOCKER'):
        vast_utils.launch(
            name='test-head',
            instance_type='1x-A100-4-8192',
            region='US',
            disk_size=30,
            image_name='registry.example.com/team/image:latest',
            ports=None,
            preemptible=False,
            secure_only=False,
            reliable_hosts=False,
            network_tier=resources_utils.NetworkTier.STANDARD,
            private_docker_registry=True,
            login='-u registry-user -p registry-password registry.example.com',
            create_instance_kwargs={registry_key: 'direct-login'},
        )

    sdk.search_offers.assert_not_called()
    sdk.create_instance.assert_not_called()


def test_run_instances_rejects_whitespace_registry_credentials_before_wait(
        monkeypatch):
    configuration = _provision_config()
    configuration.provider_config['docker_login_config'] = {
        'username': 'registry user',
        'password': 'registry-password',
        'server': 'registry.example.com',
    }
    wait_for_pending_instances = mock.Mock(return_value={})
    monkeypatch.setattr(vast_instance, '_wait_for_no_pending_instances',
                        wait_for_pending_instances)
    monkeypatch.setattr(vast_utils, 'launch',
                        mock.Mock(return_value='instance-1'))
    monkeypatch.setattr(vast_instance, '_wait_for_instances_ready',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        vast_utils, 'list_instances',
        lambda: {'instance-1': _instance('instance-1', 'RUNNING')})

    with pytest.raises(ValueError, match='whitespace'):
        vast_instance.run_instances('US', 'test', 'test', configuration)

    wait_for_pending_instances.assert_not_called()


def test_run_instances_passes_generated_registry_login_to_vast(monkeypatch):
    configuration = _provision_config()
    configuration.node_config['ImageId'] = 'team/image:latest'
    configuration.provider_config['docker_login_config'] = {
        'username': 'registry-user',
        'password': 'registry-password',
        'server': 'registry.example.com',
    }
    monkeypatch.setattr(vast_instance, '_wait_for_no_pending_instances',
                        lambda *_args, **_kwargs: {})
    launch = mock.Mock(return_value='instance-1')
    monkeypatch.setattr(vast_utils, 'launch', launch)
    monkeypatch.setattr(vast_instance, '_wait_for_instances_ready',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        vast_utils, 'list_instances',
        lambda: {'instance-1': _instance('instance-1', 'RUNNING')})

    vast_instance.run_instances('US', 'test', 'test', configuration)

    launch_kwargs = launch.call_args.kwargs
    assert launch_kwargs[
        'image_name'] == 'registry.example.com/team/image:latest'
    assert launch_kwargs['login'] == (
        '-u registry-user -p registry-password registry.example.com')
    assert launch_kwargs['private_docker_registry'] is True


def test_run_instances_redacts_registry_password_from_launch_failure(
        monkeypatch, caplog):
    configuration = _provision_config()
    configuration.provider_config['docker_login_config'] = {
        'username': 'registry-user',
        'password': 'registry-password',
        'server': 'registry.example.com',
    }
    monkeypatch.setattr(vast_instance, '_wait_for_no_pending_instances',
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        vast_utils, 'launch',
        mock.Mock(side_effect=RuntimeError(
            'Vast rejected -u registry-user -p registry-password '
            'registry.example.com')))

    with caplog.at_level(logging.WARNING), pytest.raises(
            exceptions.VastProvisioningError) as exc_info:
        vast_instance.run_instances('US', 'test', 'test', configuration)

    formatted_exception = ''.join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb))
    assert 'registry-password' not in str(exc_info.value)
    assert 'registry-password' not in formatted_exception
    assert 'registry-password' not in caplog.text
    assert '<redacted>' in str(exc_info.value)


def test_run_instances_preserves_registry_override_value_error(
        monkeypatch, caplog):
    configuration = _provision_config()
    configuration.provider_config.update({
        'create_instance_kwargs': {
            'login': 'stale-direct-login',
        },
        'docker_login_config': {
            'username': 'registry-user',
            'password': 'registry-password',
            'server': 'registry.example.com',
        },
    })
    monkeypatch.setattr(vast_instance, '_wait_for_no_pending_instances',
                        lambda *_args, **_kwargs: {})
    sdk = _mock_vast_sdk(monkeypatch)

    with caplog.at_level(logging.WARNING), pytest.raises(
            ValueError, match='SKYPILOT_DOCKER'):
        vast_instance.run_instances('US', 'test', 'test', configuration)

    assert 'stale-direct-login' not in caplog.text
    assert 'registry-password' not in caplog.text
    sdk.search_offers.assert_not_called()


def test_wait_for_instances_ready_treats_null_as_pending(monkeypatch):
    instances = [
        {
            'instance-1': _instance('instance-1', 'NULL')
        },
        {
            'instance-1': _instance('instance-1', 'RUNNING', ssh_port=22)
        },
    ]
    monkeypatch.setattr(vast_instance, '_filter_instances',
                        lambda *_args, **_kwargs: instances.pop(0))
    monkeypatch.setattr(vast_instance.time, 'monotonic', lambda: 0)
    monkeypatch.setattr(vast_instance.time, 'sleep', lambda _seconds: None)

    ready_instances = vast_instance._wait_for_instances_ready(
        'test',
        expected_count=1,
        deadline=30,
        created_instance_ids=['instance-1'])

    assert ready_instances['instance-1']['ssh_port'] == 22


def test_wait_for_instances_ready_treats_resumed_stopped_as_pending(
        monkeypatch):
    instances = [
        {
            'instance-1': _instance('instance-1', 'STOPPED')
        },
        {
            'instance-1': _instance('instance-1', 'RUNNING', ssh_port=22)
        },
    ]
    monkeypatch.setattr(vast_instance, '_filter_instances',
                        lambda *_args, **_kwargs: instances.pop(0))
    monkeypatch.setattr(vast_instance.time, 'monotonic', lambda: 0)
    monkeypatch.setattr(vast_instance.time, 'sleep', lambda _seconds: None)

    ready_instances = vast_instance._wait_for_instances_ready(
        'test',
        expected_count=1,
        deadline=30,
        created_instance_ids=[],
        resumed_instance_ids=['instance-1'])

    assert ready_instances['instance-1']['ssh_port'] == 22


def test_run_instances_returns_resumed_instance_ids(monkeypatch):
    configuration = _provision_config()
    configuration.resume_stopped_nodes = True
    stopped_instance = _instance('instance-1', 'STOPPED')
    monkeypatch.setattr(
        vast_instance, '_wait_for_no_pending_instances',
        lambda *_args, **_kwargs: {'instance-1': stopped_instance})
    start = mock.Mock()
    monkeypatch.setattr(vast_utils, 'start', start)
    monkeypatch.setattr(
        vast_instance, '_wait_for_instances_ready', lambda *_args, **_kwargs:
        {'instance-1': _instance('instance-1', 'RUNNING', ssh_port=22)})
    monkeypatch.setattr(
        vast_utils, 'list_instances',
        lambda: {'instance-1': _instance('instance-1', 'RUNNING', ssh_port=22)})

    record = vast_instance.run_instances('US', 'test', 'test', configuration)

    start.assert_called_once_with('instance-1')
    assert record.resumed_instance_ids == ['instance-1']
    assert record.created_instance_ids == []


@pytest.mark.parametrize('status',
                         ['EXITED', 'STOPPED', 'FROZEN', 'UNKNOWN', 'OFFLINE'])
def test_wait_for_instances_ready_fails_for_terminal_or_lost_host(
        monkeypatch, status):
    monkeypatch.setattr(
        vast_instance, '_filter_instances', lambda *_args, **_kwargs: {
            'instance-1': _instance(
                'instance-1', status, status_msg='Vast reported failure')
        })

    with pytest.raises(exceptions.VastProvisioningError,
                       match=f'instance-1.*{status}'):
        vast_instance._wait_for_instances_ready(
            'test',
            expected_count=1,
            deadline=30,
            created_instance_ids=['instance-1'])


def test_wait_for_instances_ready_times_out_without_ssh(monkeypatch):
    monkeypatch.setattr(
        vast_instance, '_filter_instances', lambda *_args, **_kwargs:
        {'instance-1': _instance('instance-1', 'RUNNING')})
    monotonic = mock.Mock(side_effect=[0, 30])
    monkeypatch.setattr(vast_instance.time, 'monotonic', monotonic)
    monkeypatch.setattr(vast_instance.time, 'sleep', lambda _seconds: None)

    with pytest.raises(exceptions.VastProvisioningError, match='timed out'):
        vast_instance._wait_for_instances_ready(
            'test',
            expected_count=1,
            deadline=30,
            created_instance_ids=['instance-1'])


def test_run_instances_retries_once_on_a_different_machine(monkeypatch):
    first_failure = exceptions.VastProvisioningError(
        'Vast instance provisioning failed for instance-1 (EXITED).',
        instance_ids=['instance-1'])
    monkeypatch.setattr(vast_instance, '_filter_instances',
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(vast_instance.time, 'monotonic', lambda: 0)
    monkeypatch.setattr(
        vast_instance, '_wait_for_instances_ready',
        mock.Mock(side_effect=[
            first_failure, {
                'instance-2': _instance(
                    'instance-2', 'RUNNING', ssh_port=22, machine_id=20)
            }
        ]))
    monkeypatch.setattr(vast_instance, '_log_instance_diagnostics',
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(vast_utils, 'launch',
                        mock.Mock(side_effect=['instance-1', 'instance-2']))
    monkeypatch.setattr(
        vast_utils, 'list_instances',
        mock.Mock(side_effect=[{
            'instance-1': _instance('instance-1', 'EXITED', machine_id=10)
        }, {
            'instance-2': _instance(
                'instance-2', 'RUNNING', ssh_port=22, machine_id=20)
        }]))
    remove = mock.Mock()
    monkeypatch.setattr(vast_utils, 'remove', remove)

    record = vast_instance.run_instances('any', 'test', 'test',
                                         _provision_config())

    assert remove.call_args_list == [mock.call('instance-1')]
    assert vast_utils.launch.call_args_list[1].kwargs[
        'excluded_machine_ids'] == [10]
    assert record.head_instance_id == 'instance-2'
    assert record.created_instance_ids == ['instance-2']


def test_diagnostics_redact_known_secrets(monkeypatch, caplog):

    def get_logs(instance_id, daemon_logs, tail):
        del instance_id, daemon_logs, tail
        return 'api-key registry-password env-secret'

    monkeypatch.setattr(vast_utils, 'get_instance_logs', get_logs)
    vast_instance.logger.addHandler(caplog.handler)
    try:
        caplog.set_level(logging.DEBUG, logger=vast_instance.logger.name)
        vast_instance._log_instance_diagnostics(
            ['instance-1'], ['api-key', 'registry-password', 'env-secret'])
    finally:
        vast_instance.logger.removeHandler(caplog.handler)

    assert 'api-key' not in caplog.text
    assert 'registry-password' not in caplog.text
    assert 'env-secret' not in caplog.text
    assert '<redacted>' in caplog.text


def test_diagnostics_redact_log_fetch_exception(monkeypatch, caplog):

    def get_logs(instance_id, daemon_logs, tail):
        del instance_id, daemon_logs, tail
        raise RuntimeError('log request exposed registry-password')

    monkeypatch.setattr(vast_utils, 'get_instance_logs', get_logs)
    vast_instance.logger.addHandler(caplog.handler)
    try:
        caplog.set_level(logging.DEBUG, logger=vast_instance.logger.name)
        vast_instance._log_instance_diagnostics(['instance-1'],
                                                ['registry-password'])
    finally:
        vast_instance.logger.removeHandler(caplog.handler)

    assert 'registry-password' not in caplog.text
    assert '<redacted>' in caplog.text


def test_query_instances_maps_all_vast_lifecycle_states(monkeypatch):
    monkeypatch.setattr(
        vast_instance, '_filter_instances', lambda *_args, **_kwargs: {
            'null': _instance('null', 'NULL', status_msg='provisioning'),
            'unknown': _instance(
                'unknown', 'UNKNOWN', status_msg='no heartbeat'),
            'offline': _instance('offline', 'OFFLINE'),
            'frozen': _instance('frozen', 'FROZEN'),
            'other': _instance('other', 'UNRECOGNIZED'),
        })

    statuses = vast_instance.query_instances('test', 'test', {})

    assert statuses['null'] == (status_lib.ClusterStatus.INIT, 'provisioning')
    assert statuses['unknown'] == (status_lib.ClusterStatus.INIT,
                                   'no heartbeat')
    assert statuses['offline'][0] == status_lib.ClusterStatus.INIT
    assert statuses['frozen'][0] == status_lib.ClusterStatus.STOPPED
    assert statuses['other'][0] == status_lib.ClusterStatus.INIT
