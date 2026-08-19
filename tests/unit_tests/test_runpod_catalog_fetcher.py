"""Regression tests for RunPod GPU-to-data-center catalog rows."""

from sky.catalog.data_fetchers import fetch_runpod


def _gpu_details(gpu_id: str, gpu_count: int):
    del gpu_id
    return {
        'displayName': 'A40',
        'manufacturer': 'NVIDIA',
        'memoryInGb': 48,
        'secureCloud': True,
        'secureSpotPrice': 0.1,
        'securePrice': 0.2,
        'lowestPrice': {
            'minVcpu': 9,
            'minMemory': 48,
        },
        'gpuCount': gpu_count,
    }


def test_gpu_catalog_rows_only_use_v2_available_data_centers(monkeypatch):
    """Prevent the catalog from inventing GPU-zone pairs absent from v2."""
    monkeypatch.setattr(fetch_runpod, 'get_gpu_details', _gpu_details)
    monkeypatch.setattr(fetch_runpod, 'get_launchable_region_zones', lambda: {
        'AU': ['OC-AU-1'],
        'US': ['US-CA-1'],
    })

    instances = fetch_runpod.get_gpu_instance_configurations(
        'NVIDIA A40', {1: {
            'NVIDIA A40': {'OC-AU-1'}
        }})

    assert [(instance['Region'], instance['AvailabilityZone'])
            for instance in instances] == [('AU', 'OC-AU-1')]
