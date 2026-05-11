"""Unit tests for the Vast live-price overlay."""
# pylint: disable=protected-access
import math
from unittest import mock

import pandas as pd
import pytest

from sky.catalog.vast import _offer_processing as proc
from sky.catalog.vast import live_overlay


@pytest.fixture(autouse=True)
def _disable_background_refresh(monkeypatch):
    """Never spawn refresh threads from unit tests."""
    monkeypatch.setattr(live_overlay, '_TEST_REFRESH_DISABLED', True)
    # Reset latched state between tests.
    monkeypatch.setattr(live_overlay, '_OVERLAY_DISABLED', False)
    monkeypatch.setattr(live_overlay, '_DISABLED_BY_ENV', False)
    live_overlay._MEMO.clear()
    yield
    live_overlay._MEMO.clear()


# ---------------------------------------------------------------------------
# _offer_processing
# ---------------------------------------------------------------------------
def _fake_offer(num_gpus=1,
                gpu_name='RTX 5090',
                cpu_cores=32,
                cpu_ram=65536,
                price=0.50,
                spot=0.10,
                region='Spain, ES, EU',
                hosting_type=0,
                gpu_total_ram=32607):
    return {
        'num_gpus': num_gpus,
        'gpu_name': gpu_name,
        'cpu_cores': cpu_cores,
        'cpu_ram': cpu_ram,
        'gpu_total_ram': gpu_total_ram,
        'search': {
            'totalHour': price
        },
        'min_bid': spot,
        'geolocation': region,
        'hosting_type': hosting_type,
    }


def test_make_instance_type_matches_legacy_format():
    # The legacy format from fetch_vast.create_instance_type was:
    #   '{num_gpus}x-{stubify(gpu_name)}-{cpu_cores}-{cpu_ram}'
    assert proc.make_instance_type(2, 'A100 SXM4', 64, 524288) == \
        '2x-A100_SXM4-64-524288'


def test_normalize_gpu_name_known_renames():
    assert proc.normalize_gpu_name('TeslaV100') == 'V100'
    assert proc.normalize_gpu_name('TeslaT4') == 'T4'
    # Drops trailing PCIE/SXM/Ti/NVL.
    assert proc.normalize_gpu_name('A100 SXM4') == 'A100'
    # 'Ada' gets a hyphen prefix.
    assert proc.normalize_gpu_name('RTX 6000Ada') == 'RTX6000-Ada'


def test_offers_to_dataframe_typed_columns():
    # The median-price + 'seen' dedup pipeline (mirrors fetch_vast.py)
    # emits one row per unique (InstanceType, country, hosting_type) tuple
    # only when at least two offers tie at the median price. Provide two
    # at identical prices to land in the bucket.
    offers = [_fake_offer(price=0.50), _fake_offer(price=0.50)]
    df = proc.offers_to_dataframe(offers)
    assert df.shape[0] == 1
    assert 'InstanceType' in df.columns
    assert df['Price'].dtype.kind == 'f'
    assert df['SpotPrice'].dtype.kind == 'f'
    assert df['HostingType'].iloc[0] == 0
    # GpuInfo is single-quoted so list_accelerators_impl can ast.literal_eval.
    assert df['GpuInfo'].iloc[0].startswith('{\'Gpus\':')


def test_offers_to_dataframe_drops_malformed_offers():
    bad = {'gpu_name': 'X'}  # missing num_gpus etc.
    good_a = _fake_offer(price=0.50)
    good_b = _fake_offer(price=0.50)
    df = proc.offers_to_dataframe([bad, good_a, good_b])
    assert df.shape[0] == 1
    assert df['AcceleratorName'].iloc[0] == 'RTX5090'


# ---------------------------------------------------------------------------
# merge_with_base
# ---------------------------------------------------------------------------
def _csv_row(it='1x-H100-32-65536',
             region='Spain, ES, EU',
             hosting_type=0,
             price=2.50,
             spot=0.00,
             accel='H100',
             accel_count=1,
             vcpus=32,
             memory=64.0,
             gpu_info=('{\'Gpus\': [{\'Name\': \'H100\', \'Count\': 1, '
                       '\'MemoryInfo\': {\'SizeInMiB\': 81920}}]}')):
    return {
        'InstanceType': it,
        'AcceleratorName': accel,
        'AcceleratorCount': accel_count,
        'vCPUs': vcpus,
        'MemoryGiB': memory,
        'GpuInfo': gpu_info,
        'Price': price,
        'SpotPrice': spot,
        'Region': region,
        'HostingType': hosting_type,
    }


_KW_TO_COL = {
    'it': 'InstanceType',
    'region': 'Region',
    'hosting_type': 'HostingType',
    'price': 'Price',
    'spot': 'SpotPrice',
    'accel': 'AcceleratorName',
    'accel_count': 'AcceleratorCount',
    'vcpus': 'vCPUs',
    'memory': 'MemoryGiB',
    'gpu_info': 'GpuInfo',
}


def _live_row(**overrides):
    base = _csv_row()
    for k, v in overrides.items():
        base[_KW_TO_COL.get(k, k)] = v
    return base


def test_merge_both_sides_takes_min_price():
    base = pd.DataFrame([_csv_row(price=2.50)])
    overlay = pd.DataFrame([_live_row(price=1.99)])
    out = live_overlay.merge_with_base(base, overlay)
    assert out.shape[0] == 1
    assert math.isclose(out.iloc[0]['Price'], 1.99)


def test_merge_treats_zero_spot_as_missing():
    base = pd.DataFrame([_csv_row(price=2.50, spot=0.00)])
    overlay = pd.DataFrame([_live_row(price=2.50, spot=0.30)])
    out = live_overlay.merge_with_base(base, overlay)
    # 0 from CSV must be ignored; live's 0.30 wins.
    assert math.isclose(out.iloc[0]['SpotPrice'], 0.30)


def test_merge_appends_live_only_rows():
    base = pd.DataFrame([_csv_row(it='1x-H100-32-65536')])
    overlay = pd.DataFrame(
        [_live_row(it='2x-RTX5090-64-131072', accel='RTX5090', accel_count=2)])
    out = live_overlay.merge_with_base(base, overlay)
    assert set(
        out['InstanceType']) == {'1x-H100-32-65536', '2x-RTX5090-64-131072'}


def test_merge_keeps_csv_only_rows():
    base = pd.DataFrame([
        _csv_row(it='1x-H100-32-65536'),
        _csv_row(it='1x-T4-32-65536', accel='T4'),
    ])
    overlay = pd.DataFrame([_live_row(it='1x-H100-32-65536', price=1.50)])
    out = live_overlay.merge_with_base(base, overlay)
    types = sorted(out['InstanceType'].tolist())
    assert types == ['1x-H100-32-65536', '1x-T4-32-65536']


def test_merge_returns_base_when_overlay_missing_columns():
    base = pd.DataFrame([_csv_row()])
    overlay = pd.DataFrame([{'InstanceType': 'X'}])  # missing keys
    out = live_overlay.merge_with_base(base, overlay)
    pd.testing.assert_frame_equal(out, base)


def test_merge_returns_base_when_overlay_none_or_empty():
    base = pd.DataFrame([_csv_row()])
    pd.testing.assert_frame_equal(live_overlay.merge_with_base(base, None),
                                  base)
    pd.testing.assert_frame_equal(
        live_overlay.merge_with_base(base, pd.DataFrame()), base)


# ---------------------------------------------------------------------------
# _search_offers_safely
# ---------------------------------------------------------------------------
def test_search_offers_safely_handles_int_payload(monkeypatch):
    fake_sdk = mock.Mock()
    fake_sdk.search_offers.return_value = 1  # SDK error sentinel
    monkeypatch.setattr('sky.adaptors.vast.vast', lambda: fake_sdk)
    assert live_overlay._search_offers_safely() is None


def test_search_offers_safely_latches_disabled_on_signature_drift(monkeypatch):
    fake_sdk = mock.Mock()
    fake_sdk.search_offers.side_effect = TypeError('unexpected kwarg')
    monkeypatch.setattr('sky.adaptors.vast.vast', lambda: fake_sdk)
    assert live_overlay._search_offers_safely() is None
    assert live_overlay._OVERLAY_DISABLED is True


def test_search_offers_safely_swallows_runtime_errors(monkeypatch):
    fake_sdk = mock.Mock()
    fake_sdk.search_offers.side_effect = RuntimeError('timeout')
    monkeypatch.setattr('sky.adaptors.vast.vast', lambda: fake_sdk)
    assert live_overlay._search_offers_safely() is None
    # RuntimeError must NOT latch the overlay off.
    assert live_overlay._OVERLAY_DISABLED is False


def test_search_offers_safely_handles_missing_sdk(monkeypatch):

    def _raise():
        raise ImportError('no vastai_sdk')

    monkeypatch.setattr('sky.adaptors.vast.vast', _raise)
    assert live_overlay._search_offers_safely() is None


# ---------------------------------------------------------------------------
# OverlayDataFrame end-to-end
# ---------------------------------------------------------------------------
def test_overlay_dataframe_returns_base_when_overlay_disabled(monkeypatch):
    base_df = pd.DataFrame([_csv_row(price=2.50)])
    fake_lazy = mock.Mock()
    fake_lazy.get_dataframe.return_value = base_df
    fake_lazy.filename = '/tmp/does-not-exist.csv'

    monkeypatch.setattr(live_overlay, '_DISABLED_BY_ENV', True)
    odf = live_overlay.OverlayDataFrame(fake_lazy)
    pd.testing.assert_frame_equal(odf._df(), base_df)


def test_overlay_dataframe_splices_live_prices(monkeypatch):
    base_df = pd.DataFrame([_csv_row(price=2.50)])
    overlay_df = pd.DataFrame([_live_row(price=1.20)])

    fake_lazy = mock.Mock()
    fake_lazy.get_dataframe.return_value = base_df
    fake_lazy.filename = '/tmp/does-not-exist.csv'

    monkeypatch.setattr(live_overlay, 'get_overlay_dataframe',
                        lambda: overlay_df)

    odf = live_overlay.OverlayDataFrame(fake_lazy)
    out = odf._df()
    assert math.isclose(out.iloc[0]['Price'], 1.20)


# ---------------------------------------------------------------------------
# End-to-end: a populated overlay cache must surface through vast_catalog.
# ---------------------------------------------------------------------------
def test_end_to_end_overlay_surfaces_lower_price_and_new_inventory(
        monkeypatch, tmp_path):
    # pylint: disable=import-outside-toplevel
    from sky.catalog import vast_catalog

    # Build a base df with one known instance type and one HostingType=1 row.
    base = pd.DataFrame([
        _csv_row(it='1x-RTX_5090-32-65536',
                 accel='RTX5090',
                 region='Spain, ES, EU',
                 hosting_type=0,
                 price=2.50,
                 spot=0.00),
        _csv_row(it='1x-A100-32-65536',
                 accel='A100',
                 region='Virginia, US, NA',
                 hosting_type=1,
                 price=3.00,
                 spot=0.00),
    ])

    # Live overlay: cheaper price for the existing row + a brand-new IT.
    overlay = pd.DataFrame([
        _live_row(it='1x-RTX_5090-32-65536',
                  accel='RTX5090',
                  region='Spain, ES, EU',
                  hosting_type=0,
                  price=1.20,
                  spot=0.00),
        _live_row(it='99x-FAKEGPU-32-65536',
                  accel='FAKEGPU',
                  accel_count=99,
                  region='Mars, MR, ZZ',
                  hosting_type=1,
                  price=42.00,
                  spot=0.00),
    ])

    # Wire both into the live overlay machinery without touching disk.
    fake_lazy = mock.Mock()
    fake_lazy.get_dataframe.return_value = base
    fake_lazy.filename = str(tmp_path / 'fake_base.csv')
    (tmp_path / 'fake_base.csv').write_text('placeholder')

    monkeypatch.setattr(live_overlay, 'get_overlay_dataframe', lambda: overlay)
    monkeypatch.setattr(vast_catalog, '_df',
                        live_overlay.OverlayDataFrame(fake_lazy))
    live_overlay._MEMO.clear()

    # 1. Cheaper live price wins through get_hourly_cost.
    price = vast_catalog.get_hourly_cost('1x-RTX_5090-32-65536', use_spot=False)
    assert math.isclose(price, 1.20)

    # 2. Live-only inventory is reachable via get_instance_type_for_accelerator.
    inst, _ = vast_catalog.get_instance_type_for_accelerator('FAKEGPU', 99)
    assert inst == ['99x-FAKEGPU-32-65536']

    # 3. Boolean indexing through OverlayDataFrame for the datacenter filter.
    dc_only = vast_catalog._apply_datacenter_filter(vast_catalog._df,
                                                    datacenter_only=True)
    instance_types = sorted(set(dc_only['InstanceType'].tolist()))
    assert '1x-A100-32-65536' in instance_types
    assert '99x-FAKEGPU-32-65536' in instance_types
    # The HostingType=0 row must be filtered out.
    assert '1x-RTX_5090-32-65536' not in instance_types


# ---------------------------------------------------------------------------
# Legacy-equivalence: the refactored row builder must produce a row that
# matches what the historical fetch_vast logic produced for the same offer.
# This locks down the (InstanceType, Region, HostingType) merge key.
# ---------------------------------------------------------------------------
def test_build_csv_rows_legacy_equivalence():
    # Two duplicate offers at the same price guarantee one emitted row,
    # mirroring how the live catalog has many machines per (IT, country,
    # hosting) tuple.
    offers = [
        _fake_offer(num_gpus=1,
                    gpu_name='RTX 5090',
                    cpu_cores=32,
                    cpu_ram=65536,
                    price=0.47,
                    spot=0.00,
                    region='Spain, ES, EU',
                    hosting_type=0,
                    gpu_total_ram=32607),
        _fake_offer(num_gpus=1,
                    gpu_name='RTX 5090',
                    cpu_cores=32,
                    cpu_ram=65536,
                    price=0.47,
                    spot=0.00,
                    region='Spain, ES, EU',
                    hosting_type=0,
                    gpu_total_ram=32607),
    ]
    rows = proc.build_csv_rows(offers)
    assert len(rows) == 1
    row = rows[0]
    # Merge-key columns (these are what live_overlay.merge_with_base joins on)
    assert row['InstanceType'] == '1x-RTX_5090-32-65536'
    assert row['Region'] == 'Spain, ES, EU'
    assert row['HostingType'] == 0
    # Other catalog columns expected by downstream readers.
    assert row['AcceleratorName'] == 'RTX5090'
    assert row['AcceleratorCount'] == 1
    assert row['vCPUs'] == 32
    # cpu_ram in MiB -> GiB
    assert row['MemoryGiB'] == 64.0
    # Single-quoted dict so list_accelerators_impl can ast.literal_eval it.
    assert row['GpuInfo'].startswith('{\'Gpus\':')
    assert '\'Name\': \'RTX5090\'' in row['GpuInfo']
    # Median price formatted as 2-decimal string (CSV format).
    assert row['Price'] == '0.47'
    assert row['SpotPrice'] == '0.00'
