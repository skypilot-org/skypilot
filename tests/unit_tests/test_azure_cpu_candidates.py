"""Azure CPU selection and native capacity failover."""
# pylint: disable=protected-access,redefined-outer-name

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from sky import optimizer
from sky import Resources
from sky import Task
from sky.catalog import azure_catalog
from sky.clouds import Azure
from sky.utils.resources_utils import DiskTier


@pytest.fixture
def azure_cpu(monkeypatch):
    rows = [
        ('Standard_D2s_v5', 2, 8, 0.10, 0.04),
        ('Standard_D2s_v6', 2, 8, 0.11, 0.03),
        ('Standard_D2s_v7', 2, 8, 0.12, 0.02),
        ('Standard_D4s_v6', 4, 16, 0.20, 0.08),
        ('Standard_D8s_v6', 8, 32, 0.40, 0.10),
        ('Standard_F2s_v2', 2, 4, 0.09, 0.025),
        ('Standard_D2_v5', 2, 8, 0.08, 0.015),
        ('Standard_D2ps_v6', 2, 8, 0.06, 0.01),
        ('Standard_NC2_v3', 2, 8, 0.05, 0.01),
        ('Standard_D2s_v8', 2, 8, None, None),
    ]
    frame = pd.DataFrame(
        rows,
        columns=['InstanceType', 'vCPUs', 'MemoryGiB', 'Price', 'SpotPrice'])
    frame['Region'] = 'southcentralus'
    frame['AcceleratorName'] = None
    frame['AcceleratorCount'] = None
    other_region = frame.iloc[[2]].assign(Region='eastus', Price=0.01)
    monkeypatch.setattr(azure_catalog, '_df',
                        pd.concat([frame, other_region], ignore_index=True))
    eligible = set(frame.InstanceType) - {'Standard_D2ps_v6', 'Standard_NC2_v3'}
    monkeypatch.setattr(azure_catalog,
                        'get_cpu_instance_types',
                        lambda region: eligible,
                        raising=False)
    monkeypatch.setattr(
        azure_catalog,
        'get_instance_type_zones',
        lambda name, region: ['1'] if name == 'Standard_D2s_v5' else ['2'],
        raising=False,
    )
    monkeypatch.setattr(azure_catalog, 'validate_region_zone',
                        lambda region, zone: (region, zone))
    return SimpleNamespace(
        catalog=azure_catalog,
        feasible=Azure._get_feasible_launchable_resources,
        fill=optimizer._fill_in_launchable_resources,
    )


@pytest.mark.parametrize(
    ('constraints', 'expected'),
    [
        ({
            'cpus': '2',
            'memory': '4+',
            'zone': '2'
        }, ['Standard_F2s_v2', 'Standard_D2s_v6', 'Standard_D2s_v7']),
        ({
            'cpus': '2',
            'memory': '8'
        }, ['Standard_D2s_v5', 'Standard_D2s_v6', 'Standard_D2s_v7']),
        ({
            'cpus': '2+',
            'memory': '4x',
            'zone': '2'
        }, [
            'Standard_D2s_v6', 'Standard_D2s_v7', 'Standard_D4s_v6',
            'Standard_D8s_v6'
        ]),
        ({
            'cpus': '2',
            'memory': '4+',
            'max_hourly_cost': 0.10
        }, ['Standard_F2s_v2', 'Standard_D2s_v5']),
        ({
            'cpus': '2',
            'memory': '4+',
            'use_spot': True,
            'max_hourly_cost': 0.025
        }, ['Standard_D2s_v7', 'Standard_F2s_v2']),
        ({}, ['Standard_D8s_v6']),
        ({
            'cpus': '64'
        }, []),
        ({
            'cpus': '2',
            'zone': '3'
        }, []),
    ],
)
def test_all_cpu_candidates_obey_native_constraints(azure_cpu, constraints,
                                                    expected):

    options = dict(region='southcentralus',
                   disk_tier=DiskTier.MEDIUM,
                   **constraints)
    assert azure_cpu.catalog.get_instance_types_for_cpus_mem(
        **options) == expected
    assert azure_cpu.catalog.get_default_instance_type(
        **options) == (expected[0] if expected else None)


def test_native_blocklist_retries_other_cpu_types_with_settings_preserved(
        azure_cpu, monkeypatch):

    requested = Resources(
        cloud=Azure(),
        cpus='2',
        memory='4+',
        region='southcentralus',
        zone='2',
        image_id='Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest',
        disk_tier='medium',
        disk_size=40,
        ports=['22'],
        labels={'purpose': 'repair'},
        max_hourly_cost=0.13,
    )
    candidates = azure_cpu.feasible(Azure(), requested).resources_list
    assert [resource.instance_type for resource in candidates
           ] == ['Standard_F2s_v2', 'Standard_D2s_v6', 'Standard_D2s_v7']
    for candidate in candidates:
        for field in (
                'image_id',
                'disk_tier',
                'disk_size',
                'region',
                'zone',
                'use_spot',
                'ports',
                'labels',
                'max_hourly_cost',
        ):
            assert getattr(candidate, field) == getattr(requested, field)
    monkeypatch.setattr(Resources, 'validate', lambda self: None)
    monkeypatch.setattr(
        Azure,
        'get_feasible_launchable_resources',
        lambda self, resources, num_nodes: azure_cpu.feasible(self, resources),
    )
    monkeypatch.setattr(optimizer.sky_check,
                        'get_cached_enabled_clouds_or_refresh',
                        lambda **kwargs: [Azure()])
    monkeypatch.setattr(optimizer.resources_utils,
                        'make_launchables_for_valid_region_zones',
                        lambda resource: [resource])
    task = Task().set_resources(requested)
    for blocked_count in range(len(candidates) + 1):
        launchable, _, _, _ = azure_cpu.fill(task,
                                             candidates[:blocked_count],
                                             quiet=True)
        selected = [
            resource.instance_type
            for values in launchable.values()
            for resource in values
        ]
        assert selected == ([candidates[blocked_count].instance_type]
                            if blocked_count < len(candidates) else [])


def test_explicit_instance_type_stays_pinned(azure_cpu):

    requested = Resources(cloud=Azure(), instance_type='Standard_D2s_v5')
    candidates = azure_cpu.feasible(Azure(), requested).resources_list
    assert [resource.instance_type for resource in candidates
           ] == ['Standard_D2s_v5']
