"""Keep authoritative RunPod CPU disk capacity through CSV publication."""
import pandas as pd
import pytest

pytest.importorskip('runpod')

# pylint: disable=wrong-import-position
from sky.catalog.data_fetchers import fetch_runpod


@pytest.mark.parametrize('disk_per_vcpu', [10, 5, None])
def test_cpu_disk_capacity_survives_catalog_csv(monkeypatch, tmp_path,
                                                disk_per_vcpu):
    monkeypatch.setattr(
        fetch_runpod, 'query_cpu_id', lambda _: [{
            'minVcpu': 8,
            'maxVcpu': 16,
            'ramMultiplier': 2,
            'diskLimitPerVcpu': disk_per_vcpu,
        }])
    monkeypatch.setattr(fetch_runpod, 'REGION_ZONES', {'US': ['US-CA-1']})
    monkeypatch.setattr(fetch_runpod, 'query_cpu_specifics', lambda *_: [{
        'specifics': {
            'securePrice': 0.1
        }
    }])
    rows = fetch_runpod.get_cpu_instance_configurations('cpu3c')
    path = tmp_path / 'vms.csv'
    fetch_runpod.save_catalog(rows, str(path))
    result = pd.read_csv(path).sort_values('vCPUs')
    assert list(result['vCPUs']) == [8, 16]
    if disk_per_vcpu is None:
        assert result['MaxDiskSize'].isna().all()
    else:
        assert list(
            result['MaxDiskSize']) == [8 * disk_per_vcpu, 16 * disk_per_vcpu]


def test_gpu_only_catalog_serializes_without_a_cpu_disk_limit(tmp_path):
    info = fetch_runpod.get_gpu_info('A100-80GB', {
        'displayName': 'A100 PCIe',
        'manufacturer': 'NVIDIA',
        'memoryInGb': 80,
    }, 1)
    assert info['MaxDiskSize'] is None
    row = {
        **info,
        'InstanceType': '1x_A100-80GB_Secure',
        'AcceleratorName': 'A100-80GB',
        'AcceleratorCount': 1,
        'Region': 'US',
        'AvailabilityZone': 'US-CA-1',
        'Price': 2.0,
        'SpotPrice': None,
    }
    path = tmp_path / 'vms.csv'
    fetch_runpod.save_catalog([row], str(path))
    assert pd.read_csv(path)['MaxDiskSize'].isna().all()
