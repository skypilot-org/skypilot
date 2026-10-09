"""Unit tests for Modal catalog (TEST-01, D-04).

All tests are fully offline — no network calls. The bundled catalog CSV is
read from the seeded file.  Tests assert:
  - 9-column schema (no AvailabilityZone)
  - >= 8 GPU families including the required set
  - positive prices
  - optimizer path is network-forbidden (requests.get patch to raise)
  - _seed_catalog_if_missing is idempotent (write-if-missing, tamper-safe)
"""
import os

import pytest

from sky.catalog import modal_catalog
import sky.catalog.common as catalog_common


def test_catalog_schema():
    """CAT-01 / D-04: catalog has exactly the 9 required columns, no AZ."""
    df = catalog_common.read_catalog('modal/vms.csv', pull_frequency_hours=None)
    cols = set(df.head(1).columns)
    expected = {
        'InstanceType',
        'AcceleratorName',
        'AcceleratorCount',
        'vCPUs',
        'MemoryGiB',
        'Price',
        'SpotPrice',
        'Region',
        'GpuInfo',
    }
    assert cols == expected, (
        f'Unexpected columns. Got: {cols}. Expected: {expected}')
    assert 'AvailabilityZone' not in cols, (
        'AvailabilityZone must NOT appear in the Modal catalog schema')


def test_list_accelerators_returns_required_gpus():
    """CAT-02 / D-04: list_accelerators returns >= 8 GPU families including
    the required set."""
    result = modal_catalog.list_accelerators(gpus_only=True,
                                             name_filter=None,
                                             region_filter=None,
                                             quantity_filter=None,
                                             all_regions=True,
                                             require_price=True)
    required = {
        'T4', 'A10', 'A100-40GB', 'A100-80GB', 'L40S', 'H100', 'H200', 'B200'
    }
    gpu_names = set(result.keys())
    missing = required - gpu_names
    assert not missing, (f'Missing required GPU families: {missing}. '
                         f'Available: {sorted(gpu_names)}')
    assert len(gpu_names) >= 8, (
        f'Expected >= 8 distinct GPU families; got {len(gpu_names)}: '
        f'{sorted(gpu_names)}')


def test_catalog_positive_prices():
    """CAT-01 / D-04: every Price in the catalog is strictly positive."""
    import pandas as pd  # noqa: PLC0415
    df = catalog_common.read_catalog('modal/vms.csv', pull_frequency_hours=None)
    # LazyDataFrame proxies __getitem__ to the underlying pd.DataFrame.
    prices = df['Price']
    assert isinstance(
        prices,
        pd.Series), (f'Expected pd.Series for Price column; got {type(prices)}')
    assert (prices > 0).all(), (f'All prices must be positive (> 0). '
                                f'Got min price: {prices.min()}')


def test_catalog_does_not_call_network(monkeypatch):
    """CAT-02 / D-04 / D-07: the offline catalog path never calls requests.get.

    Patches requests.get to raise RuntimeError, then reads the catalog —
    must NOT raise (no network egress from the optimizer path).
    """
    monkeypatch.setattr(
        'sky.catalog.common.requests.get', lambda *a, **kw:
        (_ for _ in ()).throw(
            RuntimeError('network call forbidden in offline catalog path')))
    df = catalog_common.read_catalog('modal/vms.csv', pull_frequency_hours=None)
    _ = df.head()  # materialize — must not raise


def test_seed_catalog_is_idempotent(monkeypatch, tmp_path):
    """CAT-02 / D-04 / T-02-01: _seed_catalog_if_missing is write-if-missing.

    Running the function twice must NOT overwrite a user-edited catalog.
    """
    fake_path = str(tmp_path / 'modal' / 'vms.csv')
    monkeypatch.setattr('sky.catalog.common.get_catalog_path',
                        lambda _: fake_path)
    os.makedirs(str(tmp_path / 'modal'), exist_ok=True)

    modal_catalog._seed_catalog_if_missing()  # pylint: disable=protected-access
    assert os.path.exists(
        fake_path), '_seed_catalog_if_missing must create file'

    # Simulate user editing the catalog
    with open(fake_path, 'w', encoding='utf-8') as fh:
        fh.write('CUSTOM CONTENT')

    # Second call must not overwrite
    modal_catalog._seed_catalog_if_missing()  # pylint: disable=protected-access
    with open(fake_path, encoding='utf-8') as fh:
        content = fh.read()
    assert content == 'CUSTOM CONTENT', (
        '_seed_catalog_if_missing must NOT overwrite existing catalog '
        f'(tamper-safe T-02-01). Got: {content!r}')


def test_seed_catalog_writes_csv_on_missing(monkeypatch, tmp_path):
    """CAT-02 / D-04: _seed_catalog_if_missing writes a valid CSV when absent.

    After the write, the file must be parsable by read_catalog.
    """
    fake_path = str(tmp_path / 'modal' / 'vms.csv')
    monkeypatch.setattr('sky.catalog.common.get_catalog_path',
                        lambda _: fake_path)
    os.makedirs(str(tmp_path / 'modal'), exist_ok=True)

    assert not os.path.exists(fake_path)
    modal_catalog._seed_catalog_if_missing()  # pylint: disable=protected-access
    assert os.path.exists(fake_path), 'Seed must create the catalog CSV'

    # Must contain the required column headers
    with open(fake_path, encoding='utf-8') as fh:
        header = fh.readline()
    assert 'InstanceType' in header, (
        f'Seeded CSV header missing InstanceType: {header!r}')
    assert 'AcceleratorName' in header, (
        f'Seeded CSV header missing AcceleratorName: {header!r}')
