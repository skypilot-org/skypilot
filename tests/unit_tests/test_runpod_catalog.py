"""RunPod CPU disk limits must constrain native resource selection."""
# pylint: disable=protected-access,redefined-outer-name
import pandas as pd
import pytest

import sky
from sky.catalog import runpod_catalog
from sky.clouds import runpod


@pytest.fixture
def cpu_catalog(monkeypatch):
    df = pd.DataFrame([{
        'InstanceType': f'cpu3c-{cpus}-{cpus * 2}',
        'AcceleratorName': None,
        'AcceleratorCount': None,
        'vCPUs': float(cpus),
        'MemoryGiB': float(cpus * 2),
        'MaxDiskSize': float(cpus * 10),
        'Region': 'US',
        'AvailabilityZone': 'US-CA-1',
        'Price': cpus / 100,
        'SpotPrice': None,
    } for cpus in (8, 16, 64)])
    monkeypatch.setattr(runpod_catalog, '_df', df)
    return df


@pytest.mark.parametrize(('disk_size', 'instance'), [
    (80, 'cpu3c-8-16'),
    (81, 'cpu3c-16-32'),
    (500, 'cpu3c-64-128'),
    (641, None),
])
def test_native_selection_respects_cpu_disk_limit(cpu_catalog, disk_size,
                                                  instance):
    del cpu_catalog
    resource = sky.Resources(infra='runpod', cpus='8+', disk_size=disk_size)
    feasible = runpod.RunPod().get_feasible_launchable_resources(resource)
    assert [candidate.instance_type for candidate in feasible.resources_list
           ] == ([] if instance is None else [instance])
    assert all(candidate.disk_size == disk_size
               for candidate in feasible.resources_list)


@pytest.mark.parametrize('constraints', [
    {
        'cpus': '8'
    },
    {
        'cpus': '8+',
        'max_hourly_cost': 0.3
    },
    {
        'cpus': '8+',
        'region': 'EU'
    },
    {
        'cpus': '8+',
        'memory': '256+'
    },
])
def test_disk_filter_preserves_other_constraints(cpu_catalog, constraints):
    del cpu_catalog
    assert runpod_catalog.get_default_instance_type(disk_size=500,
                                                    **constraints) is None


@pytest.mark.parametrize(('instance', 'disk_size', 'accepted'), [
    ('cpu3c-8-16', 80, True),
    ('cpu3c-8-16', 81, False),
    ('cpu3c-8-16', 500, False),
    ('cpu3c-64-128', 500, True),
])
def test_explicit_instance_cannot_bypass_disk_limit(cpu_catalog, instance,
                                                    disk_size, accepted):
    del cpu_catalog
    resource = sky.Resources(infra='runpod',
                             instance_type=instance,
                             disk_size=disk_size)
    feasible = runpod.RunPod().get_feasible_launchable_resources(resource)
    assert bool(feasible.resources_list) is accepted
    if not accepted:
        assert 'disk' in feasible.hint


def test_legacy_catalog_without_disk_column_retains_compatibility(
        cpu_catalog, monkeypatch):
    monkeypatch.setattr(runpod_catalog, '_df',
                        cpu_catalog.drop(columns=['MaxDiskSize']))
    assert runpod_catalog.get_default_instance_type(
        cpus='8+', disk_size=500) == 'cpu3c-8-16'
    assert runpod_catalog.is_disk_size_supported('cpu3c-8-16', 500)


def test_missing_limit_in_new_catalog_does_not_claim_cpu_feasibility(
        cpu_catalog, monkeypatch):
    cpu_catalog.loc[cpu_catalog['vCPUs'] == 8, 'MaxDiskSize'] = None
    monkeypatch.setattr(runpod_catalog, '_df', cpu_catalog)
    assert runpod_catalog.get_default_instance_type(
        cpus='8+', disk_size=80) == 'cpu3c-16-32'
    assert not runpod_catalog.is_disk_size_supported('cpu3c-8-16', 80)


def test_gpu_pods_do_not_inherit_cpu_disk_limits(cpu_catalog, monkeypatch):
    gpu = cpu_catalog.iloc[0].copy()
    gpu['InstanceType'] = '1x_A100-80GB_Secure'
    gpu['AcceleratorName'] = 'A100-80GB'
    gpu['AcceleratorCount'] = 1
    gpu['MaxDiskSize'] = None
    gpu['Price'] = 2.0
    monkeypatch.setattr(
        runpod_catalog, '_df',
        pd.concat([cpu_catalog, pd.DataFrame([gpu])], ignore_index=True))
    resource = sky.Resources(infra='runpod',
                             instance_type='1x_A100-80GB_Secure',
                             disk_size=500)
    feasible = runpod.RunPod().get_feasible_launchable_resources(resource)
    assert [candidate.instance_type for candidate in feasible.resources_list
           ] == ['1x_A100-80GB_Secure']


def test_default_catalog_api_without_disk_requirement_is_unchanged(cpu_catalog):
    del cpu_catalog
    assert runpod_catalog.get_default_instance_type(cpus='8+') == 'cpu3c-8-16'
