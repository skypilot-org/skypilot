"""Tests for the io.net cloud: catalog, fetcher, cloud and provisioner."""
# pylint: disable=redefined-outer-name,protected-access

import argparse
import ast
import csv
import json
from typing import Any, Dict
from unittest import mock
from urllib import parse

import pandas as pd
import pytest
import requests

import sky
from sky import clouds
from sky import exceptions
from sky import provision
from sky import skypilot_config
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.catalog import common as catalog_common
from sky.catalog import ionet_catalog
from sky.catalog.data_fetchers import fetch_ionet
from sky.provision import common
from sky.provision.ionet import instance
from sky.provision.ionet import utils
from sky.utils import auth_utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import yaml_utils

_VMS_CSV = """\
InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,StorageGiB,Price,SpotPrice,Region,GpuInfo,HardwareId,GpusPerVm
1x_RTX4090_24GB,RTX4090,1.0,16.0,64.0,500.0,0.3,,US,"{'Gpus': [{'Name': 'RTX4090', 'Manufacturer': 'NVIDIA', 'Count': 1.0, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 24576}",12,1
2x_RTX4090_24GB,RTX4090,2.0,32.0,128.0,,0.5,,DE,"{'Gpus': [{'Name': 'RTX4090', 'Manufacturer': 'NVIDIA', 'Count': 2.0, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 49152}",12,2
8x_H100_80GB,H100,8.0,128.0,1024.0,2000.0,16.0,,US,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 8.0, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 655360}",h100-hw,8
8x_H100_80GB,H100,8.0,128.0,1024.0,2000.0,18.0,,DE,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 8.0, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 655360}",h100-de,8
"""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fails any test that would reach the real io.net API."""

    def request(method, url, **kwargs):
        del kwargs
        pytest.fail(f'Unexpected real io.net request: {method} {url}')

    monkeypatch.setattr(utils.requests, 'request', request)


@pytest.fixture
def catalog_df(monkeypatch, tmp_path):
    """Serves the io.net catalog from a static vms.csv."""
    path = tmp_path / 'vms.csv'
    path.write_text(_VMS_CSV, encoding='utf-8')
    monkeypatch.setattr(ionet_catalog, '_df',
                        catalog_common.LazyDataFrame(str(path), lambda: False))


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_catalog_queries(catalog_df):
    del catalog_df
    assert ionet_catalog.instance_type_exists('1x_RTX4090_24GB')
    assert not ionet_catalog.instance_type_exists('12__1__US')
    assert ionet_catalog.get_hourly_cost('1x_RTX4090_24GB', region='US') == 0.3
    assert ionet_catalog.get_hourly_cost('8x_H100_80GB') == 16.0
    assert ionet_catalog.get_vcpus_mem_from_instance_type('8x_H100_80GB') == (
        128.0, 1024.0)
    assert ionet_catalog.get_accelerators_from_instance_type(
        '2x_RTX4090_24GB') == {
            'RTX4090': 2
        }
    assert ionet_catalog.get_instance_type_for_accelerator(
        'RTX4090', 2, region='DE')[0] == ['2x_RTX4090_24GB']
    assert ionet_catalog.get_instance_type_for_accelerator(
        'H100', 8)[0] == ['8x_H100_80GB']
    assert sorted(r.name for r in ionet_catalog.regions()) == ['DE', 'US']
    assert [
        r.name for r in ionet_catalog.get_region_zones_for_instance_type(
            '1x_RTX4090_24GB', use_spot=False)
    ] == ['US']


def test_hardware_config_is_per_region(catalog_df):
    del catalog_df
    assert ionet_catalog.get_hardware_config('1x_RTX4090_24GB', 'us') == (12, 1)
    assert ionet_catalog.get_hardware_config('8x_H100_80GB',
                                             'US') == ('h100-hw', 8)
    assert ionet_catalog.get_hardware_config('8x_H100_80GB',
                                             'DE') == ('h100-de', 8)
    hardware_id, _ = ionet_catalog.get_hardware_config('1x_RTX4090_24GB', 'US')
    assert type(hardware_id) is int  # pylint: disable=unidiomatic-typecheck


def test_missing_offer_raises_resources_unavailable(catalog_df):
    del catalog_df
    with pytest.raises(exceptions.ResourcesUnavailableError):
        ionet_catalog.get_hardware_config('gone', 'US')
    with pytest.raises(exceptions.ResourcesUnavailableError):
        ionet_catalog.get_hardware_config('1x_RTX4090_24GB', 'DE')


def test_storage_gib(catalog_df):
    del catalog_df
    assert ionet_catalog.get_storage_gib('1x_RTX4090_24GB', 'US') == 500
    assert ionet_catalog.get_storage_gib('2x_RTX4090_24GB', 'DE') is None
    assert ionet_catalog.get_storage_gib('gone', 'US') is None


def test_list_accelerators_and_float_columns(catalog_df):
    del catalog_df
    assert ionet_catalog._df['vCPUs'].dtype == 'float64'
    assert ionet_catalog._df['MemoryGiB'].dtype == 'float64'
    accelerators = ionet_catalog.list_accelerators(gpus_only=True,
                                                   name_filter=None,
                                                   region_filter='US',
                                                   quantity_filter=None)
    assert sorted(accelerators) == ['H100', 'RTX4090']
    info = accelerators['RTX4090'][0]
    assert info.instance_type == '1x_RTX4090_24GB'
    assert info.device_memory == 24.0
    assert isinstance(info.cpu_count, float)
    assert not pd.isna(info.price)


def test_spot_and_zones_are_rejected(catalog_df):
    del catalog_df
    with pytest.raises(ValueError, match='spot'):
        ionet_catalog.get_hourly_cost('1x_RTX4090_24GB', use_spot=True)
    with pytest.raises(ValueError, match='zones'):
        ionet_catalog.validate_region_zone('US', 'US-1')
    assert ionet_catalog.validate_region_zone('US', None) == ('US', None)


# ---------------------------------------------------------------------------
# Catalog fetcher
# ---------------------------------------------------------------------------


def _offer(**kwargs):
    offer = {
        'id': 'offer-1',
        'deploy_id': 12,
        'name': 'NVIDIA GeForce RTX 4090',
        'num_cards': 1,
        'price': 0.3,
        'location': 'us',
        'sold_out': False,
        'vram_per_card': 24,
        'vcpu': 16,
        'memory': 64,
        'storage': 500,
    }
    offer.update(kwargs)
    return offer


def _response(*offers):
    return {'data': {'hardware': list(offers)}}


@pytest.mark.parametrize('name,vram,expected', [
    ('NVIDIA GeForce RTX 4090', 24, 'RTX4090'),
    ('GeForce RTX 3090', 24, 'RTX3090'),
    ('H100 80GB HBM3', 80, 'H100'),
    ('NVIDIA H100 SXM5', 80, 'H100'),
    ('H100-PCIe', 80, 'H100'),
    ('H100 NVL', 94, 'H100-NVL'),
    ('h200', 141, 'H200'),
    ('NVIDIA A100-SXM4-80GB', 80, 'A100-80GB'),
    ('A100 40GB PCIe', 40, 'A100'),
    ('A100', None, 'A100'),
    ('Tesla V100', 32, 'V100-32GB'),
    ('NVIDIA L40S', 48, 'L40S'),
    ('RTX 6000 Ada', 48, 'RTX6000-Ada'),
    ('NVIDIA RTX A6000', 48, 'RTXA6000'),
    ('RTX PRO 6000', 96, 'RTXPRO6000'),
    ('AMD MI300X', 192, 'MI300X'),
])
def test_canonical_gpu_names(name, vram, expected):
    assert fetch_ionet.canonicalize_gpu_name(name, vram) == expected


def test_rows_have_readable_names_and_standard_columns():
    rows = fetch_ionet.parse_hardware(
        _response(
            _offer(),
            _offer(id='offer-2',
                   deploy_id='77',
                   name='A100 SXM4 80GB',
                   num_cards=8,
                   vram_per_card=80,
                   price=12.5,
                   location='DE',
                   vcpu=128,
                   memory=1024,
                   storage=None)))
    assert [r['InstanceType'] for r in rows
           ] == ['1x_RTX4090_24GB', '8x_A100_80GB']
    rtx, a100 = rows
    assert set(rtx) == set(fetch_ionet.COLUMNS)
    assert rtx['AcceleratorName'] == 'RTX4090'
    assert rtx['AcceleratorCount'] == 1.0
    assert rtx['Region'] == 'US'
    assert rtx['HardwareId'] == 12
    assert rtx['GpusPerVm'] == 1
    assert rtx['vCPUs'] == 16.0 and isinstance(rtx['vCPUs'], float)
    assert rtx['SpotPrice'] is None
    gpu_info = ast.literal_eval(rtx['GpuInfo'])
    assert gpu_info['Gpus'][0]['MemoryInfo']['SizeInMiB'] == 24 * 1024
    assert gpu_info['Gpus'][0]['Name'] == 'RTX4090'
    assert a100['AcceleratorName'] == 'A100-80GB'
    assert a100['HardwareId'] == 77
    assert a100['StorageGiB'] is None


def test_missing_vram_keeps_row():
    rows = fetch_ionet.parse_hardware(_response(_offer(vram_per_card=None)))
    assert rows[0]['InstanceType'] == '1x_RTX4090'
    gpu_info = ast.literal_eval(rows[0]['GpuInfo'])
    assert gpu_info['Gpus'][0]['MemoryInfo']['SizeInMiB'] is None


def test_sold_out_offers_are_skipped():
    rows = fetch_ionet.parse_hardware(
        _response(_offer(sold_out=True), _offer(id='2', location='DE')))
    assert [(r['InstanceType'], r['Region']) for r in rows
           ] == [('1x_RTX4090_24GB', 'DE')]


def test_region_collision_keeps_cheapest():
    rows = fetch_ionet.parse_hardware(
        _response(_offer(deploy_id=1, price=0.5), _offer(deploy_id=2,
                                                         price=0.2),
                  _offer(deploy_id=3, price=0.4)))
    assert len(rows) == 1
    assert rows[0]['HardwareId'] == 2
    assert rows[0]['Price'] == 0.2


def test_cross_region_shape_mismatch_gets_suffix():
    rows = fetch_ionet.parse_hardware(
        _response(_offer(), _offer(location='DE', vcpu=32, memory=128),
                  _offer(name='H100', vram_per_card=80)))
    assert [(r['InstanceType'], r['Region']) for r in rows] == [
        ('1x_H100_80GB', 'US'),
        ('1x_RTX4090_24GB_16vCPU_64GiB', 'US'),
        ('1x_RTX4090_24GB_32vCPU_128GiB', 'DE'),
    ]


@pytest.mark.parametrize('bad', [
    {
        'num_cards': 0
    },
    {
        'num_cards': 'two'
    },
    {
        'price': None
    },
    {
        'price': 'nan'
    },
    {
        'price': -1
    },
    {
        'location': ''
    },
    {
        'name': ' '
    },
    {
        'deploy_id': True
    },
    {
        'deploy_id': None
    },
    {
        'vcpu': 'many'
    },
])
def test_malformed_offers_are_skipped(bad):
    rows = fetch_ionet.parse_hardware(
        _response(_offer(id='bad', **bad), _offer(id='good', location='DE')))
    assert [r['Region'] for r in rows] == ['DE']


def test_missing_required_key_and_non_dict_items_are_skipped():
    offer = _offer()
    del offer['deploy_id']
    rows = fetch_ionet.parse_hardware(
        _response(offer, 'junk', None, _offer(location='DE')))
    assert [r['Region'] for r in rows] == ['DE']


@pytest.mark.parametrize('response', [{}, {
    'data': {}
}, {
    'data': {
        'hardware': {}
    }
}, [], None])
def test_malformed_response_raises(response):
    with pytest.raises(ValueError, match='data.hardware'):
        fetch_ionet.parse_hardware(response)


class _HardwareClient:
    response = _response(_offer())
    api_keys: list = []

    def __init__(self, api_key):
        self.api_keys.append(api_key)

    def get_hardware(self):
        return self.response


def test_create_catalog_writes_readable_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_ionet.ionet_utils, 'IONetClient', _HardwareClient)
    monkeypatch.setattr(_HardwareClient, 'api_keys', [])
    path = tmp_path / 'ionet' / 'vms.csv'
    fetch_ionet.create_catalog('key', str(path))
    assert _HardwareClient.api_keys == ['key']
    with open(path, encoding='utf-8') as f:
        assert next(csv.reader(f)) == fetch_ionet.COLUMNS
    df = catalog_common.LazyDataFrame(str(path), lambda: False)
    assert df['vCPUs'].dtype == 'float64'
    infos = catalog_common.list_accelerators_impl('IONet', df, True, None, None,
                                                  None)
    assert infos['RTX4090'][0].device_memory == 24.0


def test_create_catalog_keeps_existing_when_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_ionet.ionet_utils, 'IONetClient', _HardwareClient)
    monkeypatch.setattr(_HardwareClient, 'response',
                        _response(_offer(sold_out=True)))
    path = tmp_path / 'vms.csv'
    path.write_text('previous', encoding='utf-8')
    fetch_ionet.create_catalog('key', str(path))
    assert path.read_text(encoding='utf-8') == 'previous'


def test_api_key_sources(monkeypatch, tmp_path):
    key_file = tmp_path / 'api_key'
    key_file.write_text('from-file\n', encoding='utf-8')
    args = argparse.Namespace(api_key='from-flag', api_key_path=None)
    assert fetch_ionet.get_api_key(args) == 'from-flag'
    args = argparse.Namespace(api_key=None, api_key_path=str(key_file))
    assert fetch_ionet.get_api_key(args) == 'from-file'
    monkeypatch.setenv('IONET_API_KEY', 'from-env')
    args = argparse.Namespace(api_key=None, api_key_path=None)
    assert fetch_ionet.get_api_key(args) == 'from-env'


# ---------------------------------------------------------------------------
# Cloud
# ---------------------------------------------------------------------------


def test_instance_type_validates_with_cloud(catalog_df):
    del catalog_df
    sky.Resources(cloud=clouds.IONet(),
                  instance_type='1x_RTX4090_24GB').validate()
    with pytest.raises(ValueError, match='Invalid instance type'):
        sky.Resources(cloud=clouds.IONet(), instance_type='missing').validate()


@pytest.mark.parametrize('enable_all_clouds', [[clouds.IONet()]], indirect=True)
def test_instance_type_resolves_among_enabled_clouds(catalog_df,
                                                     enable_all_clouds):
    del catalog_df, enable_all_clouds
    resources = sky.Resources(instance_type='1x_RTX4090_24GB')
    resources.validate()
    assert clouds.IONet().is_same_cloud(resources.cloud)
    with pytest.raises(ValueError, match='Invalid instance type'):
        sky.Resources(instance_type='missing').validate()


def _deploy_variables(resources=None):
    cloud = clouds.IONet()
    if resources is None:
        resources = sky.Resources(cloud=cloud,
                                  instance_type='1x_RTX4090_24GB',
                                  region='US')
    return cloud.make_deploy_resources_variables(
        resources, resources_utils.ClusterName('test', 'test-1234'),
        clouds.Region('US'), None, 1)


def test_deploy_variables(catalog_df):
    del catalog_df
    with skypilot_config.override_skypilot_config(
        {'ionet': {
            'duration_hours': 3
        }}):
        variables = _deploy_variables()
    assert variables == {
        'instance_type': '1x_RTX4090_24GB',
        'hardware_id': 12,
        'gpus_per_vm': 1,
        'custom_resources': json.dumps({'RTX4090': 1}, separators=(',', ':')),
        'region': 'US',
        'duration_hours': 3,
    }


@pytest.mark.parametrize('duration_hours', [None, 0, -1, True, 1.5])
def test_deploy_variables_require_positive_duration(catalog_df, duration_hours):
    del catalog_df
    get_config = skypilot_config.get_effective_region_config

    # The config schema rejects these values, so bypass it to test the guard.
    def get_effective_region_config(**kwargs):
        if kwargs['cloud'] == 'ionet' and kwargs['keys'] == ('duration_hours',):
            return duration_hours
        return get_config(**kwargs)

    with mock.patch.object(skypilot_config, 'get_effective_region_config',
                           get_effective_region_config):
        with pytest.raises(ValueError, match='ionet.duration_hours'):
            _deploy_variables()


def test_deploy_variables_check_disk_size(catalog_df):
    del catalog_df
    resources = sky.Resources(cloud=clouds.IONet(),
                              instance_type='1x_RTX4090_24GB',
                              region='US',
                              disk_size=1000)
    with pytest.raises(ValueError, match='below the requested'):
        _deploy_variables(resources)


def _credential_check(monkeypatch, status_code):
    monkeypatch.setenv('IONET_API_KEY', 'test-key')
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs['params']))
        response = mock.Mock()
        response.status_code = status_code
        response.ok = status_code == 200
        response.reason = 'Unauthorized'
        response.content = b'{}'
        response.json.return_value = {'detail': 'bad key'}
        return response

    monkeypatch.setattr(utils.requests, 'request', request)
    return clouds.IONet._check_compute_credentials(), calls


def test_credential_check_lists_deployments(monkeypatch):
    (ok, reason), calls = _credential_check(monkeypatch, 200)
    assert (ok, reason) == (True, None)
    # The check must not depend on the hardware inventory endpoint.
    assert calls == [('GET',
                      utils.API_BASE_URL + utils.API_PREFIX + '/deployments', {
                          'page': 1,
                          'page_size': utils.PAGE_SIZE
                      })]


@pytest.mark.parametrize('status_code', [401, 403])
def test_credential_check_rejected_key(monkeypatch, status_code):
    (ok, reason), _ = _credential_check(monkeypatch, status_code)
    assert not ok
    assert 'rejected the API key' in reason
    assert 'IONET_API_KEY' in reason


def test_credential_check_missing_key(monkeypatch, tmp_path):
    monkeypatch.delenv('IONET_API_KEY', raising=False)
    monkeypatch.setattr(utils, 'API_KEY_PATH', str(tmp_path / 'api_key'))
    ok, reason = clouds.IONet._check_compute_credentials()
    assert not ok
    assert 'API key not found' in reason
    assert 'IONET_API_KEY' in reason


# ---------------------------------------------------------------------------
# Provisioner
# ---------------------------------------------------------------------------


class FakeClient:
    """API responses in the shape of the enterprise VMaaS endpoint."""

    def __init__(self):
        self.deployment = None
        self.status = 'running'
        self.calls = []
        self.fail_delete = False

    def list_deployments(self, page=1):
        self.calls.append(('list', page))
        deployments = [] if self.deployment is None else [{
            'id': self.deployment,
            'name': 'sky-test',
            'status': self.status,
        }]
        return {'data': {'deployments': deployments, 'total': len(deployments)}}

    def deploy_vms(self, request):
        self.calls.append(('deploy', request))
        self.deployment = 'deployment-1'
        return {'status': 'success', 'deployment_id': self.deployment}

    def get_deployment_details(self, deployment_id):
        self.calls.append(('details', deployment_id))
        return {'data': {'status': self.status}}

    def get_deployment_vms(self, deployment_id, page=1):
        self.calls.append(('vms', deployment_id, page))
        return {
            'data': {
                'total': 1,
                'workers': [{
                    'vm_id': 'vm-1',
                    'ssh_access': 'ssh -p 22042 ionet@203.0.113.7',
                }]
            }
        }

    def destroy_deployment(self, deployment_id):
        self.calls.append(('delete', deployment_id))
        if self.fail_delete:
            raise utils.IONetError('HTTP 503')
        self.status = 'destroyed'
        return {'status': 'success'}


@pytest.fixture
def fake_api(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, 'METADATA_DIR', str(tmp_path))
    client = FakeClient()
    monkeypatch.setattr(instance, '_client', lambda: client)
    return client


def _config():
    return common.ProvisionConfig(
        provider_config={},
        authentication_config={},
        docker_config={},
        node_config={
            'InstanceType': 'io-h100',
            'HardwareId': 'hardware-1',
            'GpusPerVm': 1,
            'DurationHours': 4,
            'PublicKey': 'ssh-ed25519 example',
        },
        count=1,
        tags={},
        resume_stopped_nodes=True,
        ports_to_open_on_launch=None,
    )


def _local_deployments():
    return utils.IONetMetadata('sky-test').list_deployments()


def test_lifecycle_preserves_vm_identity_and_ssh_details(fake_api):
    record = instance.run_instances('US', 'test', 'sky-test', _config())
    assert record.head_instance_id == 'vm-1'
    assert record.created_instance_ids == ['vm-1']
    request = next(value for kind, value in fake_api.calls if kind == 'deploy')
    assert request == {
        'resource_private_name': 'sky-test',
        'duration_hours': 4,
        'hardware_id': 'hardware-1',
        'gpus_per_vm': 1,
        'location_ids': ['US'],
        'vms_qty': 1,
        'vm_image_type': 'general',
        'ssh_keys': {
            'skypilot': 'ssh-ed25519 example'
        },
        'network_services': {},
    }

    info = instance.get_cluster_info('US', 'sky-test', {})
    assert info.head_instance_id == 'vm-1'
    assert info.ssh_user == 'ionet'
    assert info.instances['vm-1'][0].external_ip == '203.0.113.7'
    assert info.instances['vm-1'][0].ssh_port == 22042
    assert instance.query_instances('test', 'sky-test', {}) == {
        'vm-1': (status_lib.ClusterStatus.UP, None)
    }

    instance.terminate_instances('sky-test', {})
    assert instance.query_instances('test', 'sky-test', {}) == {}


def test_existing_remote_deployment_is_reused(fake_api):
    fake_api.deployment = 'deployment-1'
    record = instance.run_instances('US', 'test', 'sky-test', _config())
    assert record.head_instance_id == 'vm-1'
    assert not record.created_instance_ids
    assert not any(kind == 'deploy' for kind, *_ in fake_api.calls)


def test_expired_duration_does_not_keep_stale_ownership(fake_api):
    instance.run_instances('US', 'test', 'sky-test', _config())
    fake_api.status = 'completed'
    assert instance.query_instances('test', 'sky-test', {}) == {}
    assert not _local_deployments()


def test_missing_listing_preserves_active_deployment(fake_api, monkeypatch):
    instance.run_instances('US', 'test', 'sky-test', _config())
    monkeypatch.setattr(fake_api, 'list_deployments',
                        lambda page: {'data': {
                            'deployments': [],
                            'total': 0
                        }})
    second = instance.run_instances('US', 'test', 'sky-test', _config())
    assert second.head_instance_id == 'vm-1'
    assert not second.created_instance_ids
    assert sum(call[0] == 'deploy' for call in fake_api.calls) == 1


def test_delete_failure_keeps_discovery_metadata(fake_api):
    instance.run_instances('US', 'test', 'sky-test', _config())
    fake_api.fail_delete = True
    with pytest.raises(utils.IONetError, match='503'):
        instance.terminate_instances('sky-test', {})
    assert _local_deployments()


def test_terminal_failure_fails_over_and_cleans_up(fake_api):
    # The existing failed deployment is ignored and a new one is created.
    fake_api.deployment = 'deployment-0'
    fake_api.status = 'failed'
    with pytest.raises(exceptions.ResourcesUnavailableError, match='failed'):
        instance.run_instances('US', 'test', 'sky-test', _config())
    assert ('delete', 'deployment-1') in fake_api.calls
    assert ('delete', 'deployment-0') not in fake_api.calls
    assert not _local_deployments()


def test_failed_deployment_is_not_matched(fake_api):
    instance.run_instances('US', 'test', 'sky-test', _config())
    fake_api.status = 'failed'
    assert instance.query_instances('test', 'sky-test', {}) == {}
    assert not _local_deployments()


def test_query_maps_failed_to_terminated(fake_api, monkeypatch):
    instance.run_instances('US', 'test', 'sky-test', _config())
    fake_api.status = 'failed'
    # Bypass discovery to check the status mapping itself.
    monkeypatch.setattr(instance, '_find_deployment',
                        lambda *args: 'deployment-1')
    assert instance.query_instances('test',
                                    'sky-test', {},
                                    non_terminated_only=False) == {
                                        'vm-1': (None, None)
                                    }
    assert instance.query_instances('test', 'sky-test', {}) == {}


def test_pending_deployment_without_vm_is_reported(fake_api, monkeypatch):
    instance.run_instances('US', 'test', 'sky-test', _config())
    fake_api.status = 'deploying'
    monkeypatch.setattr(fake_api, 'get_deployment_vms',
                        lambda *args: {'data': {
                            'total': 0,
                            'workers': []
                        }})
    assert instance.query_instances('test', 'sky-test', {}) == {
        'deployment-1': (status_lib.ClusterStatus.INIT, None)
    }


def test_wait_timeout_destroys_created_deployment(fake_api, monkeypatch):
    fake_api.status = 'deploying'
    monkeypatch.setattr(instance, 'MAX_POLL_ATTEMPTS', 2)
    monkeypatch.setattr(instance, 'POLL_INTERVAL', 0)
    with pytest.raises(exceptions.ResourcesUnavailableError,
                       match='did not become ready'):
        instance.run_instances('US', 'test', 'sky-test', _config())
    assert ('delete', 'deployment-1') in fake_api.calls
    assert not _local_deployments()


def test_wait_failure_cleanup_error_is_logged(fake_api, monkeypatch):
    fake_api.status = 'deploying'
    fake_api.fail_delete = True
    monkeypatch.setattr(instance, 'MAX_POLL_ATTEMPTS', 1)
    monkeypatch.setattr(instance, 'POLL_INTERVAL', 0)
    with mock.patch.object(instance.logger, 'warning') as warning:
        with pytest.raises(exceptions.ResourcesUnavailableError):
            instance.run_instances('US', 'test', 'sky-test', _config())
    assert 'deployment-1' in warning.call_args[0][0]
    assert _local_deployments()


def test_wait_timeout_keeps_reused_deployment(fake_api, monkeypatch):
    fake_api.deployment = 'deployment-1'
    fake_api.status = 'deploying'
    monkeypatch.setattr(instance, 'MAX_POLL_ATTEMPTS', 1)
    monkeypatch.setattr(instance, 'POLL_INTERVAL', 0)
    with pytest.raises(exceptions.ResourcesUnavailableError):
        instance.run_instances('US', 'test', 'sky-test', _config())
    assert not any(kind == 'delete' for kind, *_ in fake_api.calls)


def _fail_vms_after_ready(fake_api, monkeypatch):
    """Lets the readiness check pass, then fails worker discovery."""
    vms_calls = []
    get_vms = fake_api.get_deployment_vms

    def get_deployment_vms(deployment_id, page=1):
        vms_calls.append(page)
        if len(vms_calls) > 1:
            raise utils.IONetError('io.net API HTTP 500: boom', status_code=500)
        return get_vms(deployment_id, page)

    monkeypatch.setattr(fake_api, 'get_deployment_vms', get_deployment_vms)


def test_worker_discovery_failure_destroys_created_deployment(
        fake_api, monkeypatch):
    _fail_vms_after_ready(fake_api, monkeypatch)
    with pytest.raises(utils.IONetError, match='boom'):
        instance.run_instances('US', 'test', 'sky-test', _config())
    assert ('delete', 'deployment-1') in fake_api.calls
    assert not _local_deployments()


def test_worker_discovery_failure_keeps_metadata_if_destroy_fails(
        fake_api, monkeypatch):
    _fail_vms_after_ready(fake_api, monkeypatch)
    fake_api.fail_delete = True
    with mock.patch.object(instance.logger, 'warning') as warning:
        with pytest.raises(utils.IONetError, match='boom'):
            instance.run_instances('US', 'test', 'sky-test', _config())
    assert 'deployment-1' in warning.call_args[0][0]
    assert _local_deployments()


def test_worker_discovery_failure_keeps_reused_deployment(
        fake_api, monkeypatch):
    fake_api.deployment = 'deployment-1'
    _fail_vms_after_ready(fake_api, monkeypatch)
    with pytest.raises(utils.IONetError, match='boom'):
        instance.run_instances('US', 'test', 'sky-test', _config())
    assert not any(kind == 'delete' for kind, *_ in fake_api.calls)


def test_rejected_deploy_fails_over(fake_api):

    def reject(request):
        raise utils.IONetError('io.net API HTTP 400: no capacity',
                               status_code=400)

    fake_api.deploy_vms = reject
    with pytest.raises(exceptions.ResourcesUnavailableError,
                       match='no capacity'):
        instance.run_instances('US', 'test', 'sky-test', _config())
    assert not _local_deployments()


@pytest.mark.parametrize('status_code', [401, 403])
def test_auth_errors_raise_invalid_credentials(fake_api, status_code):

    def reject(request):
        raise utils.IONetError(f'io.net API HTTP {status_code}: denied',
                               status_code=status_code)

    fake_api.deploy_vms = reject
    with pytest.raises(exceptions.InvalidCloudCredentials):
        instance.run_instances('US', 'test', 'sky-test', _config())

    def reject_list(page=1):
        raise utils.IONetError('denied', status_code=status_code)

    fake_api.list_deployments = reject_list
    with pytest.raises(exceptions.InvalidCloudCredentials):
        instance.run_instances('US', 'test', 'sky-test', _config())


def test_no_paid_deploy_retry(monkeypatch):
    monkeypatch.setenv('IONET_API_KEY', 'test-key')
    response = mock.Mock()
    response.status_code = 503
    response.reason = 'Unavailable'
    response.ok = False
    response.json.return_value = {'detail': 'Unavailable'}
    with mock.patch.object(utils.requests, 'request',
                           return_value=response) as request:
        with pytest.raises(utils.IONetError, match='503'):
            utils.IONetClient().deploy_vms({'test': True})
    assert request.call_count == 1


def test_ambiguous_deploy_transport_failure_is_not_retried(monkeypatch):
    monkeypatch.setenv('IONET_API_KEY', 'test-key')
    with mock.patch.object(
            utils.requests,
            'request',
            side_effect=utils.requests.Timeout('timed out')) as request:
        with pytest.raises(utils.IONetError, match='request failed'):
            utils.IONetClient().deploy_vms({'test': True})
    assert request.call_count == 1


def test_discovery_failure_does_not_create_deployment(fake_api):

    def fail_list(page=1):
        raise utils.IONetError('HTTP 503')

    fake_api.list_deployments = fail_list
    with pytest.raises(utils.IONetError, match='503'):
        instance.run_instances('US', 'test', 'sky-test', _config())
    assert not any(kind == 'deploy' for kind, *_ in fake_api.calls)


def _pending_delete(fake_api, status):

    def pending_delete(deployment_id):
        fake_api.calls.append(('delete', deployment_id))
        fake_api.status = status
        return {'status': 'success'}

    return pending_delete


def test_delete_timeout_retains_metadata(fake_api, monkeypatch):
    instance.run_instances('US', 'test', 'sky-test', _config())
    fake_api.destroy_deployment = _pending_delete(fake_api, 'running')
    monkeypatch.setattr(instance, 'MAX_POLL_ATTEMPTS', 1)
    monkeypatch.setattr(instance, 'POLL_INTERVAL', 0)
    with pytest.raises(utils.IONetError, match='did not terminate'):
        instance.terminate_instances('sky-test', {})
    assert _local_deployments()


def test_accepted_delete_timeout_warns(fake_api, monkeypatch):
    instance.run_instances('US', 'test', 'sky-test', _config())
    fake_api.destroy_deployment = _pending_delete(fake_api,
                                                  'termination requested')
    monkeypatch.setattr(instance, 'MAX_POLL_ATTEMPTS', 1)
    monkeypatch.setattr(instance, 'POLL_INTERVAL', 0)
    with mock.patch.object(instance.logger, 'warning') as warning:
        instance.terminate_instances('sky-test', {})
    warning.assert_called_once()
    assert not _local_deployments()


def test_delete_404_counts_as_terminated(fake_api):
    instance.run_instances('US', 'test', 'sky-test', _config())

    def gone(deployment_id):
        fake_api.calls.append(('details', deployment_id))
        raise utils.IONetError('io.net API HTTP 404: missing', status_code=404)

    fake_api.destroy_deployment = _pending_delete(fake_api, 'running')
    fake_api.get_deployment_details = gone
    instance.terminate_instances('sky-test', {})
    assert not _local_deployments()


def test_error_status_code_is_set(monkeypatch):
    monkeypatch.setenv('IONET_API_KEY', 'test-key')
    response = mock.Mock()
    response.status_code = 401
    response.reason = 'Unauthorized'
    response.ok = False
    response.json.return_value = {'detail': 'bad key'}
    with mock.patch.object(utils.requests, 'request', return_value=response):
        with pytest.raises(utils.IONetError) as exc_info:
            utils.IONetClient().list_deployments()
    assert exc_info.value.status_code == 401
    assert utils.IONetError('plain').status_code is None


# ---------------------------------------------------------------------------
# End-to-end launch flow
# ---------------------------------------------------------------------------


def test_inventory_to_template_to_lifecycle(monkeypatch, tmp_path):
    """Runs the provider flow against a stateful, mocked HTTP API.

    This uses the real catalog fetcher, catalog, cloud, template,
    authentication and provisioner code. It does not verify a live VM, SSH
    connection or billing transaction.
    """
    cloud_name = 'ionet-smoke-12345678'
    state: Dict[str, Any] = {'creates': [], 'deletes': 0, 'hardware_calls': 0}
    # Empty until the mocked deploy call creates the deployment.
    deployment: Dict[str, Any] = {}
    hardware = {
        'id': '12__1',
        'deploy_id': 12,
        'name': 'GeForce RTX 4090',
        'num_cards': 1,
        'supplier': 'internal',
        'price': 0.3,
        'vram_per_card': 24,
        'storage': 500,
        'vcpu': 16,
        'memory': 64,
        'location': 'US',
    }

    def request(method, url, **kwargs):
        assert url.startswith(utils.API_BASE_URL + utils.API_PREFIX)
        assert kwargs['headers']['x-api-key'] == 'test-key'
        assert kwargs['timeout'] > 0
        path = parse.urlparse(url).path[len(utils.API_PREFIX):]
        result: Dict[str, Any]
        if method == 'GET' and path == '/hardware':
            result = {'data': {'hardware': [hardware]}}
            state['hardware_calls'] += 1
        elif method == 'GET' and path == '/deployments':
            deployments = [deployment] if deployment else []
            result = {
                'data': {
                    'deployments': deployments,
                    'total': len(deployments)
                }
            }
        elif method == 'POST' and path == '/deploy':
            payload = kwargs['json']
            state['creates'].append(payload)
            deployment.update({
                'id': 'deployment-1',
                'name': payload['resource_private_name'],
                'status': 'running',
            })
            result = {'status': 'success', 'deployment_id': 'deployment-1'}
        elif method == 'GET' and path == '/deployment/deployment-1':
            result = {'data': deployment}
        elif method == 'GET' and path == '/deployment/deployment-1/vms':
            result = {
                'data': {
                    'total': 1,
                    'workers': [{
                        'device_id': 'device-1',
                        'vm_id': 'vm-1',
                        'status': 'running',
                        'ssh_access': 'ssh -p 30222 ubuntu@203.0.113.10',
                        'network_services': {},
                    }]
                }
            }
        elif method == 'DELETE' and path == '/deployment/deployment-1':
            state['deletes'] += 1
            deployment['status'] = 'destroyed'
            result = {'status': 'success', 'deployment_id': 'deployment-1'}
        else:
            pytest.fail(f'Unexpected API call: {method} {path}')
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(result).encode('utf-8')
        return response

    # Patch the client's lazy `requests` module, as the other tests do: a
    # patch of the real module is shadowed once a test has patched this one.
    monkeypatch.setattr(utils.requests, 'request', request)
    monkeypatch.setattr(utils, 'get_api_key', lambda: 'test-key')
    monkeypatch.setattr(utils, 'METADATA_DIR', str(tmp_path / 'metadata'))
    # Build the static catalog the way the catalog repo does, then serve it
    # from disk like the hosted vms.csv.
    catalog_path = str(tmp_path / 'ionet' / 'vms.csv')
    fetch_ionet.create_catalog('test-key', catalog_path)
    assert state['hardware_calls'] == 1
    monkeypatch.setattr(
        ionet_catalog, '_df',
        catalog_common.LazyDataFrame(catalog_path, lambda: False))

    public_key = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestFixture test'
    private_path = tmp_path / 'id_ed25519'
    public_path = tmp_path / 'id_ed25519.pub'
    private_path.write_text('unused-test-private-key', encoding='utf-8')
    public_path.write_text(public_key, encoding='utf-8')
    monkeypatch.setattr(auth_utils, 'get_or_generate_keys', lambda:
                        (str(private_path), str(public_path)))

    with skypilot_config.override_skypilot_config(
        {'ionet': {
            'duration_hours': 2
        }}):
        offerings = ionet_catalog.list_accelerators(gpus_only=True,
                                                    name_filter=None,
                                                    region_filter=None,
                                                    quantity_filter=None)
        offering = offerings['RTX4090'][0]
        assert offering.instance_type == '1x_RTX4090_24GB'
        cloud = clouds.IONet()
        resources = sky.Resources(cloud=cloud,
                                  instance_type=offering.instance_type,
                                  region='US')
        resources.validate()
        variables = cloud.make_deploy_resources_variables(
            resources, resources_utils.ClusterName('ionet-smoke', cloud_name),
            clouds.Region('US'), None, 1)
    assert not state['creates']
    # Optimization and deploy variables must not query live inventory.
    assert state['hardware_calls'] == 1
    # Variables that backend_utils.write_cluster_config adds for every cloud.
    variables.update({
        'cluster_name_on_cloud': cloud_name,
        'num_nodes': 1,
        'ssh_private_key': str(private_path),
        'credentials': {},
        'sky_ray_yaml_remote_path': '~/.sky/ray.yaml',
        'sky_ray_yaml_local_path': '/tmp/ray.yaml',
        'sky_remote_path': '~/.sky',
        'sky_wheel_hash': 'test-wheel',
        'sky_local_path': '/tmp/skypilot.whl',
        'initial_setup_commands': [],
        'conda_installation_commands': 'echo conda-install;',
        'uv_installation_commands': 'echo uv-install;',
        'ray_skypilot_installation_commands': 'echo shared-sky-runtime;',
        'copy_skypilot_templates_commands': 'echo copy-templates;',
        'ssh_max_sessions_config': 'echo ssh-sessions;',
    })
    yaml_path = str(tmp_path / 'cluster.yaml')
    common_utils.fill_template(
        cloud_vm_ray_backend._get_cluster_config_template(cloud), variables,
        yaml_path)
    backend_utils._add_auth_to_cluster_config(cloud, yaml_path)
    rendered = yaml_utils.read_yaml(yaml_path)

    assert rendered['provider']['module'] == 'sky.provision.ionet'
    assert rendered['provider']['region'] == 'US'
    # The cluster key is mounted even without credential files.
    assert rendered['file_mounts']['~/.ssh/sky-cluster-key'] == str(
        private_path)
    assert 'shared-sky-runtime' in '\n'.join(rendered['setup_commands'])
    node_config = rendered['available_node_types'][
        rendered['head_node_type']]['node_config']
    assert node_config['PublicKey'] == public_key
    provision_config = common.ProvisionConfig(
        provider_config=rendered['provider'],
        authentication_config=rendered['auth'],
        docker_config={},
        node_config=node_config,
        count=1,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )
    provision_config = provision.bootstrap_instances(
        provider_name='ionet',
        region='US',
        cluster_name_on_cloud=cloud_name,
        config=provision_config)
    record = provision.run_instances('ionet', 'US', 'ionet-smoke', cloud_name,
                                     provision_config)
    provider_config = provision_config.provider_config
    info = provision.get_cluster_info('ionet', 'US', cloud_name,
                                      provider_config)
    assert record.head_instance_id == info.head_instance_id == 'vm-1'
    assert record.created_instance_ids == ['vm-1']
    assert info.ssh_user == 'ubuntu'
    assert info.instances['vm-1'][0].ssh_port == 30222
    assert info.instances['vm-1'][0].external_ip == '203.0.113.10'
    assert provision.query_instances('ionet', 'ionet-smoke', cloud_name,
                                     provider_config) == {
                                         'vm-1':
                                             (status_lib.ClusterStatus.UP, None)
                                     }
    second = provision.run_instances('ionet', 'US', 'ionet-smoke', cloud_name,
                                     provision_config)
    assert second.head_instance_id == 'vm-1'
    assert not second.created_instance_ids
    assert len(state['creates']) == 1
    payload = state['creates'][0]
    assert payload['resource_private_name'] == cloud_name
    assert payload['hardware_id'] == 12
    assert payload['gpus_per_vm'] == 1
    assert payload['location_ids'] == ['US']
    assert payload['duration_hours'] == 2
    assert public_key in payload['ssh_keys'].values()
    # The vendor's deploy schema rejects fields it does not define.
    assert set(payload) == {
        'resource_private_name', 'duration_hours', 'hardware_id', 'gpus_per_vm',
        'location_ids', 'vms_qty', 'vm_image_type', 'ssh_keys',
        'network_services'
    }
    with pytest.raises(utils.IONetError, match='workers'):
        provision.terminate_instances('ionet',
                                      cloud_name,
                                      provider_config,
                                      worker_only=True)
    assert state['deletes'] == 0
    provision.terminate_instances('ionet', cloud_name, provider_config)
    assert state['deletes'] == 1
    assert deployment['status'] == 'destroyed'
    assert not utils.IONetMetadata(cloud_name).list_deployments()
