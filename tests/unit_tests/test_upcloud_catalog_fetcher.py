"""Tests for the UpCloud catalog fetcher."""
# pylint: disable=protected-access

import logging

import pytest

from sky.catalog.data_fetchers import fetch_upcloud


def test_normalize_gpu_name():
    assert fetch_upcloud.normalize_gpu_name('L40S') == 'L40S'
    assert fetch_upcloud.normalize_gpu_name('NVIDIA L40s') == 'L40S'
    assert fetch_upcloud.normalize_gpu_name('NVIDIA H100 80GB HBM3') == 'H100'
    assert fetch_upcloud.normalize_gpu_name('NVIDIA B200') == 'B200'
    assert fetch_upcloud.normalize_gpu_name('B300') == 'B300'
    assert fetch_upcloud.normalize_gpu_name('NVIDIA B300 SXM6 AC') == 'B300'
    assert fetch_upcloud.normalize_gpu_name(
        'NVIDIA RTX PRO 6000 Blackwell Server Edition') == 'RTXPRO6000'
    assert fetch_upcloud.normalize_gpu_name('') == ''
    assert fetch_upcloud.normalize_gpu_name(None) == ''


def _gpu_plan(name, current='yes'):
    return {
        'name': name,
        'current_offering': current,
        'core_number': 8,
        'memory_amount': 65536,
        'gpu_amount': 1,
        'gpu_model': 'NVIDIA L40S',
    }


@pytest.fixture
def plans(monkeypatch):
    monkeypatch.setattr(
        fetch_upcloud, '_list_plans', lambda t: [
            _gpu_plan('GPU-8xCPU-64GB-1xL40S'),
            _gpu_plan('GPU-SPOT-8xCPU-64GB-1xL40S'),
            _gpu_plan('GPU-SPOT-8xCPU-64GB-1xB200'),
            _gpu_plan('GPU-OLD', current='no'),
            {
                'name': '2xCPU-4GB',
                'core_number': 2,
                'memory_amount': 4096
            },
        ])
    monkeypatch.setattr(fetch_upcloud, '_list_zones',
                        lambda t: ['fi-hel2', 'de-fra1', 'uk-lon1'])
    monkeypatch.setattr(fetch_upcloud, 'GPU_ZONES', ['fi-hel2', 'de-fra1'])
    monkeypatch.setattr(
        fetch_upcloud, '_price_map', lambda t, z: {
            'fi-hel2': {
                'GPU-8xCPU-64GB-1xL40S': 1.0,
                'GPU-SPOT-8xCPU-64GB-1xL40S': 0.5,
                '2xCPU-4GB': 0.02,
                'GPU-OLD': 0.01,
            },
            'de-fra1': {
                'GPU-8xCPU-64GB-1xL40S': 1.0,
                '2xCPU-4GB': 0.02,
            },
            'uk-lon1': {
                'GPU-8xCPU-64GB-1xL40S': 1.0,
                '2xCPU-4GB': 0.02,
            },
        })


@pytest.mark.usefixtures('plans')
def test_fetch_rows_merges_spot(caplog):
    caplog.set_level(logging.INFO)
    rows = fetch_upcloud.fetch_upcloud_rows('tok')
    names = {r[0] for r in rows}
    assert names == {'GPU-8xCPU-64GB-1xL40S', '2xCPU-4GB'}
    gpu = {r[6]: r for r in rows if r[0] == 'GPU-8xCPU-64GB-1xL40S'}
    # GPU plans only in GPU_ZONES, CPU plans in every priced zone.
    assert set(gpu) == {'fi-hel2', 'de-fra1'}
    assert [r[6] for r in rows if r[0] == '2xCPU-4GB'
           ] == ['de-fra1', 'fi-hel2', 'uk-lon1']
    assert gpu['fi-hel2'][1] == 'L40S'
    assert gpu['fi-hel2'][5] == 1.0
    assert gpu['fi-hel2'][8] == 0.5
    # No spot price in a zone without the spot plan.
    assert gpu['de-fra1'][8] == ''
    cpu = [r for r in rows if r[0] == '2xCPU-4GB']
    assert all(r[1] == '' and r[8] == '' for r in cpu)
    assert cpu[0][4] == 4.0
    assert 'GPU-SPOT-8xCPU-64GB-1xB200' in caplog.text


def test_list_zones_skips_private(monkeypatch):
    monkeypatch.setattr(fetch_upcloud,
                        '_get',
                        lambda t, p, params=None: {
                            'zones': {
                                'zone': [{
                                    'id': 'fi-hel2',
                                    'public': 'yes'
                                }, {
                                    'id': 'fi-hel3',
                                    'public': 'no'
                                }]
                            }
                        })
    assert fetch_upcloud._list_zones('tok') == ['fi-hel2']


def test_create_catalog_keeps_file_when_empty(monkeypatch, tmp_path):
    path = tmp_path / 'vms.csv'
    path.write_text('old', encoding='utf-8')
    monkeypatch.setattr(fetch_upcloud, 'fetch_upcloud_rows', lambda t: [])
    with pytest.raises(RuntimeError, match='No UpCloud rows'):
        fetch_upcloud.create_catalog('tok', str(path))
    assert path.read_text(encoding='utf-8') == 'old'


def test_price_map_rejects_non_usd(monkeypatch):
    monkeypatch.setattr(
        fetch_upcloud,
        '_get',
        lambda t, p, params=None: {'prices': {
            'currency': 'EUR',
            'zone': []
        }})
    with pytest.raises(ValueError, match='expected USD'):
        fetch_upcloud._price_map('tok', ['fi-hel2'])


def test_price_map_converts_cents_to_dollars(monkeypatch):
    calls = []

    def fake_get(t, p, params=None):
        calls.append((p, params))
        return {
            'prices': {
                'currency': 'USD',
                'zone': [{
                    'name': 'fi-hel2',
                    'server_plan_GPU-8xCPU-64GB-1xL40S': {
                        'price': 125
                    },
                }],
            }
        }

    monkeypatch.setattr(fetch_upcloud, '_get', fake_get)
    assert fetch_upcloud._price_map('tok', ['fi-hel2', 'de-fra1']) == {
        'fi-hel2': {
            'GPU-8xCPU-64GB-1xL40S': 1.25
        }
    }
    assert calls == [('/price', {'zone': ['fi-hel2', 'de-fra1']})]
