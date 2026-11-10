import importlib.util
from unittest import mock

import jsonschema
import pytest

from sky.adaptors import nebius as nebius_adaptor
from sky.provision.nebius import utils as nebius_utils
from sky.utils import resources_utils
from sky.utils import schemas

_HAS_NEBIUS_SDK = importlib.util.find_spec('nebius') is not None
nebius_sdk_required = pytest.mark.skipif(not _HAS_NEBIUS_SDK,
                                         reason='Nebius SDK is not installed')


@pytest.mark.parametrize(
    'config',
    [
        {
            'nebius': {
                'disk_encrypted': True
            }
        },
        {
            'nebius': {
                'region_configs': {
                    'us-central1': {
                        'disk_encrypted': True
                    }
                }
            }
        },
    ],
)
def test_disk_encrypted_config_accepts_bool(config):
    jsonschema.validate(instance=config, schema=schemas.get_config_schema())


def test_disk_encrypted_config_rejects_non_bool():
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(
            instance={'nebius': {
                'disk_encrypted': 'yes'
            }},
            schema=schemas.get_config_schema(),
        )


def _launch_disk_spec(disk_tier, disk_encrypted=False):
    """
    Build a Nebius launch request and return its boot disk specification.
    """
    compute = nebius_adaptor.compute()
    service = mock.MagicMock()
    instance = mock.MagicMock()
    instance.metadata.id = 'instance-id'
    instance.status.state.name = 'STARTING'

    with (
            mock.patch.object(nebius_utils,
                              'get_project_by_region',
                              return_value='project-id'),
            mock.patch.object(nebius_utils,
                              'get_subnet_id',
                              return_value='subnet-id'),
            mock.patch.object(nebius_adaptor, 'sdk'),
            mock.patch.object(compute,
                              'InstanceServiceClient',
                              return_value=service),
            mock.patch.object(nebius_adaptor,
                              'sync_call',
                              side_effect=[None, instance]),
    ):
        nebius_utils.launch(
            cluster_name_on_cloud='cluster',
            node_type='head',
            platform='gpu-l40s',
            preset='1gpu-16vcpu-64gb',
            region='us-central1',
            image_id_or_family='computeimage-test',
            disk_size=93,
            user_data='',
            associate_public_ip_address=False,
            filesystems=[],
            disk_tier=disk_tier,
            disk_encrypted=disk_encrypted,
        )

    request = service.create.call_args.args[0]
    return request.spec.boot_disk.managed_disk.spec


@nebius_sdk_required
@pytest.mark.parametrize(
    ('disk_tier', 'expected_disk_type'),
    [
        (resources_utils.DiskTier.HIGH, 'NETWORK_SSD_IO_M3'),
        (resources_utils.DiskTier.LOW, 'NETWORK_SSD_NON_REPLICATED'),
    ],
)
def test_encrypts_supported_disk_types(disk_tier, expected_disk_type):
    compute = nebius_adaptor.compute()
    disk_spec = _launch_disk_spec(disk_tier, disk_encrypted=True)

    assert disk_spec.type.name == expected_disk_type
    assert disk_spec.disk_encryption.type == (
        compute.DiskEncryption.DiskEncryptionType.DISK_ENCRYPTION_MANAGED)


@nebius_sdk_required
@pytest.mark.parametrize(
    'disk_tier',
    [
        resources_utils.DiskTier.HIGH,
        resources_utils.DiskTier.LOW,
    ],
)
def test_disk_encryption_defaults_disabled(disk_tier):
    compute = nebius_adaptor.compute()
    disk_spec = _launch_disk_spec(disk_tier)

    assert disk_spec.disk_encryption.type == (
        compute.DiskEncryption.DiskEncryptionType.DISK_ENCRYPTION_UNSPECIFIED)


@nebius_sdk_required
def test_standard_ssd_does_not_set_optional_encryption(caplog):
    compute = nebius_adaptor.compute()
    disk_spec = _launch_disk_spec(resources_utils.DiskTier.MEDIUM,
                                  disk_encrypted=True)

    assert disk_spec.type == compute.DiskSpec.DiskType.NETWORK_SSD
    assert disk_spec.disk_encryption.type == (
        compute.DiskEncryption.DiskEncryptionType.DISK_ENCRYPTION_UNSPECIFIED)
    assert ('does not support explicitly configured Nebius-managed encryption'
            in caplog.text)
