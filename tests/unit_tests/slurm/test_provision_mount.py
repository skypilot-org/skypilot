"""Tests for Slurm provision-time storage MOUNT baking.

The Slurm provisioner mounts storage from the persistent batch job (so the
FUSE daemon survives proctrack/cgroup) and reports storage_mounts_synced=True
so the backend skips the runtime mount. These tests cover the template_override
hook and the shared mount-command helper without a real cluster.
"""
from unittest import mock

from sky.data import mounting_utils
from sky.provision import common
from sky.provision.slurm import instance as slurm_instance


def _make_storage(mode, mount_cmd, *, name='bkt', source='s3://bkt',
                  read_only=False):
    """A minimal stand-in for a storage.Storage with one store."""
    store = mock.MagicMock()
    store.mount_command.return_value = mount_cmd
    store.mount_cached_command.return_value = mount_cmd
    storage_obj = mock.MagicMock()
    storage_obj.mode = mode
    storage_obj.stores = {'s3': store}
    storage_obj.name = name
    storage_obj.source = source
    storage_obj.mount_config = mock.MagicMock(read_only=read_only)
    storage_obj.construct = mock.MagicMock()
    return storage_obj


def test_resolve_mount_commands_mount_mode():
    from sky.data import storage as storage_lib
    storage_obj = _make_storage(storage_lib.StorageMode.MOUNT, 'GOOFYS_CMD')
    specs = mounting_utils.resolve_mount_commands({'/mnt/data': storage_obj})
    assert len(specs) == 1
    dst, mount_cmd, action, src = specs[0]
    assert dst == '/mnt/data'
    assert mount_cmd == 'GOOFYS_CMD'
    assert 'Mounting' in action
    assert src == 's3://bkt'
    storage_obj.construct.assert_called_once()


def test_resolve_mount_commands_skips_copy_mode():
    from sky.data import storage as storage_lib

    # COPY mode is not in MOUNTABLE_STORAGE_MODES -> skipped.
    storage_obj = _make_storage(storage_lib.StorageMode.COPY, 'X')
    assert mounting_utils.resolve_mount_commands({'/mnt': storage_obj}) == []


def test_template_override_no_storage_returns_none():
    """No storage -> use the default template unchanged."""
    task = mock.MagicMock()
    task.storage_mounts = {}
    to_provision = mock.MagicMock()
    to_provision.extract_docker_image.return_value = None
    assert slurm_instance.template_override(
        task, to_provision, _extra_launch_context={},
        _is_launched_by_jobs_controller=False) is None


def test_template_override_container_returns_none():
    """Container clusters mount inside the container -> runtime fallback."""
    task = mock.MagicMock()
    to_provision = mock.MagicMock()
    to_provision.extract_docker_image.return_value = 'docker:ubuntu'
    with mock.patch.object(mounting_utils, 'resolve_mount_commands') as m:
        result = slurm_instance.template_override(
            task, to_provision, _extra_launch_context={},
            _is_launched_by_jobs_controller=False)
    assert result is None
    # Should short-circuit before generating mount commands.
    m.assert_not_called()


def test_template_override_bakes_mounts():
    """With MOUNT storage: returns a TemplateSpec injecting the mount shell."""
    task = mock.MagicMock()
    to_provision = mock.MagicMock()
    to_provision.extract_docker_image.return_value = None
    specs = [('/mnt/data', 'GOOFYS_MOUNT_CMD', 'Mounting', 's3://bkt')]
    with mock.patch.object(mounting_utils, 'resolve_mount_commands',
                           return_value=specs), \
         mock.patch('sky.check.get_cloud_credential_file_mounts',
                    return_value={'~/.aws/credentials': '/local/creds'}):
        spec = slurm_instance.template_override(
            task, to_provision, _extra_launch_context={},
            _is_launched_by_jobs_controller=False)
    assert spec is not None
    assert spec.template_path == slurm_instance._SLURM_CLUSTER_CONFIG_TEMPLATE
    assert 'GOOFYS_MOUNT_CMD' in spec.variables['storage_mounts_setup']
    assert '/mnt/data' in spec.variables['storage_mounts_setup']
    assert spec.variables['fuse_required'] is True
    assert spec.variables['storage_mount_creds'] == {
        '~/.aws/credentials': '/local/creds'
    }


def test_runtime_metadata_has_storage_mounts_synced():
    """The narrow flag exists and defaults False."""
    md = common.ProvisionRuntimeMetadata()
    assert md.storage_mounts_synced is False
    assert common.ProvisionRuntimeMetadata(
        storage_mounts_synced=True).storage_mounts_synced is True
