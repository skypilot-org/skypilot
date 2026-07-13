"""Tests for the dynamic OpenStack flavor catalog."""

# pylint: disable=protected-access

import csv
import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

import pytest


def _import_catalog():
    return importlib.import_module('sky.catalog.openstack_catalog')


def _patch_catalog_dir(monkeypatch, catalog, tmp_path: Path):

    def get_catalog_path(filename: str) -> str:
        return str(tmp_path / filename)

    monkeypatch.setattr(catalog.common, 'get_catalog_path', get_catalog_path)


def _connection(*,
                project_id='project-a',
                region='RegionOne',
                flavors=None,
                zones=None):
    if flavors is None:
        flavors = [
            SimpleNamespace(id='flavor-1',
                            name='m1.small',
                            vcpus=2,
                            ram=4096,
                            disk=20),
            SimpleNamespace(id='flavor-2',
                            name='m1.large',
                            vcpus=8,
                            ram=16384,
                            disk=80),
        ]
    if zones is None:
        zones = [
            SimpleNamespace(name='nova', state={'available': True}),
            SimpleNamespace(name='maintenance', state={'available': False}),
        ]
    compute = SimpleNamespace(
        flavors=mock.Mock(return_value=iter(flavors)),
        availability_zones=mock.Mock(return_value=iter(zones)),
    )
    return SimpleNamespace(current_project_id=project_id,
                           config=SimpleNamespace(region_name=region),
                           compute=compute)


def _read_rows(path: str):
    with open(path, encoding='utf-8', newline='') as file:
        return list(csv.DictReader(file))


def test_import_catalog_does_not_import_sdk_or_contact_openstack():
    result = subprocess.run(
        [
            sys.executable, '-c',
            'import sys; import sky.catalog.openstack_catalog; '
            'assert \'openstack\' not in sys.modules'
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_refresh_catalog_records_flavor_region_zone_and_root_disk(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    connection = _connection()

    path = catalog.refresh_catalog('lab', connection=connection)

    rows = _read_rows(path)
    assert rows == [
        {
            'InstanceType': 'm1.large',
            'FlavorId': 'flavor-2',
            'AcceleratorName': '',
            'AcceleratorCount': '',
            'vCPUs': '8.0',
            'MemoryGiB': '16.0',
            'GpuInfo': '',
            'Price': '0.0',
            'SpotPrice': '',
            'Region': 'RegionOne',
            'AvailabilityZone': 'nova',
            'RootDiskGiB': '80.0',
        },
        {
            'InstanceType': 'm1.small',
            'FlavorId': 'flavor-1',
            'AcceleratorName': '',
            'AcceleratorCount': '',
            'vCPUs': '2.0',
            'MemoryGiB': '4.0',
            'GpuInfo': '',
            'Price': '0.0',
            'SpotPrice': '',
            'Region': 'RegionOne',
            'AvailabilityZone': 'nova',
            'RootDiskGiB': '20.0',
        },
    ]
    connection.compute.flavors.assert_called_once_with(details=True)
    connection.compute.availability_zones.assert_called_once_with()
    relative = os.path.relpath(path, tmp_path)
    assert Path(relative).parent == Path('openstack')


def test_refresh_accepts_mapping_sdk_results_and_normalizes_context(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    flavor = {
        'id': 'flavor-1',
        'name': ' m1.small ',
        'vcpus': 2,
        'ram': 4096,
        'disk': 20,
    }
    zone = {
        'zone_name': ' nova ',
        'zone_state': {
            'available': True
        },
    }
    connection = _connection(project_id=' project-a ',
                             region=' RegionOne ',
                             flavors=[flavor],
                             zones=[zone])

    inferred_path = catalog.refresh_catalog(' lab ', connection=connection)
    explicit_path = catalog.get_catalog_path('lab', 'project-a', 'RegionOne')

    assert inferred_path == explicit_path
    assert _read_rows(inferred_path)[0]['AvailabilityZone'] == 'nova'


def test_catalog_paths_are_isolated_by_profile_project_and_region(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)

    paths = {
        catalog.get_catalog_path('lab', 'project-a', 'RegionOne'),
        catalog.get_catalog_path('other', 'project-a', 'RegionOne', 'nova'),
        catalog.get_catalog_path('lab', 'project-b', 'RegionOne', 'nova'),
        catalog.get_catalog_path('lab', 'project-a', 'RegionTwo', 'nova'),
    }

    assert len(paths) == 4
    assert catalog.get_catalog_path('lab', 'project-a', 'RegionOne',
                                    'nova') == catalog.get_catalog_path(
                                        'lab', 'project-a', 'RegionOne', 'edge')
    assert all(Path(path).parent == tmp_path / 'openstack' for path in paths)


def test_refresh_records_every_available_zone(monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    zones = [
        SimpleNamespace(name='nova', state={'available': True}),
        SimpleNamespace(name='edge', state={'available': True}),
    ]

    path = catalog.refresh_catalog('lab', connection=_connection(zones=zones))

    assert {(row['InstanceType'], row['AvailabilityZone'])
            for row in _read_rows(path)} == {
                ('m1.small', 'nova'),
                ('m1.small', 'edge'),
                ('m1.large', 'nova'),
                ('m1.large', 'edge'),
            }
    regions = catalog.regions()
    assert [(region.name, sorted(zone.name
                                 for zone in region.zones))
            for region in regions] == [('RegionOne', ['edge', 'nova'])]


def test_refresh_excludes_nova_internal_service_zone(monkeypatch,
                                                     tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    zones = [
        SimpleNamespace(name='internal', state={'available': True}),
        SimpleNamespace(name='nova', state={'available': True}),
    ]

    path = catalog.refresh_catalog('lab', connection=_connection(zones=zones))

    assert {row['AvailabilityZone'] for row in _read_rows(path)} == {'nova'}


def test_refresh_rejects_internal_service_zone_as_only_zone(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    zones = [SimpleNamespace(name='internal', state={'available': True})]

    with pytest.raises(ValueError, match='availability zone'):
        catalog.refresh_catalog('lab', connection=_connection(zones=zones))


def test_switching_context_invalidates_in_memory_dataframe(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    path_a = catalog.refresh_catalog(
        'lab',
        project_id='project-a',
        region='RegionOne',
        availability_zone='nova',
        connection=_connection(flavors=[
            SimpleNamespace(id='a', name='a.small', vcpus=2, ram=2048, disk=20)
        ]))
    path_b = catalog.refresh_catalog(
        'lab',
        project_id='project-b',
        region='RegionOne',
        availability_zone='nova',
        connection=_connection(flavors=[
            SimpleNamespace(id='b', name='b.small', vcpus=2, ram=2048, disk=20)
        ]))

    catalog.set_catalog_context('lab', 'project-a', 'RegionOne', 'nova')
    assert catalog.instance_type_exists('a.small')
    assert not catalog.instance_type_exists('b.small')
    catalog.set_catalog_context('lab', 'project-b', 'RegionOne', 'nova')
    assert catalog.instance_type_exists('b.small')
    assert not catalog.instance_type_exists('a.small')
    assert path_a != path_b


def test_catalog_reload_detects_refresh_from_another_worker(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    path = catalog.refresh_catalog(
        'lab',
        connection=_connection(flavors=[
            SimpleNamespace(id='a', name='a.small', vcpus=2, ram=2048, disk=20)
        ]))
    assert catalog.instance_type_exists('a.small')

    replacement_rows = catalog._flavor_rows(
        _connection(flavors=[
            SimpleNamespace(id='b', name='b.small', vcpus=2, ram=2048, disk=20)
        ]), 'RegionOne', ['nova'])
    catalog._write_catalog_atomically(path, replacement_rows)
    catalog._write_active_context_atomically(
        catalog._CatalogContext('lab', 'project-a', 'RegionOne'))

    assert catalog.instance_type_exists('b.small')
    assert not catalog.instance_type_exists('a.small')


def test_catalog_context_survives_process_state_reset(monkeypatch,
                                                      tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    catalog.refresh_catalog('lab', connection=_connection())

    monkeypatch.setattr(catalog, '_active_context', None)
    monkeypatch.setattr(catalog, '_active_catalog_path', None)
    monkeypatch.setattr(catalog, '_df', None)

    assert catalog.instance_type_exists('m1.small')


def test_refresh_replaces_catalog_atomically(monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    path = catalog.get_catalog_path('lab', 'project-a', 'RegionOne', 'nova')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text('previous-catalog\n', encoding='utf-8')
    real_replace = os.replace
    replacements = []

    def record_replace(source, destination):
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(catalog.os, 'replace', record_replace)

    catalog.refresh_catalog('lab',
                            project_id='project-a',
                            region='RegionOne',
                            availability_zone='nova',
                            connection=_connection())

    assert {destination for _, destination in replacements} == {
        path,
        catalog._active_context_path(),
    }
    source, destination = next(item for item in replacements if item[1] == path)
    assert Path(source).parent == Path(destination).parent
    assert _read_rows(path)[0]['InstanceType'] == 'm1.large'


def test_failed_atomic_replace_preserves_previous_catalog(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    path = catalog.get_catalog_path('lab', 'project-a', 'RegionOne', 'nova')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text('previous-catalog\n', encoding='utf-8')

    def fail_replace(source, destination):
        del source, destination
        raise OSError('simulated replace failure')

    monkeypatch.setattr(catalog.os, 'replace', fail_replace)

    with pytest.raises(OSError, match='simulated replace failure'):
        catalog.refresh_catalog('lab',
                                project_id='project-a',
                                region='RegionOne',
                                availability_zone='nova',
                                connection=_connection())

    assert Path(path).read_text(encoding='utf-8') == 'previous-catalog\n'
    assert not list(Path(path).parent.glob('*.tmp'))


def test_catalog_queries_use_only_refreshed_local_data(monkeypatch,
                                                       tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    connection = _connection()
    catalog.refresh_catalog('lab', connection=connection)
    connection.compute.flavors.reset_mock()
    connection.compute.availability_zones.reset_mock()

    assert catalog.instance_type_exists('m1.small')
    assert catalog.get_vcpus_mem_from_instance_type('m1.small') == (2.0, 4.0)
    assert catalog.get_hourly_cost('m1.small', False, 'RegionOne',
                                   'nova') == 0.0
    assert catalog.get_default_instance_type(cpus='2',
                                             memory='4',
                                             region='RegionOne',
                                             zone='nova') == 'm1.small'
    assert catalog.get_root_disk_size('m1.small') == 20.0
    regions = catalog.regions()
    assert [(region.name, [zone.name
                           for zone in region.zones])
            for region in regions] == [('RegionOne', ['nova'])]
    connection.compute.flavors.assert_not_called()
    connection.compute.availability_zones.assert_not_called()


def test_default_instance_type_requires_four_gib_per_vcpu(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    flavors = [
        SimpleNamespace(id='low',
                        name='a.low-memory',
                        vcpus=2,
                        ram=2048,
                        disk=20),
        SimpleNamespace(id='general',
                        name='b.general',
                        vcpus=4,
                        ram=16384,
                        disk=40),
    ]
    catalog.refresh_catalog('lab', connection=_connection(flavors=flavors))

    assert catalog.get_default_instance_type() == 'b.general'
    assert catalog.get_default_instance_type(cpus='2+') == 'b.general'


def test_default_instance_type_selects_smallest_matching_flavor(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    flavors = [
        SimpleNamespace(id='huge',
                        name='a.huge',
                        vcpus=64,
                        ram=262144,
                        disk=200),
        SimpleNamespace(id='small', name='z.small', vcpus=2, ram=8192, disk=20),
    ]
    catalog.refresh_catalog('lab', connection=_connection(flavors=flavors))

    assert catalog.get_default_instance_type() == 'z.small'
    assert catalog.get_default_instance_type(cpus='2+',
                                             memory='4x') == 'z.small'


def test_default_instance_type_skips_flavor_with_small_root_disk(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    flavors = [
        SimpleNamespace(id='small', name='a.small', vcpus=2, ram=8192, disk=20),
        SimpleNamespace(id='large-disk',
                        name='b.large-disk',
                        vcpus=2,
                        ram=8192,
                        disk=100),
    ]
    catalog.refresh_catalog('lab', connection=_connection(flavors=flavors))

    assert catalog.get_default_instance_type(cpus='2+',
                                             memory='4x',
                                             min_disk_size=80) == 'b.large-disk'
    assert catalog.get_default_instance_type(
        cpus='2+', memory='4x', min_disk_size=120) is None


def test_cpu_flavors_have_no_architecture_or_local_disk(monkeypatch,
                                                        tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    catalog.refresh_catalog('lab', connection=_connection())

    assert catalog.get_arch_from_instance_type('m1.small') is None
    assert catalog.get_local_disk_from_instance_type('m1.small') is None
    with pytest.raises(ValueError, match='missing'):
        catalog.get_arch_from_instance_type('missing')
    with pytest.raises(ValueError, match='missing'):
        catalog.get_local_disk_from_instance_type('missing')


def test_disk_size_larger_than_flavor_root_disk_is_rejected(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    catalog.refresh_catalog('lab', connection=_connection())

    catalog.check_disk_size('m1.small', 20)
    with pytest.raises(ValueError,
                       match=r'm1\.small.*20.*requested disk size 30'):
        catalog.check_disk_size('m1.small', 30)


def test_refresh_requires_resolvable_project_region_and_available_zone(
        monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)

    with pytest.raises(ValueError, match='project ID'):
        catalog.refresh_catalog('lab', connection=_connection(project_id=None))
    with pytest.raises(ValueError, match='region'):
        catalog.refresh_catalog('lab', connection=_connection(region=None))
    with pytest.raises(ValueError, match='availability zone'):
        catalog.refresh_catalog('lab', connection=_connection(zones=[]))


def test_cpu_only_catalog_reports_no_accelerators(monkeypatch, tmp_path: Path):
    catalog = _import_catalog()
    _patch_catalog_dir(monkeypatch, catalog, tmp_path)
    catalog.refresh_catalog('lab', connection=_connection())

    assert catalog.get_accelerators_from_instance_type('m1.small') is None
    assert catalog.get_instance_type_for_accelerator('A100', 1) == (None, [])
    assert catalog.list_accelerators(True, None, None, None) == {}
