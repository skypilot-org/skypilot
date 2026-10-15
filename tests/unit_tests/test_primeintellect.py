"""Tests for the Prime Intellect catalog fetcher and socket resolution."""
# pylint: disable=protected-access
import ast
import csv

import pandas as pd
import pytest

from sky.catalog.data_fetchers import fetch_primeintellect as fetch
from sky.provision.primeintellect import utils


def _offer(**overrides):
    offer = {
        'cloudId': 'n3-H100-SXM5x8',
        'gpuType': 'H100_80GB',
        'socket': 'SXM5',
        'provider': 'hyperstack',
        'country': 'US',
        'dataCenter': 'US-1',
        'gpuCount': 8,
        'gpuMemory': 80,
        'vcpu': {
            'defaultCount': 192
        },
        'memory': {
            'defaultCount': 960
        },
        'security': 'secure_cloud',
        'prices': {
            'onDemand': 19.2,
            'currency': 'USD'
        },
        'images': [],
        'isSpot': False,
        'prepaidTime': None,
    }
    offer.update(overrides)
    return offer


# ---------------------------------------------------------------- fetcher


@pytest.mark.parametrize('gpu_type,socket,expected', [
    ('H100_80GB', 'PCIe', 'H100'),
    ('H100_80GB', 'SXM5', 'H100-SXM'),
    ('H200_141GB', 'SXM5', 'H200-SXM'),
    ('A100_80GB', 'SXM4', 'A100-80GB-SXM'),
    ('A100_40GB', 'PCIe', 'A100'),
    ('L40S_48GB', 'PCIe', 'L40S'),
    ('RTX_PRO_6000B_96GB', 'PCIe', 'RTXPRO6000'),
    ('B200_180GB', None, 'B200'),
])
def test_accelerator_name(gpu_type, socket, expected):
    assert fetch.accelerator_name(gpu_type, socket) == expected


def test_offer_to_row_sxm_gpu():
    row = fetch.offer_to_row(_offer())
    assert row['InstanceType'] == 'hyperstack__8xH100_80GB__192__960'
    assert row['UpstreamCloudId'] == 'n3-H100-SXM5x8'
    assert row['AcceleratorName'] == 'H100-SXM'
    assert row['AcceleratorCount'] == 8.0
    assert row['Region'] == 'US'
    assert row['AvailabilityZone'] == 'US-1'
    assert row['Price'] == 19.2
    assert row['SpotPrice'] == ''
    assert row['Socket'] == 'SXM5'
    # SkyPilot parses GpuInfo with ast.literal_eval
    # (sky/catalog/common.py); per-GPU size, total across GPUs.
    info = ast.literal_eval(row['GpuInfo'])
    assert info['Gpus'][0]['Name'] == 'H100-SXM'
    assert info['Gpus'][0]['MemoryInfo']['SizeInMiB'] == 80 * 1024
    assert info['TotalGpuMemoryInMiB'] == 8 * 80 * 1024


def test_offer_to_row_cpu_node():
    row = fetch.offer_to_row(
        _offer(gpuType='CPU_NODE', socket='PCIe', gpuCount=1, gpuMemory=0))
    assert row['InstanceType'] == 'hyperstack__CPU_NODE__192__960'
    assert row['AcceleratorName'] == ''
    assert row['AcceleratorCount'] == 0.0
    assert row['GpuInfo'] == ''


@pytest.mark.parametrize(
    'overrides',
    [
        {
            'isSpot': True
        },
        {
            'prepaidTime': 24
        },  # pre-charged at order time
        {
            'security': 'community_cloud'
        },
        {
            'prices': {
                'onDemand': None,
                'currency': 'USD'
            }
        },
        {
            'prices': {
                'onDemand': 2.0,
                'currency': 'EUR'
            }
        },
        {
            'country': None
        },
        {
            'vcpu': {}
        },
    ])
def test_offer_to_row_skips_unlaunchable(overrides):
    assert fetch.offer_to_row(_offer(**overrides)) is None


def test_build_rows_keeps_cheapest_duplicate():
    rows = fetch.build_rows([
        _offer(prices={
            'onDemand': 20.0,
            'currency': 'USD'
        }),
        _offer(cloudId='other', prices={
            'onDemand': 18.0,
            'currency': 'USD'
        }),
    ])
    assert len(rows) == 1
    assert rows[0]['UpstreamCloudId'] == 'other'


def test_build_rows_disambiguates_socket_collision():
    rows = fetch.build_rows([
        _offer(cloudId='pcie-box', socket='PCIe'),
        _offer(cloudId='sxm-box', socket='SXM5'),
    ])
    by_id = {r['UpstreamCloudId']: r for r in rows}
    assert by_id['pcie-box']['InstanceType'] == (
        'hyperstack__8xH100_80GB__192__960')
    assert by_id['sxm-box']['InstanceType'] == (
        'hyperstack__8xH100_80GB__192__960__SXM5')
    # The provisioner reads only the first four `__` fields.
    parts = by_id['sxm-box']['InstanceType'].split('__')
    assert (parts[1], int(parts[2]), int(parts[3])) == ('8xH100_80GB', 192, 960)


class _FakeResponse:

    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.ok = status == 200
        self.text = str(body)

    def json(self):
        return self._body


class _FakeSession:

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, headers, params, timeout):
        del timeout
        self.calls.append((url, headers, dict(params)))
        return _FakeResponse(self.pages[params['page'] - 1])


def test_fetch_offers_paginates_until_total():
    session = _FakeSession([
        {
            'items': [_offer(cloudId='a'),
                      _offer(cloudId='b')],
            'totalCount': 3
        },
        {
            'items': [_offer(cloudId='c')],
            'totalCount': 3
        },
    ])
    offers = fetch.fetch_offers('key', session=session)
    assert [o['cloudId'] for o in offers] == ['a', 'b', 'c']
    assert [c[2]['page'] for c in session.calls] == [1, 2]
    assert session.calls[0][1]['Authorization'] == 'Bearer key'


def test_fetch_offers_raises_on_http_error():

    class _ErrSession(_FakeSession):

        def get(self, url, headers, params, timeout):
            return _FakeResponse({'detail': 'nope'}, status=401)

    with pytest.raises(fetch.PrimeIntellectCatalogError):
        fetch.fetch_offers('key', session=_ErrSession([]))


def test_write_catalog_round_trip(tmp_path):
    path = tmp_path / 'vms.csv'
    fetch.write_catalog(fetch.build_rows([_offer()]), str(path))
    with open(path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == fetch.FIELDNAMES
    assert rows[0]['Socket'] == 'SXM5'


# ------------------------------------------------------------ provisioner


@pytest.fixture(name='catalog')
def _catalog_fixture(monkeypatch):
    """Install a small in-memory catalog for the provisioner lookups."""

    def _install(rows):
        monkeypatch.setattr(utils, '_df', pd.DataFrame(rows))
        monkeypatch.setattr(utils, '_lookup_dict', None)
        monkeypatch.setattr(utils, '_socket_lookup', None)

    return _install


def _client(monkeypatch, api_items=None, api_error=None):
    client = object.__new__(utils.PrimeIntellectAPIClient)
    client.base_url = 'https://api.example'
    client.headers = {}
    client.team_id = None
    calls = []

    def fake_request(method, url, headers, data=None):
        del headers
        calls.append((method, url, data))
        if url.endswith('/api/v1/availability/gpus'):
            if api_error is not None:
                raise api_error
            return {'items': api_items or [], 'totalCount': 1}
        return {'id': 'pod-1'}

    monkeypatch.setattr(utils, '_try_request_with_backoff', fake_request)
    return client, calls


def test_catalog_socket_wins_without_api_call(monkeypatch, catalog):
    catalog([{
        'InstanceType': 'datacrunch__1xH100_80GB__30__120',
        'UpstreamCloudId': '1H100.80S.30V',
        'AvailabilityZone': 'FIN-02',
        'Socket': 'SXM5',
    }])
    client, calls = _client(monkeypatch)
    assert client.resolve_socket('datacrunch__1xH100_80GB__30__120',
                                 '1H100.80S.30V', 'H100_80GB',
                                 'FIN-02') == 'SXM5'
    assert not calls


def test_old_catalog_falls_back_to_availability_api(monkeypatch, catalog):
    # Catalogs written before the Socket column existed.
    catalog([{
        'InstanceType': 'nebius__1xH100_80GB__16__200',
        'UpstreamCloudId': 'gpu-h100-sxm_1gpu-16vcpu-200gb',
        'AvailabilityZone': 'eu-north1',
    }])
    client, calls = _client(monkeypatch,
                            api_items=[{
                                'cloudId': 'gpu-h100-sxm_1gpu-16vcpu-200gb',
                                'socket': 'SXM5'
                            }, {
                                'cloudId': 'unrelated',
                                'socket': 'PCIe'
                            }])
    assert client.resolve_socket('nebius__1xH100_80GB__16__200',
                                 'gpu-h100-sxm_1gpu-16vcpu-200gb', 'H100_80GB',
                                 'eu-north1') == 'SXM5'
    assert calls[0][2] == {
        'cloud_id': 'gpu-h100-sxm_1gpu-16vcpu-200gb',
        'gpu_type': 'H100_80GB',
        'data_center_id': 'eu-north1',
    }


@pytest.mark.parametrize('api_items,api_error', [
    (None, utils.PrimeintellectAPIError('forbidden', status_code=403)),
    ([], None),
    ([{
        'cloudId': 'x',
        'socket': 'PCIe'
    }, {
        'cloudId': 'x',
        'socket': 'SXM5'
    }], None),
])
def test_unknown_socket_defaults_to_pcie(monkeypatch, catalog, api_items,
                                         api_error):
    catalog([{
        'InstanceType': 't',
        'UpstreamCloudId': 'x',
        'AvailabilityZone': 'z'
    }])
    client, _ = _client(monkeypatch, api_items=api_items, api_error=api_error)
    assert client.resolve_socket('t', 'x', 'H100_80GB', 'z') == 'PCIe'


def test_cpu_node_does_not_look_up(monkeypatch, catalog):
    catalog([{
        'InstanceType': 't',
        'UpstreamCloudId': 'x',
        'AvailabilityZone': 'z'
    }])
    client, calls = _client(monkeypatch)
    assert client.resolve_socket('t', 'x', 'CPU_NODE', 'z') == 'PCIe'
    assert not calls


def test_launch_sends_resolved_socket(monkeypatch, catalog):
    catalog([{
        'InstanceType': 'datacrunch__8xH100_80GB__176__1480',
        'UpstreamCloudId': '8H100.80S.176V',
        'AvailabilityZone': 'FIN-02',
        'Socket': 'SXM5',
    }])
    client, calls = _client(monkeypatch)
    client.launch(name='c',
                  instance_type='datacrunch__8xH100_80GB__176__1480',
                  region='FI',
                  availability_zone='FIN-02',
                  disk_size=256)
    method, url, payload = calls[-1]
    assert (method, url) == ('post', 'https://api.example/api/v1/pods')
    assert payload['pod']['socket'] == 'SXM5'
    assert payload['pod']['cloudId'] == '8H100.80S.176V'
    assert payload['pod']['gpuType'] == 'H100_80GB'
    assert payload['pod']['gpuCount'] == 8
