"""Managed Instance Group Utils"""
import re
import subprocess
from typing import Any, Dict

from sky import sky_logging
from sky.adaptors import gcp
from sky.provision.gcp import constants

logger = sky_logging.init_logger(__name__)

MIG_RESOURCE_NOT_FOUND_PATTERN = re.compile(
    r'The resource \'projects/.*/zones/.*/instanceGroupManagers/.*\' was not '
    r'found')

IT_RESOURCE_NOT_FOUND_PATTERN = re.compile(
    r'The resource \'projects/.*/regions/.*/instanceTemplates/.*\' was not '
    'found')


def get_instance_template_name(cluster_name: str) -> str:
    return f'{constants.INSTANCE_TEMPLATE_NAME_PREFIX}{cluster_name}'


def get_managed_instance_group_name(cluster_name: str) -> str:
    return f'{constants.MIG_NAME_PREFIX}{cluster_name}'


def check_instance_template_exits(project_id: str, region: str,
                                  template_name: str) -> bool:
    compute = gcp.build('compute',
                        'v1',
                        credentials=None,
                        cache_discovery=False)
    try:
        compute.regionInstanceTemplates().get(
            project=project_id, region=region,
            instanceTemplate=template_name).execute()
    except gcp.http_error_exception() as e:
        if IT_RESOURCE_NOT_FOUND_PATTERN.search(str(e)) is not None:
            # Instance template does not exist.
            return False
        raise
    return True


def create_region_instance_template(cluster_name_on_cloud: str, project_id: str,
                                    region: str, template_name: str,
                                    node_config: Dict[str, Any]) -> dict:
    """Create a regional instance template."""
    logger.debug(f'Creating regional instance template {template_name!r}.')
    compute = gcp.build('compute',
                        'v1',
                        credentials=None,
                        cache_discovery=False)
    config = node_config.copy()
    config.pop(constants.MANAGED_INSTANCE_GROUP_CONFIG, None)

    # We have to ignore user defined scheduling for DWS.
    # TODO: Add a warning log for this behvaiour.
    scheduling = config.get('scheduling', {})
    assert scheduling.get('provisioningModel') != 'SPOT', (
        'DWS does not support spot VMs.')

    reservations_affinity = config.pop('reservation_affinity', None)
    if reservations_affinity is not None:
        logger.warning(
            f'Ignoring reservations_affinity {reservations_affinity} '
            'for DWS.')

    # Create the regional instance template request
    operation = compute.regionInstanceTemplates().insert(
        project=project_id,
        region=region,
        body={
            'name': template_name,
            'properties': dict(
                description=(
                    'SkyPilot instance template for '
                    f'{cluster_name_on_cloud!r} to support DWS requests.'),
                reservationAffinity=dict(
                    consumeReservationType='NO_RESERVATION'),
                **config,
            )
        }).execute()
    return operation


def create_managed_instance_group(project_id: str, zone: str, group_name: str,
                                  instance_template_url: str,
                                  size: int) -> dict:
    logger.debug(f'Creating managed instance group {group_name!r}.')
    compute = gcp.build('compute',
                        'v1',
                        credentials=None,
                        cache_discovery=False)
    operation = compute.instanceGroupManagers().insert(
        project=project_id,
        zone=zone,
        body={
            'name': group_name,
            'instanceTemplate': instance_template_url,
            'target_size': size,
            'instanceLifecyclePolicy': {
                'defaultActionOnFailure': 'DO_NOTHING',
            },
            'updatePolicy': {
                'type': 'OPPORTUNISTIC',
            },
        }).execute()
    return operation


def resize_managed_instance_group(project_id: str, zone: str, group_name: str,
                                  resize_by: int, run_duration: int) -> dict:
    logger.debug(f'Resizing managed instance group {group_name!r} by '
                 f'{resize_by} with run duration {run_duration}.')
    compute = gcp.build('compute',
                        'beta',
                        credentials=None,
                        cache_discovery=False)
    operation = compute.instanceGroupManagerResizeRequests().insert(
        project=project_id,
        zone=zone,
        instanceGroupManager=group_name,
        body={
            'name': group_name,
            'resizeBy': resize_by,
            'requestedRunDuration': {
                'seconds': run_duration,
            }
        }).execute()
    return operation


def cancel_all_resize_request_for_mig(project_id: str, zone: str,
                                      group_name: str) -> None:
    logger.debug(f'Cancelling all resize requests for MIG {group_name!r}.')
    try:
        compute = gcp.build('compute',
                            'beta',
                            credentials=None,
                            cache_discovery=False)
        operation = compute.instanceGroupManagerResizeRequests().list(
            project=project_id,
            zone=zone,
            instanceGroupManager=group_name,
            filter='state eq ACCEPTED').execute()
        for request in operation.get('items', []):
            try:
                compute.instanceGroupManagerResizeRequests().cancel(
                    project=project_id,
                    zone=zone,
                    instanceGroupManager=group_name,
                    resizeRequest=request['name']).execute()
            except gcp.http_error_exception() as e:
                logger.warning('Failed to cancel resize request '
                               f'{request["id"]!r}: {e}')
    except gcp.http_error_exception() as e:
        if re.search(MIG_RESOURCE_NOT_FOUND_PATTERN, str(e)) is None:
            raise
        logger.warning(f'MIG {group_name!r} does not exist. Skip '
                       'resize request cancellation.')
        logger.debug(f'Error: {e}')


def check_managed_instance_group_exists(project_id: str, zone: str,
                                        group_name: str) -> bool:
    compute = gcp.build('compute',
                        'v1',
                        credentials=None,
                        cache_discovery=False)
    try:
        compute.instanceGroupManagers().get(
            project=project_id, zone=zone,
            instanceGroupManager=group_name).execute()
    except gcp.http_error_exception() as e:
        if MIG_RESOURCE_NOT_FOUND_PATTERN.search(str(e)) is not None:
            return False
        raise
    return True


def wait_for_managed_group_to_be_stable(project_id: str, zone: str,
                                        group_name: str, timeout: int) -> None:
    """Wait until the managed instance group is stable."""
    logger.debug(f'Waiting for MIG {group_name} to be stable with timeout '
                 f'{timeout}.')
    try:
        cmd = ('gcloud compute instance-groups managed wait-until '
               f'{group_name} '
               '--stable '
               f'--zone={zone} '
               f'--project={project_id} '
               f'--timeout={timeout}')
        logger.info(
            f'Waiting for MIG {group_name} to be stable with command:\n{cmd}')
        proc = subprocess.run(
            f'yes | {cmd}',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            check=True,
        )
        stdout = proc.stdout.decode('ascii')
        logger.info(stdout)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode('ascii')
        logger.info(stderr)
        raise
