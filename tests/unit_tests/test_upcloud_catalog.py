"""Tests for the UpCloud catalog."""
# pylint: disable=protected-access

import io

import pandas as pd
import pytest

from sky.catalog import upcloud_catalog

_L40S = 'GPU-8xCPU-64GB-1xL40S'
_CPU = 'CLOUDNATIVE-2xCPU-4GB'
_CATALOG = """\
InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,Price,Region,GpuInfo,SpotPrice
CLOUDNATIVE-2xCPU-4GB,,,2,4.0,0.02,fi-hel2,,
GPU-8xCPU-64GB-1xL40S,L40S,1,8,64.0,1.0,fi-hel2,"{'Gpus': [{'Name': 'L40S', 'Manufacturer': 'NVIDIA', 'Count': 1.0, 'MemoryInfo': {'SizeInMiB': 46068}}], 'TotalGpuMemoryInMiB': 46068}",0.5
"""


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
    monkeypatch.setattr(upcloud_catalog, '_df',
                        pd.read_csv(io.StringIO(_CATALOG)))


def test_instance_type_exists():
    assert upcloud_catalog.instance_type_exists(_L40S)
    assert not upcloud_catalog.instance_type_exists('does-not-exist')


def test_hourly_cost_on_demand_and_spot():
    on_demand = upcloud_catalog.get_hourly_cost(_L40S,
                                                use_spot=False,
                                                region='fi-hel2')
    spot = upcloud_catalog.get_hourly_cost(_L40S,
                                           use_spot=True,
                                           region='fi-hel2')
    assert on_demand == 1.0
    assert spot == 0.5


def test_vcpus_mem_and_accelerators():
    assert upcloud_catalog.get_vcpus_mem_from_instance_type(_L40S) == (8, 64.0)
    assert upcloud_catalog.get_accelerators_from_instance_type(_L40S) == {
        'L40S': 1
    }
    assert upcloud_catalog.get_accelerators_from_instance_type(_CPU) is None


def test_instance_type_for_accelerator():
    instance_types, _ = upcloud_catalog.get_instance_type_for_accelerator(
        'L40S', 1)
    assert instance_types and _L40S in instance_types
    spot_types, _ = upcloud_catalog.get_instance_type_for_accelerator(
        'L40S', 1, use_spot=True)
    assert spot_types and _L40S in spot_types
    none, fuzzy = upcloud_catalog.get_instance_type_for_accelerator('T4', 1)
    assert none is None
    assert isinstance(fuzzy, list)


def test_default_instance_type():
    default = upcloud_catalog.get_default_instance_type()
    assert default == _CPU
    assert upcloud_catalog.get_default_instance_type(cpus='8+',
                                                     memory='32+') == _L40S


def test_list_accelerators_and_regions():
    accs = upcloud_catalog.list_accelerators(gpus_only=True,
                                             name_filter=None,
                                             region_filter=None,
                                             quantity_filter=None)
    assert 'L40S' in accs
    assert any(r.name == 'fi-hel2' for r in upcloud_catalog.regions())
