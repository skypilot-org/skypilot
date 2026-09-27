"""Tests for the Vast.ai REST client in sky.adaptors.vast."""
# pylint: disable=redefined-outer-name
import json
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest
import requests

from sky.adaptors import vast


class _FakeResponse:
    """Stand-in for requests.Response."""

    def __init__(self, status_code: int = 200, payload: Any = None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')


class _FakeRequests:
    """Records calls to requests.request and replays canned responses."""

    def __init__(self, responses: Optional[List[_FakeResponse]] = None):
        self.calls: List[Dict[str, Any]] = []
        self.responses = list(responses or [])

    def __call__(self,
                 method,
                 url,
                 headers=None,
                 params=None,
                 json=None,
                 timeout=None):  # pylint: disable=redefined-outer-name
        self.calls.append({
            'method': method,
            'url': url,
            'headers': headers,
            'params': params,
            'json': json,
            'timeout': timeout,
        })
        if self.responses:
            return self.responses.pop(0)
        return _FakeResponse(200, {})


@pytest.fixture
def client() -> vast.VastClient:
    return vast.VastClient(api_key='test-key')


@pytest.fixture
def fake_requests(monkeypatch) -> _FakeRequests:
    fake = _FakeRequests()
    monkeypatch.setattr(vast.requests, 'request', fake)
    monkeypatch.setattr(vast.time, 'sleep', lambda _: None)
    return fake


# --- API key resolution ------------------------------------------------------


def test_resolve_api_key_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    monkeypatch.setenv(vast.API_KEY_ENV_VAR, ' env-key \n')
    assert vast.resolve_api_key() == 'env-key'


def test_resolve_api_key_files(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv(vast.API_KEY_ENV_VAR, raising=False)
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    assert vast.resolve_api_key() is None

    legacy = tmp_path / '.vast_api_key'
    legacy.write_text('legacy-key\n')
    assert vast.resolve_api_key() == 'legacy-key'

    config = tmp_path / '.config' / 'vastai'
    config.mkdir(parents=True)
    (config / 'vast_api_key').write_text('config-key\n')
    assert vast.resolve_api_key() == 'config-key'

    xdg = tmp_path / 'xdg' / 'vastai'
    xdg.mkdir(parents=True)
    (xdg / 'vast_api_key').write_text('xdg-key')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    assert vast.resolve_api_key() == 'xdg-key'


def test_client_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv(vast.API_KEY_ENV_VAR, raising=False)
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    with pytest.raises(RuntimeError, match='No Vast.ai API key'):
        vast.VastClient()


# --- Query parsing -----------------------------------------------------------


def test_parse_offer_query_launch_shape():
    # The exact shape sky/provision/vast/utils.py builds.
    query = ('chunked=true georegion=true geolocation="NA" disk_space>=100 '
             'num_gpus=1 gpu_name="RTX 4090" cpu_ram>="16.0" datacenter=true '
             'hosting_type>=1')
    filters, georegion, chunked = vast.parse_offer_query(query)
    assert georegion and chunked
    assert filters == {
        'verified': {
            'eq': True
        },
        'external': {
            'eq': False
        },
        'rentable': {
            'eq': True
        },
        'geolocation': {
            'in': ['CA', 'US']
        },
        'disk_space': {
            'gte': '100'
        },
        'num_gpus': {
            'eq': '1'
        },
        'gpu_name': {
            'eq': 'RTX 4090'
        },
        'cpu_ram': {
            'gte': 16000.0
        },
        'datacenter': {
            'eq': True
        },
        'hosting_type': {
            'gte': '1'
        },
    }


def test_parse_offer_query_catalog_shape():
    # The shape sky/catalog/data_fetchers/fetch_vast.py builds (with spaces).
    query = ('georegion = true chunked = true inet_down >= 100 '
             'disk_space >= 80')
    filters, georegion, chunked = vast.parse_offer_query(query)
    assert georegion and chunked
    assert filters['inet_down'] == {'gte': '100'}
    assert filters['disk_space'] == {'gte': '80'}
    assert 'georegion' not in filters and 'chunked' not in filters


def test_parse_offer_query_operators_aliases_and_lists():
    filters, georegion, chunked = vast.parse_offer_query(
        'gpu_name in [RTX_4090,"A100 SXM4"] dph<=0.5 cuda_vers gte 12.0 '
        'reliability > 0.9 verified=any external != true '
        'gpu_ram notin [24,48] duration=1')
    assert not georegion and not chunked
    assert filters['gpu_name'] == {'in': ['RTX 4090', 'A100 SXM4']}
    assert filters['dph_total'] == {'lte': '0.5'}
    assert filters['cuda_max_good'] == {'gte': '12.0'}
    assert filters['reliability'] == {'gt': '0.9'}
    assert 'verified' not in filters  # wildcard removes the default.
    # Operators on one field merge (the default `external eq False` stays).
    assert filters['external'] == {'eq': False, 'neq': True}
    assert filters['gpu_ram'] == {'notin': [24000.0, 48000.0]}
    assert filters['duration'] == {'eq': 86400.0}


def test_parse_offer_query_none_and_defaults():
    filters, georegion, chunked = vast.parse_offer_query(None)
    assert not georegion and not chunked
    assert filters == {
        'verified': {
            'eq': True
        },
        'external': {
            'eq': False
        },
        'rentable': {
            'eq': True
        },
    }
    filters, _, _ = vast.parse_offer_query('num_gpus=2', defaults={})
    assert filters == {'num_gpus': {'eq': '2'}}


def test_parse_offer_query_directive_order_independent():
    before, _, _ = vast.parse_offer_query('georegion=true geolocation=NA')
    after, georegion, _ = vast.parse_offer_query(
        'geolocation=NA georegion=true')
    assert georegion
    assert before == after
    assert after['geolocation'] == {'in': ['CA', 'US']}


def test_parse_offer_query_geolocation_without_georegion():
    filters, _, _ = vast.parse_offer_query('geolocation=NA')
    assert filters['geolocation'] == {'eq': 'NA'}
    filters, _, _ = vast.parse_offer_query('georegion=true geolocation=US')
    # Not a region code: passed through untouched.
    assert filters['geolocation'] == {'eq': 'US'}


@pytest.mark.parametrize('query', [
    'gpu_name=RTX 4090',
    'num_gpus ~ 1',
    'num_gpus=',
    'georegion>=true',
    'verified>any',
])
def test_parse_offer_query_rejects_malformed(query):
    with pytest.raises(ValueError):
        vast.parse_offer_query(query)


# --- Offer post-processing ---------------------------------------------------


def test_postprocess_offers_chunked_and_georegion():
    offers = [
        {
            'id': 1,
            'hosting_type': 1,
            'geolocation': 'Texas, US',
            'cpu_ram': 128 * 1024,
            'cpu_cores': 64,
            'min_bid': 0.2,
            'gpu_ram': 24564,
            'disk_space': 1234.5,
        },
        {
            # Below the cpu_ram cutoff: dropped when chunked.
            'id': 2,
            'hosting_type': 0,
            'geolocation': 'Berlin, DE',
            'cpu_ram': 32 * 1024,
            'cpu_cores': 64,
            'min_bid': 0.1,
        },
        {
            # No geolocation, unknown country: left alone.
            'id': 3,
            'hosting_type': None,
            'geolocation': 'Somewhere, ZZ',
            'cpu_ram': 128 * 1024,
            'cpu_cores': 32,
            'min_bid': 0,
            'gpu_ram': 80000,
            'disk_space': 100,
        },
    ]
    result = vast.postprocess_offers([dict(o) for o in offers],
                                     georegion=True,
                                     chunked=True)
    assert [o['id'] for o in result] == [1, 3]
    first = result[0]
    assert first['datacenter'] is True
    assert first['geolocation'] == 'Texas, US, NA'
    assert first['cpu_ram'] == 64 * 1024
    assert first['cpu_cores'] == 32
    assert first['min_bid'] == 0
    assert first['gpu_ram'] == 24560
    assert first['disk_space'] == 1216
    assert result[1]['datacenter'] is False
    assert result[1]['geolocation'] == 'Somewhere, ZZ'

    plain = vast.postprocess_offers([dict(o) for o in offers])
    assert [o['id'] for o in plain] == [1, 2, 3]
    assert plain[1]['geolocation'] == 'Berlin, DE'
    assert plain[1]['cpu_ram'] == 32 * 1024


# --- HTTP calls --------------------------------------------------------------


def test_search_offers_request(client, fake_requests):
    fake_requests.responses.append(
        _FakeResponse(
            200, {
                'offers': [{
                    'id': 7,
                    'hosting_type': 1,
                    'geolocation': 'Ontario, CA',
                    'cpu_ram': 200000,
                    'cpu_cores': 48,
                    'min_bid': 0.5,
                    'gpu_ram': 81920,
                    'disk_space': 500,
                }]
            }))
    offers = client.search_offers(
        query='georegion=true chunked=true num_gpus=1 gpu_name="H100 SXM"',
        limit=10000)
    assert [o['id'] for o in offers] == [7]
    assert offers[0]['geolocation'] == 'Ontario, CA, NA'
    assert offers[0]['datacenter'] is True

    call = fake_requests.calls[0]
    assert call['method'] == 'POST'
    assert call['url'] == 'https://console.vast.ai/api/v0/bundles/'
    assert call['headers']['Authorization'] == 'Bearer test-key'
    assert call['json'] == {
        'verified': {
            'eq': True
        },
        'external': {
            'eq': False
        },
        'rentable': {
            'eq': True
        },
        'num_gpus': {
            'eq': '1'
        },
        'gpu_name': {
            'eq': 'H100 SXM'
        },
        'order': [['score', 'desc']],
        'type': 'on-demand',
        'limit': 10000,
        'allocated_storage': 5.0,
    }
    # The request body must be JSON serializable.
    json.dumps(call['json'])


def test_create_instance_payload(client, fake_requests):
    fake_requests.responses.append(
        _FakeResponse(200, {
            'success': True,
            'new_contract': 4242
        }))
    result = client.create_instance(123,
                                    label='sky-head',
                                    image='vastai/base:0.0.2',
                                    disk=100,
                                    onstart_cmd='touch ~/.no_auto_tmux',
                                    bid_price=0.3,
                                    env={'__SOURCE': 'skypilot'})
    assert result['new_contract'] == 4242
    call = fake_requests.calls[0]
    assert call['method'] == 'PUT'
    assert call['url'] == 'https://console.vast.ai/api/v0/asks/123/'
    body = call['json']
    assert body['client_id'] == 'me'
    assert body['image'] == 'vastai/base:0.0.2'
    assert body['disk'] == 100
    assert body['label'] == 'sky-head'
    assert body['onstart'] == 'touch ~/.no_auto_tmux'
    assert body['price'] == 0.3
    assert body['env'] == {'__SOURCE': 'skypilot'}
    assert body['runtype'] == 'ssh'
    assert body['template_hash_id'] is None
    assert 'args' not in body


def test_create_instance_template_and_env_string(client, fake_requests):
    client.create_instance('9',
                           template_hash_id='abc123',
                           env='-e FOO=bar -p 8080:8080 -e BAZ="q=1"')
    body = fake_requests.calls[0]['json']
    assert body['template_hash_id'] == 'abc123'
    assert 'runtype' not in body  # The template decides.
    assert body['env'] == {'FOO': 'bar', '-p 8080:8080': '1', 'BAZ': 'q=1'}

    client.create_instance(9, args=['python', 'train.py'])
    body = fake_requests.calls[1]['json']
    assert body['runtype'] == 'args'
    assert body['args'] == ['python', 'train.py']

    client.create_instance(9, jupyter_lab=True)
    assert fake_requests.calls[2]['json'][
        'runtype'] == 'jupyter_proxy ssh_proxy'

    client.create_instance(9, runtype='ssh_proxy')
    assert fake_requests.calls[3]['json']['runtype'] == 'ssh_proxy'

    client.create_instance(9, ssh=True)
    assert fake_requests.calls[4]['json']['runtype'] == 'ssh_proxy'
    client.create_instance(9, ssh=True, direct=True)
    assert fake_requests.calls[5]['json']['runtype'] == 'ssh_direc ssh_proxy'
    client.create_instance(9, jupyter=True, direct=True)
    assert (fake_requests.calls[6]['json']['runtype'] ==
            'jupyter_direc ssh_direc ssh_proxy')
    volume = {'mount_path': '/data', 'create_new': True, 'size': 15}
    client.create_instance(9, volume_info=volume)
    assert fake_requests.calls[7]['json']['volume_info'] == volume
    assert 'volume_info' not in fake_requests.calls[6]['json']
    with pytest.raises(ValueError, match='jupyter and args'):
        client.create_instance(9, jupyter=True, args=['x'])

    client.create_instance(9, direct=True)
    assert fake_requests.calls[8]['json']['runtype'] == 'ssh_direc ssh_proxy'

    client.create_instance(9, image_login='-u me -p secret registry', vm=True)
    body = fake_requests.calls[9]['json']
    assert body['image_login'] == '-u me -p secret registry'
    assert body['vm'] is True
    assert 'vm' not in fake_requests.calls[8]['json']


def test_create_instance_is_not_retried_after_transport_errors(
        client, monkeypatch):
    monkeypatch.setattr(vast.time, 'sleep', lambda _: None)
    request = mock.Mock(side_effect=requests.Timeout('slow'))
    monkeypatch.setattr(vast.requests, 'request', request)
    with pytest.raises(requests.Timeout):
        client.create_instance(9)
    # Renting is not idempotent: a timeout after Vast accepted the rental
    # must not create a second contract.
    assert request.call_count == 1


def test_create_instance_retries_only_rate_limits(client, fake_requests):
    fake_requests.responses.extend(
        [_FakeResponse(429),
         _FakeResponse(200, {'new_contract': 1})])
    assert client.create_instance(9)['new_contract'] == 1
    assert len(fake_requests.calls) == 2

    fake_requests.responses.extend([_FakeResponse(503), _FakeResponse(200)])
    with pytest.raises(requests.HTTPError):
        client.create_instance(9)
    assert len(fake_requests.calls) == 3


def test_instance_lifecycle_endpoints(client, fake_requests):
    fake_requests.responses.extend([
        _FakeResponse(
            200, {
                'instances': [{
                    'id': 1,
                    'label': 'sky-head',
                    'actual_status': 'running',
                    'ssh_port': 2222,
                    'start_date': 0,
                    'extra_env': [['A', '1']],
                }]
            }),
        _FakeResponse(
            200, {'instances': {
                'id': 1,
                'start_date': 0,
                'extra_env': []
            }}),
        _FakeResponse(200, {'instances': None}),
        _FakeResponse(200, {'success': True}),
        _FakeResponse(200, {'success': True}),
        _FakeResponse(200, {'success': True}),
    ])
    instances = client.show_instances()
    assert instances[0]['label'] == 'sky-head'
    assert instances[0]['extra_env'] == {'A': '1'}
    assert instances[0]['duration'] > 0

    assert client.show_instance(1)['id'] == 1
    assert client.show_instance(2) is None
    client.start_instance(1)
    client.stop_instance('1')
    client.destroy_instance(1)

    calls = fake_requests.calls
    assert (calls[0]['method'], calls[0]['url'],
            calls[0]['params']) == ('GET',
                                    'https://console.vast.ai/api/v0/instances/',
                                    {
                                        'owner': 'me'
                                    })
    assert calls[1]['url'] == 'https://console.vast.ai/api/v0/instances/1/'
    assert calls[3]['method'] == 'PUT'
    assert calls[3]['json'] == {'state': 'running'}
    assert calls[4]['json'] == {'state': 'stopped'}
    assert calls[5]['method'] == 'DELETE'
    assert calls[5]['url'] == 'https://console.vast.ai/api/v0/instances/1/'


def test_ssh_key_endpoints(client, fake_requests):
    fake_requests.responses.extend([
        _FakeResponse(200, [{
            'id': 1,
            'public_key': 'ssh-ed25519 AAAA'
        }]),
        _FakeResponse(200, {'success': True}),
    ])
    keys = client.show_ssh_keys()
    assert keys[0]['public_key'] == 'ssh-ed25519 AAAA'
    client.create_ssh_key('ssh-ed25519 BBBB')
    assert fake_requests.calls[0]['method'] == 'GET'
    assert fake_requests.calls[0][
        'url'] == 'https://console.vast.ai/api/v0/ssh/'
    assert fake_requests.calls[1]['method'] == 'POST'
    assert fake_requests.calls[1]['json'] == {'ssh_key': 'ssh-ed25519 BBBB'}


def test_retries_transient_errors(client, fake_requests):
    fake_requests.responses.extend(
        [_FakeResponse(429),
         _FakeResponse(503),
         _FakeResponse(200, [])])
    assert client.show_ssh_keys() == []
    assert len(fake_requests.calls) == 3


def test_http_errors_are_raised(client, fake_requests):
    fake_requests.responses.append(_FakeResponse(403, {'error': 'nope'}))
    with pytest.raises(requests.HTTPError):
        client.create_ssh_key('ssh-ed25519 CCCC')
    assert len(fake_requests.calls) == 1

    fake_requests.responses.extend([_FakeResponse(502)] * 3)
    with pytest.raises(requests.HTTPError):
        client.show_ssh_keys()
    assert len(fake_requests.calls) == 4


def test_connection_errors_retry_then_raise(client, monkeypatch):
    monkeypatch.setattr(vast.time, 'sleep', lambda _: None)
    request = mock.Mock(side_effect=requests.ConnectionError('down'))
    monkeypatch.setattr(vast.requests, 'request', request)
    with pytest.raises(requests.ConnectionError):
        client.show_ssh_keys()
    assert request.call_count == 3


def test_vast_singleton(monkeypatch):
    monkeypatch.setattr(vast, '_client', None)
    monkeypatch.setenv(vast.API_KEY_ENV_VAR, 'singleton-key')
    first = vast.vast()
    assert first.api_key == 'singleton-key'
    assert vast.vast() is first
