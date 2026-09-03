"""Tests for the locally refreshed Vast catalog."""

import ast
import csv
import importlib
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from sky.catalog import common
from sky.catalog import vast_catalog
from sky.catalog import vast_refresh
from sky.catalog.data_fetchers import fetch_vast
from sky.utils import annotations

_CATALOG_FIELDS = [
    'InstanceType',
    'AcceleratorName',
    'AcceleratorCount',
    'vCPUs',
    'MemoryGiB',
    'GpuInfo',
    'Price',
    'SpotPrice',
    'Region',
    'HostingType',
]


def _write_catalog(path: Path, *, include_hosting_type: bool = True) -> None:
    fields = _CATALOG_FIELDS if include_hosting_type else _CATALOG_FIELDS[:-1]
    row = {
        'InstanceType': '1x-A100-4-8192',
        'AcceleratorName': 'A100',
        'AcceleratorCount': '1',
        'vCPUs': '4',
        'MemoryGiB': '8',
        'GpuInfo': "{'Gpus': [{'MemoryInfo': {'SizeInMiB': 81920}}]}",
        'Price': '0.8',
        'SpotPrice': '0.8',
        'Region': 'any',
        'HostingType': '1',
    }
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({field: row[field] for field in fields})


@pytest.fixture(autouse=True)
def clear_request_catalog_cache():
    """Isolate request-scoped catalog snapshots between refresh tests."""
    annotations.clear_request_level_cache()
    yield
    annotations.clear_request_level_cache()


def test_catalog_loader_uses_local_common_catalog(monkeypatch):
    """Vast catalog reads the local common cache instead of hosted CSV text."""
    calls = []

    def read_catalog(filename: str):
        calls.append(filename)
        return pd.DataFrame([{
            'InstanceType': '1x-A100-4-8192',
            'AcceleratorName': 'A100',
            'AcceleratorCount': 1,
            'vCPUs': 4,
            'MemoryGiB': 8,
            'GpuInfo': 'gpu-info',
            'Price': .8,
            'SpotPrice': .8,
            'Region': 'any',
        }])

    monkeypatch.setattr(common, 'read_catalog', read_catalog)
    importlib.reload(vast_catalog)

    assert calls == ['vast/vms.csv']
    assert vast_catalog._catalog_df().iloc[0]['AcceleratorName'] == 'A100'
    monkeypatch.undo()
    importlib.reload(vast_catalog)


def test_catalog_loader_rejects_missing_required_columns(monkeypatch):
    """A malformed local catalog cannot silently enter resource selection."""
    monkeypatch.setattr(vast_catalog, '_df',
                        pd.DataFrame([{
                            'InstanceType': 'example'
                        }]))

    with pytest.raises(common.CatalogFetchError,
                       match='missing required columns'):
        vast_catalog._catalog_df()


def test_datacenter_filter_fails_closed_without_hosting_type():
    """Datacenter-only requests must not admit unknown hosting types."""
    df = pd.DataFrame([{field: '1' for field in _CATALOG_FIELDS[:-1]}])

    assert vast_catalog._apply_datacenter_filter(df, datacenter_only=True).empty


def test_fetch_vast_catalog_and_save_catalog_are_reusable(
        monkeypatch, tmp_path):
    """Catalog refresh retains its broad bucketing query and save contract."""
    offer = {
        'gpu_name': 'A100',
        'num_gpus': 1,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'search': {
            'totalHour': .8
        },
        'min_bid': .8,
        'geolocation': 'any',
        'hosting_type': 1,
        'gpu_total_ram': 81920,
    }
    client = mock.Mock(spec=['search_offers'])
    client.search_offers.return_value = [offer, offer]
    monkeypatch.setattr(fetch_vast.vast, 'vast', lambda: client)

    catalog_path = tmp_path / 'vms.csv'
    fetch_vast.save_catalog(fetch_vast.fetch_vast_catalog(), str(catalog_path))

    vast_refresh.validate_catalog(catalog_path)
    assert client.search_offers.call_args.kwargs['query'] == (
        'georegion = true chunked = true '
        'inet_down >= 100 disk_space >= 80')


def test_fetch_vast_catalog_keeps_countries_distinct(monkeypatch):
    """Asian and European country rows never collapse into continent buckets."""
    shared_offer = {
        'gpu_name': 'A100',
        'num_gpus': 1,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'search': {
            'totalHour': .8
        },
        'min_bid': .8,
        'hosting_type': 1,
        'gpu_total_ram': 81920,
    }
    offers = [{
        **shared_offer, 'geolocation': region
    } for region in ('Jiangsu, CN, AS', 'Japan, JP, AS', 'France, FR, EU')]
    client = type('Client', (),
                  {'search_offers': lambda _self, **_kwargs: offers})()
    monkeypatch.setattr(fetch_vast.vast, 'vast', lambda: client)

    rows = fetch_vast.fetch_vast_catalog()

    assert {row['Region'] for row in rows} == {
        'Jiangsu, CN, AS',
        'Japan, JP, AS',
        'France, FR, EU',
    }


def test_fetch_vast_catalog_preserves_per_gpu_memory_identity(monkeypatch):
    """A100 40GB and 80GB offers must be distinct durable catalog resources."""
    shared_offer = {
        'gpu_name': 'A100 SXM4',
        'cpu_cores': 32,
        'cpu_ram': 65536,
        'search': {
            'totalHour': .8
        },
        'min_bid': .8,
        'hosting_type': 1,
    }
    offers = [{
        **shared_offer,
        'num_gpus': 1,
        'gpu_total_ram': 40960,
        'geolocation': 'Georgia, US, NA',
    }, {
        **shared_offer,
        'num_gpus': 1,
        'gpu_total_ram': 81920,
        'geolocation': 'Prague, CZ, EU',
    }, {
        **shared_offer,
        'num_gpus': 2,
        'gpu_total_ram': 163840,
        'geolocation': 'Prague, CZ, EU',
    }]
    client = type('Client', (),
                  {'search_offers': lambda _self, **_kwargs: offers})()
    monkeypatch.setattr(fetch_vast.vast, 'vast', lambda: client)

    rows = fetch_vast.fetch_vast_catalog()

    rows_by_instance_type = {row['InstanceType']: row for row in rows}
    assert set(rows_by_instance_type) == {
        'vastv2-1x-A100_SXM4-40960-32-65536',
        'vastv2-1x-A100_SXM4-81920-32-65536',
        'vastv2-2x-A100_SXM4-81920-32-65536',
    }
    assert rows_by_instance_type['vastv2-1x-A100_SXM4-81920-32-65536'][
        'AcceleratorName'] == ('A100-80GB')
    gpu_info = ast.literal_eval(
        rows_by_instance_type['vastv2-2x-A100_SXM4-81920-32-65536']['GpuInfo'])
    assert gpu_info['Gpus'][0]['MemoryInfo']['SizeInMiB'] == 81920
    assert gpu_info['TotalGpuMemoryInMiB'] == 163840


def test_refresh_catalog_replaces_validated_staged_file(monkeypatch, tmp_path):
    """A successful Vast refresh atomically installs only validated output."""
    catalog_path = tmp_path / 'vast' / 'vms.csv'
    monkeypatch.setattr(vast_refresh.catalog_common, 'get_catalog_path',
                        lambda _name: str(catalog_path))
    monkeypatch.setattr(vast_refresh, 'has_credentials', lambda: True)
    monkeypatch.setattr(fetch_vast, 'fetch_vast_catalog', lambda: object())
    monkeypatch.setattr(fetch_vast, 'save_catalog',
                        lambda _rows, output: _write_catalog(Path(output)))

    assert vast_refresh.refresh_catalog()
    vast_refresh.validate_catalog(catalog_path)


def test_refresh_catalog_keeps_valid_file_on_fetch_failure(
        monkeypatch, tmp_path):
    """Temporary Vast failures preserve the last validated local catalog."""
    catalog_path = tmp_path / 'vast' / 'vms.csv'
    catalog_path.parent.mkdir()
    _write_catalog(catalog_path)
    original = catalog_path.read_text(encoding='utf-8')
    monkeypatch.setattr(vast_refresh.catalog_common, 'get_catalog_path',
                        lambda _name: str(catalog_path))
    monkeypatch.setattr(vast_refresh, 'has_credentials', lambda: True)
    monkeypatch.setattr(fetch_vast, 'fetch_vast_catalog', lambda:
                        (_ for _ in ()).throw(RuntimeError('offline')))

    assert vast_refresh.refresh_catalog()
    assert catalog_path.read_text(encoding='utf-8') == original


def test_refresh_catalog_skips_without_vast_credential_file(monkeypatch):
    """Refresh remains disabled unless the Vast credential file is present."""
    monkeypatch.setattr(vast_refresh, 'has_credentials', lambda: False)
    monkeypatch.setattr(fetch_vast, 'fetch_vast_catalog',
                        lambda: pytest.fail('refresh must not fetch'))

    assert not vast_refresh.refresh_catalog()


def test_refresh_catalog_force_bypasses_fresh_catalog(monkeypatch, tmp_path):
    """A feasibility retry refreshes a valid catalog inside its age window."""
    catalog_path = tmp_path / 'vast' / 'vms.csv'
    catalog_path.parent.mkdir()
    _write_catalog(catalog_path)
    monkeypatch.setattr(vast_refresh.catalog_common, 'get_catalog_path',
                        lambda _name: str(catalog_path))
    monkeypatch.setattr(vast_refresh, 'has_credentials', lambda: True)
    fetch_catalog = mock.Mock(return_value=object())
    monkeypatch.setattr(fetch_vast, 'fetch_vast_catalog', fetch_catalog)
    monkeypatch.setattr(fetch_vast, 'save_catalog',
                        lambda _rows, output: _write_catalog(Path(output)))

    assert vast_refresh.refresh_catalog(force=True)
    fetch_catalog.assert_called_once_with()
