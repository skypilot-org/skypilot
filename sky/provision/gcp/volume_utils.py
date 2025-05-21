"""Utilities for GCP volumes."""
from typing import Any, Dict, List, Optional

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.adaptors import gcp
from sky.provision.gcp import constants
from sky.utils import resources_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


def get_data_disk_tier_mapping(
    instance_type: Optional[str],) -> Dict[resources_utils.DiskTier, str]:
    # Define the default mapping from disk tiers to disk types.
    # Refer to https://cloud.google.com/compute/docs/disks/hyperdisks
    # and https://cloud.google.com/compute/docs/disks/persistent-disks
    tier2name = {
        resources_utils.DiskTier.ULTRA: 'pd-extreme',
        resources_utils.DiskTier.HIGH: 'pd-ssd',
        resources_utils.DiskTier.MEDIUM: 'pd-balanced',
        resources_utils.DiskTier.LOW: 'pd-standard',
    }

    if instance_type is None:
        return tier2name

    # Remap series-specific disk types.
    series = instance_type.split('-')[0]

    if series in ['a4', 'x4']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.MEDIUM] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.LOW] = 'hyperdisk-balanced'
    elif series in ['m4']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.MEDIUM] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.LOW] = 'hyperdisk-balanced'
        num_cpus = int(instance_type.split('-')[2])  # type: ignore
        if num_cpus < 112:
            tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-balanced'
    elif series in ['c4', 'c4a', 'c4d']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.MEDIUM] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.LOW] = 'hyperdisk-balanced'
        num_cpus = int(instance_type.split('-')[2])  # type: ignore
        if num_cpus < 64:
            tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-balanced'
    elif series in ['a3']:
        if (instance_type.startswith('a3-ultragpu') or
                instance_type.startswith('a3-megagpu') or
                instance_type.startswith('a3-edgegpu')):
            tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
            tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
            tier2name[resources_utils.DiskTier.MEDIUM] = 'hyperdisk-balanced'
            tier2name[resources_utils.DiskTier.LOW] = 'hyperdisk-balanced'
        elif instance_type.startswith('a3-highgpu'):
            tier2name[resources_utils.DiskTier.LOW] = 'pd-balanced'
            if instance_type.startswith('a3-highgpu-8g'):
                tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
                tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
                tier2name[resources_utils.DiskTier.MEDIUM] = 'pd-ssd'
            elif instance_type.startswith('a3-highgpu-4g'):
                tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
            else:
                tier2name[resources_utils.DiskTier.ULTRA] = 'pd-ssd'
    elif series in ['c3d']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.MEDIUM] = 'pd-ssd'
        tier2name[resources_utils.DiskTier.LOW] = 'pd-balanced'
        num_cpus = int(instance_type.split('-')[2])  # type: ignore
        if num_cpus < 60:
            tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-balanced'
    elif series in ['c3']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.MEDIUM] = 'pd-ssd'
        tier2name[resources_utils.DiskTier.LOW] = 'pd-balanced'
        num_cpus = int(instance_type.split('-')[2])  # type: ignore
        if num_cpus < 88:
            tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-balanced'
    elif series in ['n4']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.MEDIUM] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.LOW] = 'hyperdisk-balanced'
    elif series in ['n2d', 'n1', 't2d', 't2a', 'e2', 'c2', 'c2d', 'a2']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'pd-ssd'
    elif series in ['z3']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
        tier2name[resources_utils.DiskTier.LOW] = 'pd-balanced'
    elif series in ['h3']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.LOW] = 'pd-balanced'
    elif series in ['m3']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
        tier2name[resources_utils.DiskTier.MEDIUM] = 'pd-ssd'
        tier2name[resources_utils.DiskTier.LOW] = 'pd-balanced'
        num_cpus = int(instance_type.split('-')[2])  # type: ignore
        if num_cpus < 64:
            tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-balanced'
    elif series in ['m2']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
    elif series in ['m1']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'
        tier2name[resources_utils.DiskTier.HIGH] = 'hyperdisk-balanced'
        num_cpus = int(instance_type.split('-')[2])  # type: ignore
        if num_cpus < 80:
            tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-balanced'
    elif series in ['g2']:
        tier2name[resources_utils.DiskTier.ULTRA] = 'pd-ssd'
        tier2name[resources_utils.DiskTier.LOW] = 'pd-balanced'
    elif series in ['n2']:
        num_cpus = int(instance_type.split('-')[2])  # type: ignore
        if num_cpus < 64:
            tier2name[resources_utils.DiskTier.ULTRA] = 'pd-ssd'
        elif num_cpus >= 80:
            tier2name[resources_utils.DiskTier.ULTRA] = 'hyperdisk-extreme'

    return tier2name


def validate_instance_volumes(
    instance_type: Optional[str],
    volumes: Optional[List[Dict[str, Any]]],
) -> None:
    if not volumes:
        return
    if instance_type is None:
        logger.warning('Instance type is not specified,'
                       ' skipping instance volume validation')
        return
    instance_volume_count = 0
    for volume in volumes:
        if volume['storage_type'] == resources_utils.StorageType.INSTANCE:
            instance_volume_count += 1
    if (instance_type in constants.SSD_AUTO_ATTACH_MACHINE_TYPES and
            instance_volume_count >
            constants.SSD_AUTO_ATTACH_MACHINE_TYPES[instance_type]):
        raise exceptions.ResourcesUnavailableError(
            f'The instance type {instance_type} supports'
            f' {constants.SSD_AUTO_ATTACH_MACHINE_TYPES[instance_type]}'
            f'  instance storage, but {instance_volume_count} are specified')
    # TODO(hailong):
    # check the instance storage count for the other instance types,
    # refer to https://cloud.google.com/compute/docs/disks/local-ssd


def translate_attach_mode(attach_mode: resources_utils.DiskAttachMode) -> str:
    if attach_mode == resources_utils.DiskAttachMode.READ_ONLY:
        return 'READ_ONLY'
    return 'READ_WRITE'


def check_volume_name(project_id: str, region: clouds.Region,
                      zones: Optional[List[clouds.Zone]], use_mig: bool,
                      volume_name: str) -> Dict[str, Any]:
    """Check if the volume name exists and return the volume info."""
    logger.debug(
        f'Checking volume {volume_name} in region {region} and zones {zones}')
    try:
        compute = gcp.build('compute',
                            'v1',
                            credentials=None,
                            cache_discovery=False)
    except gcp.credential_error_exception():
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Not able to build compute client') from None

    # If zones is None, check the region disk
    # If zones is not None, check zone disks first, then region disk
    if zones is None:
        # Set zones to an empty list to normalize the logic below
        zones = []
    volume_info = None
    for zone in zones:
        try:
            volume_info = compute.disks().get(project=project_id,
                                              zone=zone.name,
                                              disk=volume_name).execute()
            if volume_info is not None:
                if use_mig:
                    # With MIG, instance template will be used, in this case,
                    # the `selfLink` for zonal disk needs to be the volume name
                    # Refer to https://cloud.google.com/compute/docs/
                    # reference/rest/v1/instances/insert
                    volume_info['selfLink'] = volume_name
                return volume_info
        except gcp.http_error_exception() as e:
            if e.resp.status == 403:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('Not able to access the volume '
                                     f'{volume_name!r}') from None
            if e.resp.status == 404:
                continue  # Try next zone
            raise

    # If not found in any zone, check region disk
    try:
        volume_info = compute.regionDisks().get(project=project_id,
                                                region=region.name,
                                                disk=volume_name).execute()
        # Check the zones are in the `replicaZones` of the region disk
        # 'replicaZones':
        #  ['https://xxx/compute/v1/projects/sky-dev-465/zones/us-central1-a',
        # 'https://xxx/compute/v1/projects/sky-dev-465/zones/us-central1-c']
        replica_zones = [
            zone.split('/')[-1] for zone in volume_info['replicaZones']
        ]
        for zone in zones:
            if zone.name not in replica_zones:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.DiskInstanceZoneMismatchError(
                        f'Zone {zone.name} is not in the `replicaZones`'
                        f' {replica_zones} of the region disk {volume_name}')
        return volume_info
    except exceptions.DiskInstanceZoneMismatchError as e:
        raise exceptions.ResourcesUnavailableError(str(e)) from None
    except gcp.http_error_exception() as e:
        if e.resp.status == 403:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Not able to access the volume '
                                 f'{volume_name!r}') from None
        if e.resp.status == 404:
            with ux_utils.print_exception_no_traceback():
                # Return a ResourcesUnavailableError to trigger failover
                raise exceptions.ResourcesUnavailableError(
                    f'Volume {volume_name} not found in region {region}'
                    f' or zones {zones}') from None
        raise
