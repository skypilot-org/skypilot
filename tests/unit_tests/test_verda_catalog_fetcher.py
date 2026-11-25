"""Tests for the Verda catalog fetcher."""
import pandas as pd

from sky.catalog.data_fetchers import fetch_verda

_INSTANCE_TYPES = [
    {
        'instance_type': '1A6000.10V',
        'price_per_hour': 0.646,
        'spot_price': 0.323,
        'cpu': {
            'number_of_cores': 10
        },
        'memory': {
            'size_in_gigabytes': 60
        },
        'gpu': {
            'number_of_gpus': 1,
            'description': '1x RTX A6000 48GB'
        },
        'gpu_memory': {
            'size_in_gigabytes': 48
        },
        'model': 'RTX A6000',
    },
    {
        'instance_type': 'CPU.4V.16G',
        'price_per_hour': 0.1,
        'cpu': {
            'number_of_cores': 4
        },
        'memory': {
            'size_in_gigabytes': 16
        },
    },
]


def _create_catalog(monkeypatch, tmp_path, on_demand, spot):
    availability = {False: on_demand, True: spot}
    monkeypatch.setenv('VERDA_CLIENT_ID', 'id')
    monkeypatch.setenv('VERDA_CLIENT_SECRET', 'secret')
    monkeypatch.setattr(fetch_verda, '_get_oauth_token', lambda *_: 'token')
    monkeypatch.setattr(fetch_verda, '_fetch_instance_types',
                        lambda *_: _INSTANCE_TYPES)
    monkeypatch.setattr(
        fetch_verda,
        '_fetch_instance_availability',
        lambda base_url, token, is_spot=False: availability[is_spot])
    output_path = tmp_path / 'vms.csv'
    fetch_verda.create_catalog(str(output_path))
    return pd.read_csv(output_path).set_index(['InstanceType', 'Region'])


def test_price_kept_when_momentarily_sold_out_on_demand(monkeypatch, tmp_path):
    # 1A6000.10V is only reported as available for spot in FIN-01.
    df = _create_catalog(monkeypatch,
                         tmp_path,
                         on_demand=[{
                             'location_code': 'FIN-01',
                             'availabilities': ['CPU.4V.16G']
                         }],
                         spot=[{
                             'location_code': 'FIN-01',
                             'availabilities': ['1A6000.10V']
                         }])

    row = df.loc[('1A6000.10V', 'FIN-01')]
    assert row['Price'] == 0.646
    assert row['SpotPrice'] == 0.323


def test_spot_price_kept_when_momentarily_sold_out_spot(monkeypatch, tmp_path):
    df = _create_catalog(monkeypatch,
                         tmp_path,
                         on_demand=[{
                             'location_code': 'FIN-01',
                             'availabilities': ['1A6000.10V', 'CPU.4V.16G']
                         }],
                         spot=[])

    assert df.loc[('1A6000.10V', 'FIN-01')]['SpotPrice'] == 0.323
    # Without an explicit spot price, none is invented for the region.
    cpu_row = df.loc[('CPU.4V.16G', 'FIN-01')]
    assert cpu_row['Price'] == 0.1
    assert pd.isna(cpu_row['SpotPrice'])
