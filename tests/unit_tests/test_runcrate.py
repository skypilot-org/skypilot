"""Tests for the Runcrate cloud."""

from typing import Any, Dict, List
from unittest import mock

import pytest

from sky import clouds
from sky.catalog.data_fetchers import fetch_runcrate
from sky.clouds import runcrate
from sky.provision import common
from sky.provision.runcrate import instance
from sky.provision.runcrate import utils

_PUBLIC_KEY = ('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAID3bgGcXo6G9ASrOhrVAFnwNk'
               'xnl6tj49O31h85HRqut test')
# `ssh-keygen -lf` of _PUBLIC_KEY.
_FINGERPRINT = 'SHA256:IjTyQi8Fp5I8QGLnQdMjq9CJ5eD+oh9lGJNSsndCpJ0'


def _offer(**overrides: Any) -> Dict[str, Any]:
    offer = {
        'id': 'bold-tesla-a6000-3f7a',
        'gpu_type': 'A6000',
        'gpu_count': 1,
        'cpu_cores': 6,
        'memory_gb': 48,
        'storage_gb': 256,
        'region': 'us/midwest',
        'deployment_type': 'vm',
        'host_tier': 'datacenter',
        'port_mapped': False,
        'hourly_rate': 0.66,
    }
    offer.update(overrides)
    return offer


def _provision_config(count: int = 1) -> common.ProvisionConfig:
    return common.ProvisionConfig(
        provider_config={'region': 'us-midwest'},
        authentication_config={'ssh_key_id': 'key-1'},
        docker_config={},
        node_config={'InstanceType': '1x_A6000_6c_48g'},
        count=count,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )


class TestOffers:

    def test_instance_type_names_the_shape(self):
        assert utils.instance_type_name(_offer()) == '1x_A6000_6c_48g'
        assert utils.instance_type_name(
            _offer(gpu_type='A100_80G',
                   gpu_count=8,
                   cpu_cores=240,
                   memory_gb=1280)) == '8x_A100-80GB_240c_1280g'
        assert utils.instance_type_name(
            _offer(gpu_type='CPU', gpu_count=0, cpu_cores=8,
                   memory_gb=32)) == 'cpu_8c_32g'

    def test_unsupported_offers_are_left_out(self):
        assert utils.is_supported_offer(_offer())
        # SSH is not on port 22 behind a host's shared address.
        assert not utils.is_supported_offer(
            _offer(deployment_type='container', port_mapped=True))
        assert not utils.is_supported_offer(_offer(host_tier='community'))
        assert not utils.is_supported_offer(_offer(gpu_type='NEWGPU'))

    def test_region_codes_never_contain_a_slash(self):
        assert utils.region_name('us/midwest') == 'us-midwest'
        assert utils.region_name('de/fra/2') == 'de-fra-2'
        assert utils.region_name('kansascity-usa-1') == 'kansascity-usa-1'

    def test_find_offer_picks_cheapest_of_type_in_region(self, monkeypatch):
        offers = [
            _offer(id='dear', hourly_rate=0.78),
            _offer(id='cheap', hourly_rate=0.66),
            _offer(id='other-region',
                   region='kansascity-usa-1',
                   hourly_rate=0.5),
            _offer(id='other-shape', memory_gb=24, hourly_rate=0.4),
            _offer(id='port-mapped', port_mapped=True, hourly_rate=0.1),
        ]
        monkeypatch.setattr(utils, '_request', lambda *a, **k: offers)
        assert utils.find_offer('1x_A6000_6c_48g',
                                'us-midwest')['id'] == 'cheap'
        assert utils.find_offer('1x_A6000_6c_48g', 'wichita-usa-1') is None


class TestCatalogFetcher:

    def test_one_row_per_type_and_region_at_cheapest_price(self):
        rows = fetch_runcrate.make_rows([
            _offer(hourly_rate=0.78),
            _offer(hourly_rate=0.66),
            _offer(region='kansascity-usa-1', hourly_rate=0.74),
            _offer(deployment_type='container',
                   port_mapped=True,
                   hourly_rate=0.1),
            _offer(gpu_type='CPU',
                   gpu_count=0,
                   cpu_cores=8,
                   memory_gb=32,
                   hourly_rate=0.16),
        ])
        by_key = {(row[0], row[6]): row for row in rows}
        assert set(by_key) == {
            ('1x_A6000_6c_48g', 'kansascity-usa-1'),
            ('1x_A6000_6c_48g', 'us-midwest'),
            ('cpu_8c_32g', 'us-midwest'),
        }
        gpu_row = by_key[('1x_A6000_6c_48g', 'us-midwest')]
        assert gpu_row[1:6] == ['A6000', 1, 6.0, 48.0, 0.66]
        assert 'SizeInMiB' in gpu_row[7]
        cpu_row = by_key[('cpu_8c_32g', 'us-midwest')]
        assert cpu_row[1:3] == ['', '']


class TestRequests:

    def _response(self, status: int, data: Any = None):
        response = mock.Mock()
        response.status_code = status
        response.content = b'{}'
        response.headers = {}
        response.json.return_value = ({
            'data': data
        } if status < 400 else {
            'error': {
                'code': 'x',
                'message': 'boom'
            }
        })
        return response

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        monkeypatch.setattr(utils, 'read_api_key', lambda: 'rc_live_test')
        monkeypatch.setattr(utils.time, 'sleep', lambda _: None)

    def test_get_is_retried_after_a_server_error(self, monkeypatch):
        calls = [self._response(502), self._response(200, ['ok'])]
        monkeypatch.setattr(utils.requests, 'request',
                            lambda *a, **k: calls.pop(0))
        assert utils._request('GET', '/instances') == ['ok']

    def test_post_is_never_retried_after_a_server_error(self, monkeypatch):
        request = mock.Mock(side_effect=[self._response(503)])
        monkeypatch.setattr(utils.requests, 'request', request)
        with pytest.raises(utils.RuncrateError) as e:
            utils._request('POST', '/instances', body={})
        assert e.value.status_code == 503
        assert request.call_count == 1

    def test_rate_limit_is_retried_for_post(self, monkeypatch):
        calls = [self._response(429), self._response(201, {'id': 'i-1'})]
        monkeypatch.setattr(utils.requests, 'request',
                            lambda *a, **k: calls.pop(0))
        assert utils._request('POST', '/instances', body={}) == {'id': 'i-1'}


class TestSshKeys:

    def test_fingerprint_matches_ssh_keygen(self):
        assert utils._fingerprint(_PUBLIC_KEY) == _FINGERPRINT

    def test_existing_key_is_reused(self, monkeypatch):
        request = mock.Mock(return_value=[{
            'id': 'key-1',
            'name': 'mine',
            'fingerprint': _FINGERPRINT
        }])
        monkeypatch.setattr(utils, '_request', request)
        assert utils.get_or_add_ssh_key(_PUBLIC_KEY, 'skypilot-x') == 'key-1'
        request.assert_called_once_with('GET', '/ssh-keys')

    def test_taken_name_adds_key_under_another_name(self, monkeypatch):

        def request(method, path, params=None, body=None):
            del path, params
            if method == 'GET':
                return []
            if body['name'] == 'skypilot-x':
                raise utils.RuncrateError('taken', status_code=409)
            return {'id': 'key-2', 'name': body['name']}

        monkeypatch.setattr(utils, '_request', request)
        assert utils.get_or_add_ssh_key(_PUBLIC_KEY, 'skypilot-x') == 'key-2'


class TestProvisioner:

    def test_only_this_clusters_instances_are_matched(self, monkeypatch):
        monkeypatch.setattr(
            utils,
            'list_instances',
            lambda _: [
                {
                    'id': 'mine',
                    'name': 'sky-a-1234-head',
                    'status': 'running'
                },
                # Same prefix, different cluster.
                {
                    'id': 'theirs',
                    'name': 'sky-a-1234-head-2-head',
                    'status': 'running'
                },
            ])
        deleted: List[str] = []
        monkeypatch.setattr(utils, 'delete_instance', deleted.append)
        instance.terminate_instances('sky-a-1234')
        assert deleted == ['mine']

    def test_no_offer_raises_for_failover(self, monkeypatch):
        monkeypatch.setattr(utils, 'list_instances', lambda _: [])
        monkeypatch.setattr(utils, 'find_offer', lambda *_: None)
        create = mock.Mock()
        monkeypatch.setattr(utils, 'create_instance', create)
        with pytest.raises(utils.RuncrateError):
            instance.run_instances('us-midwest', 'c', 'c-1234',
                                   _provision_config())
        create.assert_not_called()

    def test_failed_launch_is_terminated_and_raised(self, monkeypatch):
        monkeypatch.setattr(utils, 'list_instances', lambda _: [])
        monkeypatch.setattr(utils, 'find_offer', lambda *_: _offer())
        monkeypatch.setattr(utils, 'create_instance', lambda *_: {'id': 'i-1'})
        monkeypatch.setattr(utils, 'get_instance_status',
                            lambda _: {'status': 'error'})
        deleted: List[str] = []
        monkeypatch.setattr(utils, 'delete_instance', deleted.append)
        with pytest.raises(utils.RuncrateError):
            instance.run_instances('us-midwest', 'c', 'c-1234',
                                   _provision_config())
        assert deleted == ['i-1']

    def test_launch_returns_once_running(self, monkeypatch):
        monkeypatch.setattr(utils, 'list_instances', lambda _: [])
        monkeypatch.setattr(utils, 'find_offer', lambda *_: _offer())
        create = mock.Mock(return_value={'id': 'i-1'})
        monkeypatch.setattr(utils, 'create_instance', create)
        statuses = [{
            'status': 'starting'
        }, {
            'status': 'running',
            'ip': '1.2.3.4'
        }]
        monkeypatch.setattr(utils, 'get_instance_status',
                            lambda _: statuses.pop(0))
        monkeypatch.setattr(instance.time, 'sleep', lambda _: None)
        record = instance.run_instances('us-midwest', 'c', 'c-1234',
                                        _provision_config())
        create.assert_called_once_with('c-1234-head', 'bold-tesla-a6000-3f7a',
                                       'key-1')
        assert record.head_instance_id == 'i-1'
        assert record.created_instance_ids == ['i-1']

    def test_open_ports_adds_only_missing_ports(self, monkeypatch):
        monkeypatch.setattr(
            utils, 'list_firewall_rules', lambda: [
                {
                    'protocol': 'tcp',
                    'port_from': 22,
                    'port_to': 22,
                    'sources': ['0.0.0.0/0', '::/0']
                },
                {
                    'protocol': 'tcp',
                    'port_from': 8080,
                    'port_to': 8080,
                    'sources': ['203.0.113.7/32']
                },
            ])
        added = []
        monkeypatch.setattr(utils, 'add_firewall_rule',
                            lambda *args: added.append(args[:2]))
        instance.open_ports('c-1234', ['22', '8080-8081'])
        assert added == [(8080, 8081)]


class TestCredentials:

    def test_missing_api_key_disables_runcrate(self, monkeypatch, tmp_path):
        monkeypatch.setattr(utils, 'CREDENTIALS_PATH',
                            str(tmp_path / 'api_key'))
        valid, hint = runcrate.Runcrate.check_credentials(
            clouds.CloudCapability.COMPUTE)
        assert not valid
        assert 'api_key' in hint

    def test_rejected_api_key_disables_runcrate(self, monkeypatch, tmp_path):
        key_path = tmp_path / 'api_key'
        key_path.write_text('rc_live_revoked')
        monkeypatch.setattr(utils, 'CREDENTIALS_PATH', str(key_path))

        def list_ssh_keys():
            raise utils.RuncrateError('Invalid API key', status_code=401)

        monkeypatch.setattr(utils, 'list_ssh_keys', list_ssh_keys)
        valid, hint = runcrate.Runcrate.check_credentials(
            clouds.CloudCapability.COMPUTE)
        assert not valid
        assert 'Invalid API key' in hint
