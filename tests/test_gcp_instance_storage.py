"""Family-aware G4 local-SSD unit counts for instance-storage volumes.

Red-first against the 2026-09-30 prod-usw2 incident (RTX PRO 6000 lane,
g4-standard-48 walk): one requested instance-storage volume rendered ONE
local-ssd disk in the bulkInsert request, which GCP rejects outright for the
G4 family — "The selected machine type(g4-standard-48) should have [0, 4]
local SSD(s)." (HTTP 400 badRequest, every zone, instantly) — because G4
attaches its Titanium SSD in 4-unit groups on -48 and 8-unit groups on -96.
Every bulkInsert died, sky rotated zones internally and never surfaced a
terminal failure, the runner's stockout ledger never re-armed, and the walk
never left GCP. After the fix, one requested instance-storage volume expands
to the family's full unit group (4 on -48, 8 on -96); only the first device
carries the mount point (the JuiceFS block cache mounts the first NVMe
device), the rest are attached and auto-deleted with the instance.
"""

import pytest

from sky import exceptions
from sky.clouds import gcp
from sky.provision.gcp import constants as gcp_constants
from sky.utils import resources_utils


@pytest.fixture
def _gcp_project(monkeypatch):
    monkeypatch.setattr(gcp.GCP, 'get_project_id',
                        classmethod(lambda cls, dryrun=False: 'test-project'))


_VOLUME = {
    'path': '/mnt/jfs-nvme',
    'storage_type': resources_utils.StorageType.INSTANCE,
    'auto_delete': True,
}


def _specs(instance_type):
    return gcp.GCP._get_volumes_specs(
        region=None,
        zones=None,
        instance_type=instance_type,
        volumes=[dict(_VOLUME)],
        use_mig=False,
        tpu_vm=False,
    )


def test_g4_standard_48_expands_to_four_local_ssds(_gcp_project):
    """The live 400 receipt: g4-standard-48 allows [0, 4] local SSDs, so ONE
    requested instance-storage volume must expand to 4 local-ssd devices.
    Pre-fix this emitted 1 and the bulkInsert 400'd in every zone."""
    volumes_specs, device_mount_points = _specs('g4-standard-48')
    assert len(volumes_specs) == 4
    for spec in volumes_specs:
        assert spec['disk_tier'] == gcp_constants.INSTANCE_STORAGE_DISK_TYPE
        assert spec['storage_type'] == gcp_constants.INSTANCE_STORAGE_TYPE
        assert spec['interface_type'] == (
            gcp_constants.INSTANCE_STORAGE_INTERFACE_TYPE)
        assert spec['disk_size'] is None
        assert spec['auto_delete'] is True
    # Only the FIRST device carries the cache mount point.
    assert device_mount_points == {
        '/dev/disk/by-id/google-local-nvme-ssd-0': '/mnt/jfs-nvme'
    }


def test_g4_standard_96_expands_to_eight_local_ssds(_gcp_project):
    """g4-standard-96 allows [0, 8] — the same receipt failed the -96 family
    pre-#2925 with 'should have [0, 8] local SSD(s).'"""
    volumes_specs, device_mount_points = _specs('g4-standard-96')
    assert len(volumes_specs) == 8
    assert device_mount_points == {
        '/dev/disk/by-id/google-local-nvme-ssd-0': '/mnt/jfs-nvme'
    }


def test_non_g4_type_keeps_single_device(_gcp_project):
    """Families with no unit-group constraint keep the historical
    one-volume-one-device behavior (n2 has no local SSD table entry)."""
    volumes_specs, device_mount_points = _specs('n2-standard-8')
    assert len(volumes_specs) == 1
    assert device_mount_points == {
        '/dev/disk/by-id/google-local-nvme-ssd-0': '/mnt/jfs-nvme'
    }


def test_auto_attach_types_still_skip_disk_requests(_gcp_project):
    """The SSD_AUTO_ATTACH path is untouched: those families' devices are
    attached by GCP itself, so no disk is requested at all."""
    volumes_specs, device_mount_points = _specs('c3d-standard-60-lssd')
    assert volumes_specs == []
    assert device_mount_points == {
        '/dev/disk/by-id/google-local-nvme-ssd-0': '/mnt/jfs-nvme'
    }


def test_second_instance_volume_over_group_budget_fails_loud(_gcp_project):
    """g4-standard-48 attaches exactly 0 or 4 local SSDs — a config whose
    instance volumes would need MORE than the family budget must fail at
    config time (ResourcesUnavailableError), never at the provider."""
    volumes = [
        dict(_VOLUME, path='/mnt/jfs-nvme'),
        dict(_VOLUME, path='/mnt/scratch'),
    ]
    with pytest.raises(exceptions.ResourcesUnavailableError):
        gcp.GCP._get_volumes_specs(
            region=None,
            zones=None,
            instance_type='g4-standard-48',
            volumes=volumes,
            use_mig=False,
            tpu_vm=False,
        )


def test_second_instance_volume_within_budget_stacks(_gcp_project):
    """Within the family budget the group is shared: on g4-standard-96 two
    instance-storage volumes expand to 8 + 8 = 16 > 8? No — the budget is 8,
    so this configuration also fails loud. Use a non-budgeted pair instead:
    two volumes on an unconstrained family stay 1 + 1 with sequential device
    ids (attach order is global across the machine)."""
    volumes = [
        dict(_VOLUME, path='/mnt/jfs-nvme'),
        dict(_VOLUME, path='/mnt/scratch'),
    ]
    volumes_specs, device_mount_points = gcp.GCP._get_volumes_specs(
        region=None,
        zones=None,
        instance_type='n2-standard-8',
        volumes=volumes,
        use_mig=False,
        tpu_vm=False,
    )
    assert len(volumes_specs) == 2
    assert device_mount_points == {
        '/dev/disk/by-id/google-local-nvme-ssd-0': '/mnt/jfs-nvme',
        '/dev/disk/by-id/google-local-nvme-ssd-1': '/mnt/scratch',
    }
