"""Tests for Shadeform catalog CPU instance types."""
# pylint: disable=protected-access
import io
from unittest import mock

import pandas as pd
import pytest

from sky.catalog import common as catalog_common
from sky.catalog import shadeform_catalog
from sky.catalog.data_fetchers import fetch_shadeform


def _instance(*,
              cloud,
              shade_instance_type,
              gpu_type,
              num_gpus,
              vcpus,
              memory_in_gb,
              hourly_price,
              region,
              vram_per_gpu_in_gb=0,
              available=True):
    return {
        'cloud': cloud,
        'shade_instance_type': shade_instance_type,
        'hourly_price': hourly_price,
        'configuration': {
            'gpu_type': gpu_type,
            'num_gpus': num_gpus,
            'vcpus': vcpus,
            'memory_in_gb': memory_in_gb,
            'vram_per_gpu_in_gb': vram_per_gpu_in_gb,
        },
        'availability': [{
            'region': region,
            'available': available,
        }],
    }


@pytest.fixture
def reset_shadeform_df():
    shadeform_catalog._df = None
    yield
    shadeform_catalog._df = None


def test_create_catalog_includes_cpu_rows_with_empty_accelerators(tmp_path):
    payload = {
        'instance_types': [
            _instance(cloud='massedcompute',
                      shade_instance_type='cpu_mini',
                      gpu_type='CPU',
                      num_gpus=0,
                      vcpus=8,
                      memory_in_gb=32,
                      hourly_price=12,
                      region='us-east-5'),
            _instance(cloud='massedcompute',
                      shade_instance_type='A6000',
                      gpu_type='A6000',
                      num_gpus=1,
                      vcpus=8,
                      memory_in_gb=48,
                      hourly_price=49,
                      region='us-east-5',
                      vram_per_gpu_in_gb=48),
            _instance(cloud='massedcompute',
                      shade_instance_type='cpu_unavailable',
                      gpu_type='CPU',
                      num_gpus=0,
                      vcpus=4,
                      memory_in_gb=16,
                      hourly_price=10,
                      region='us-west-1',
                      available=False),
        ]
    }
    mock_response = mock.Mock()
    mock_response.json.return_value = payload
    output_path = tmp_path / 'vms.csv'

    with mock.patch.object(fetch_shadeform.requests,
                           'get',
                           return_value=mock_response) as mock_get:
        fetch_shadeform.create_catalog('fake-key', str(output_path))

    mock_get.assert_called_once()
    df = pd.read_csv(output_path)
    cpu_rows = df[df['InstanceType'] == 'massedcompute_cpu-mini']
    gpu_rows = df[df['InstanceType'] == 'massedcompute_A6000']

    assert len(cpu_rows) == 1
    assert pd.isna(cpu_rows.iloc[0]['AcceleratorName'])
    assert pd.isna(cpu_rows.iloc[0]['AcceleratorCount'])
    assert pd.isna(cpu_rows.iloc[0]['GpuInfo'])
    assert cpu_rows.iloc[0]['Price'] == pytest.approx(0.12)
    assert cpu_rows.iloc[0]['Region'] == 'us-east-5'

    assert len(gpu_rows) == 1
    assert gpu_rows.iloc[0]['AcceleratorName'] == 'A6000'
    assert gpu_rows.iloc[0]['AcceleratorCount'] == 1.0
    assert 'A6000' in str(gpu_rows.iloc[0]['GpuInfo'])

    assert 'cpu_unavailable' not in df['InstanceType'].tolist()


def test_catalog_keeps_cpu_rows_and_empty_accelerators(reset_shadeform_df):
    csv_text = ('InstanceType,AcceleratorName,AcceleratorCount,vCPUs,'
                'MemoryGiB,Price,Region,GpuInfo,SpotPrice\n'
                'massedcompute_cpu-mini,,,8.0,32,0.12,us-east-5,,\n'
                'massedcompute_A6000, A6000 ,1.0,8.0,48,0.49,us-east-5,,\n')
    df = pd.read_csv(io.StringIO(csv_text))

    with mock.patch.object(shadeform_catalog.common,
                           'read_catalog',
                           return_value=df):
        loaded = shadeform_catalog._get_df()

    cpu_rows = loaded[loaded['InstanceType'] == 'massedcompute_cpu-mini']
    assert len(cpu_rows) == 1
    assert pd.isna(cpu_rows.iloc[0]['AcceleratorName'])
    assert cpu_rows.iloc[0]['AcceleratorName'] != 'nan'

    accelerators = catalog_common.get_accelerators_from_instance_type_impl(
        loaded, 'massedcompute_cpu-mini')
    assert accelerators is None

    gpu_name = loaded.loc[loaded['InstanceType'] == 'massedcompute_A6000',
                          'AcceleratorName'].iloc[0]
    assert gpu_name == 'A6000'

    gpu_acc = catalog_common.get_accelerators_from_instance_type_impl(
        loaded, 'massedcompute_A6000')
    assert gpu_acc == {'A6000': 1}

    cheapest = catalog_common.get_instance_type_for_cpus_mem_impl(
        loaded, cpus='2+', memory_gb_or_ratio=None)
    assert cheapest == 'massedcompute_cpu-mini'
