"""Creation-time Vast TCP port contracts without provider requests."""
from importlib import metadata
from unittest import mock

import pytest

from sky.adaptors import vast
from sky.provision.vast import instance
from sky.provision.vast import utils


@pytest.fixture(name='sdk')
def mock_sdk(monkeypatch):
    client = mock.MagicMock()
    client.search_offers.return_value = [{'id': 7}]
    client.create_instance.return_value = {'new_contract': 9}
    client.show_instance.return_value = {'id': 9}
    monkeypatch.setattr(vast, 'vast', lambda: client)
    monkeypatch.setattr(metadata, 'version', lambda _: '1.0.0')
    return client


def launch(ports, **kwargs):
    return utils.launch(name='example',
                        instance_type='1x-RTX_4090-24-16384',
                        region='US',
                        image_name='synthetic-image',
                        disk_size=10,
                        ports=ports,
                        preemptible=False,
                        secure_only=False,
                        **kwargs)


def test_create_ports_preserves_env(sdk):
    env = {
        'SPACE': 'hello world',
        'QUOTES': 'a\' b" c',
        'EMPTY': '',
        'UNICODE': '東京',
        'EQUALS': 'a=b=c'
    }
    original = dict(env)
    assert launch([18080, 18081, 18080],
                  create_instance_kwargs={'env': env}) == 9
    query = sdk.search_offers.call_args.kwargs['query']
    assert 'direct_port_count>=3' in query
    payload = sdk.create_instance.call_args.kwargs
    assert payload['env'] == dict(env,
                                  __SOURCE='skypilot',
                                  **{
                                      '-p 18080:18080': '1',
                                      '-p 18081:18081': '1'
                                  })
    assert env == original


def test_ssh_port_not_double_counted(sdk):
    launch([22, 18080])
    assert 'direct_port_count>=2' in sdk.search_offers.call_args.kwargs['query']


def test_no_ports_keeps_existing_sdk_path(sdk, monkeypatch):
    monkeypatch.setattr(
        metadata, 'version',
        mock.Mock(side_effect=AssertionError('version queried')))
    launch(None)
    assert 'direct_port_count' not in sdk.search_offers.call_args.kwargs[
        'query']
    assert sdk.create_instance.call_args.kwargs['env'] == {
        '__SOURCE': 'skypilot'
    }


@pytest.mark.parametrize('installed', ['0.1.12', '0.6.0', '1.0.0rc1'])
def test_old_sdk_fails_before_constructor(monkeypatch, installed):
    constructor = mock.Mock(side_effect=AssertionError('provider constructed'))
    monkeypatch.setattr(vast, 'vast', constructor)
    monkeypatch.setattr(metadata, 'version', lambda _: installed)
    with pytest.raises(RuntimeError, match='vastai>=1.0.0'):
        launch([18080])
    constructor.assert_not_called()


def test_actual_public_port_map(sdk):
    sdk.show_instances.return_value = [{
        'id': 9,
        'label': 'example-head',
        'actual_status': 'running',
        'public_ipaddr': '192.0.2.10',
        'ssh_host': 'ssh.example.invalid',
        'ssh_port': 40000,
        'ports': {
            '18080/tcp': [{
                'HostIp': '0.0.0.0',
                'HostPort': '30325'
            }],
            '18081/tcp': [{
                'HostIp': '0.0.0.0',
                'HostPort': '30312'
            }],
            '8080/tcp': [{
                'HostIp': '0.0.0.0',
                'HostPort': '30320'
            }]
        },
    }]
    endpoints = instance.query_ports('example', ['18080-18081', '8080'])
    assert {
        port: [(e.host, e.port) for e in values
              ] for port, values in endpoints.items()
    } == {
        18080: [('192.0.2.10', 30325)],
        18081: [('192.0.2.10', 30312)],
        8080: [('192.0.2.10', 30320)]
    }


@pytest.mark.parametrize('mappings', [None, {}, {'8080/tcp': []}])
def test_missing_mapping_preserves_legacy_8080(sdk, mappings):
    sdk.show_instances.return_value = [{
        'id': 9,
        'label': 'example-head',
        'actual_status': 'running',
        'public_ipaddr': '192.0.2.10',
        'ssh_host': 'ssh.example.invalid',
        'ssh_port': 40000,
        'ports': mappings,
    }]
    endpoints = instance.query_ports('example', ['8080', '18080'])
    assert list(endpoints) == [8080]
    assert [(e.host, e.port) for e in endpoints[8080]
           ] == [('ssh.example.invalid', 40001)]


def test_invalid_bindings_and_duplicates(sdk):
    sdk.show_instances.return_value = [{
        'id': 9,
        'label': 'example-head',
        'actual_status': 'running',
        'public_ipaddr': '192.0.2.10',
        'ports': {
            '18080/tcp': [
                None, {}, {
                    'HostPort': True
                }, {
                    'HostPort': 1.5
                }, {
                    'HostPort': 'bad'
                }, {
                    'HostPort': '0'
                }, {
                    'HostPort': '65536'
                }, {
                    'HostPort': '30325'
                }, {
                    'HostPort': 30325
                }
            ]
        },
    }]
    assert [e.port for e in instance.query_ports('example', ['18080'])[18080]
           ] == [30325]


@pytest.mark.parametrize('legacy_package', ['vastai', 'vastai-sdk'])
def test_mixed_sdk_versions_fail_before_constructor(monkeypatch,
                                                    legacy_package):
    constructor = mock.Mock(side_effect=AssertionError('provider constructed'))
    monkeypatch.setattr(vast, 'vast', constructor)
    monkeypatch.setattr(
        metadata, 'version', lambda name: '0.6.0'
        if name == legacy_package else '1.0.0')
    with pytest.raises(RuntimeError, match='vastai-sdk>=1.0.0'):
        launch([18080])
    constructor.assert_not_called()


@pytest.mark.parametrize('env', [{
    '-p 30000:18080': '1'
}, {
    '-p 18080:18080': '0'
}, '-e LABEL=hello'])
def test_conflicting_env_rejected_before_provider(sdk, env):
    with pytest.raises(ValueError):
        launch([18080], create_instance_kwargs={'env': env})
    sdk.search_offers.assert_not_called()
    sdk.create_instance.assert_not_called()


def test_create_env_reaches_official_sdk_payload(sdk):
    provider = pytest.importorskip('vastai.api.instances')
    transport = mock.Mock()
    response = mock.Mock()
    response.json.return_value = {'new_contract': 9}
    transport.put.return_value = response
    sdk.create_instance.side_effect = lambda **kwargs: provider.create_instance(
        transport, **kwargs)
    env = {
        'SPACE': 'hello world',
        'QUOTES': 'a\' b" c',
        'EMPTY': '',
        'UNICODE': '東京',
        'EQUALS': 'a=b=c'
    }
    assert launch([18080], create_instance_kwargs={'env': env}) == 9
    transport.put.assert_called_once()
    args, kwargs = transport.put.call_args
    assert args == ('/asks/7/',)
    assert kwargs['json_data']['env'] == dict(env,
                                              __SOURCE='skypilot',
                                              **{'-p 18080:18080': '1'})
    response.raise_for_status.assert_called_once()


@pytest.mark.parametrize('env, expected_capacity', [
    ({
        '-p 18081:18081': '1'
    }, 3),
    ({
        '-p 18080:18080': '1',
        '-p 18081:18081/tcp': '1'
    }, 3),
    ({
        '-p 22:22': '1',
        '-p 18080:18080/tcp': '1'
    }, 2),
])
def test_known_env_ports_count_towards_capacity(sdk, env, expected_capacity):
    launch([18080], create_instance_kwargs={'env': env})
    query = sdk.search_offers.call_args.kwargs['query']
    assert f'direct_port_count>={expected_capacity}' in query
    payload = sdk.create_instance.call_args.kwargs['env']
    for key, value in env.items():
        assert payload[key] == value


@pytest.mark.parametrize('key', [
    '-p 18080-18081:18080-18081',
    '-p 127.0.0.1:18080:18080',
    '-p 18080:18080/udp',
    '-p18080:18080',
    '-p 18080',
    '-p ',
    '-p 0:18080',
    '-p 18080:65536',
    '-p 18080:18080/tcp/extra',
    '-p 18080:18x80',
])
def test_unsupported_port_keys_fail_before_provider(sdk, key):
    with pytest.raises(ValueError, match='Vast env TCP mapping'):
        launch([18080], create_instance_kwargs={'env': {key: '1'}})
    sdk.search_offers.assert_not_called()
    sdk.create_instance.assert_not_called()


def test_conflicting_extra_ports_fail_before_provider(sdk):
    with pytest.raises(ValueError, match='conflicting'):
        launch([18080],
               create_instance_kwargs={
                   'env': {
                       '-p 18081:18081': '1',
                       '-p 18082:18081/tcp': '1'
                   }
               })
    sdk.search_offers.assert_not_called()


def test_existing_tcp_alias_is_not_duplicated(sdk):
    launch([18080], create_instance_kwargs={'env': {'-p 18080:18080/tcp': '1'}})
    assert sdk.create_instance.call_args.kwargs['env'] == {
        '__SOURCE': 'skypilot',
        '-p 18080:18080/tcp': '1'
    }


@pytest.mark.parametrize('host,ssh_port', [('', 40000), (None, 40000),
                                           (False, 40000), ('gateway', None),
                                           ('gateway', True),
                                           ('gateway', '40000'),
                                           ('gateway', 1.5), ('gateway', 0),
                                           ('gateway', 65535)])
def test_invalid_legacy_metadata_is_omitted(sdk, host, ssh_port):
    sdk.show_instances.return_value = [{
        'id': 9,
        'label': 'example-head',
        'actual_status': 'running',
        'ssh_host': host,
        'ssh_port': ssh_port,
    }]
    assert not instance.query_ports('example', ['8080', '18080'])


def test_direct_8080_overrides_legacy_and_other_missing_ports(sdk):
    sdk.show_instances.return_value = [{
        'id': 9,
        'label': 'example-head',
        'actual_status': 'running',
        'public_ipaddr': '192.0.2.10',
        'ssh_host': 'gateway',
        'ssh_port': 40000,
        'ports': {
            '8080/tcp': [{
                'HostPort': '30001'
            }]
        },
    }]
    result = instance.query_ports('example', ['8080', '18080'])
    assert list(result) == [8080]
    assert [(e.host, e.port) for e in result[8080]] == [('192.0.2.10', 30001)]


def test_invalid_direct_8080_retains_public_ip_legacy_host(sdk):
    sdk.show_instances.return_value = [{
        'id': 9,
        'label': 'example-head',
        'actual_status': 'running',
        'public_ipaddr': '192.0.2.10',
        'ssh_host': '',
        'ssh_port': 65534,
        'ports': {
            '8080/tcp': [{
                'HostPort': 'invalid'
            }]
        },
    }]
    result = instance.query_ports('example', ['8080'])
    assert [(e.host, e.port) for e in result[8080]] == [('192.0.2.10', 65535)]
