"""Managed Instance Group Utils"""
import re
import subprocess
import sys
import typing
from typing import Any, Optional

from sky import sky_logging
from sky.adaptors import gcp
from sky.provision.gcp import constants

if typing.TYPE_CHECKING:
    from google.api_core import extended_operation
    from google.cloud import compute_v1
else:
    from sky.adaptors.gcp import compute_v1

logger = sky_logging.init_logger(__name__)

_MIG_RESOURCE_NOT_FOUND_PATTERN = re.compile(
    r'The resource \'projects/.*/zones/.*/instanceGroupManagers/.*\' was not '
    r'found')

_IT_RESOURCE_NOT_FOUND_PATTERN = re.compile(
    r'The resource \'projects/.*/zones/.*/instanceTemplates/.*\' was not found')


def get_instance_template_name(cluster_name: str) -> str:
    return f'{constants.INSTANCE_TEMPLATE_NAME_PREFIX}{cluster_name}'


def get_managed_instance_group_name(cluster_name: str) -> str:
    return f'{constants.MIG_NAME_PREFIX}{cluster_name}'


def create_regional_instance_template_properties(
        cluster_name_on_cloud, node_config) -> 'compute_v1.InstanceProperties':

    return compute_v1.InstanceProperties(  # pylint: disable=used-before-assignment
        description=('SkyPilot instance template for '
                     f'{cluster_name_on_cloud!r} to support DWS requests.'),
        machine_type=node_config['machineType'],
        # We have to ignore reservations for DWS.
        # TODO: Add a warning log for this behvaiour.
        reservation_affinity=compute_v1.ReservationAffinity(
            consume_reservation_type='NO_RESERVATION'),
        # We have to ignore user defined scheduling for DWS.
        # TODO: Add a warning log for this behvaiour.
        scheduling=compute_v1.Scheduling(on_host_maintenance='TERMINATE'),
        guest_accelerators=[
            compute_v1.AcceleratorConfig(
                accelerator_count=accelerator['acceleratorCount'],
                accelerator_type=accelerator['acceleratorType'].split('/')[-1],
            ) for accelerator in node_config.get('guestAccelerators', [])
        ],
        disks=[
            compute_v1.AttachedDisk(
                boot=disk_config['boot'],
                auto_delete=disk_config['autoDelete'],
                type_=disk_config['type'],
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    source_image=disk_config['initializeParams']['sourceImage'],
                    disk_size_gb=disk_config['initializeParams']['diskSizeGb'],
                    disk_type=disk_config['initializeParams']['diskType'].split(
                        '/')[-1]),
            ) for disk_config in node_config.get('disks', [])
        ],
        network_interfaces=[
            compute_v1.NetworkInterface(
                subnetwork=network_interface['subnetwork'],
                access_configs=[
                    compute_v1.AccessConfig(
                        name=access_config['name'],
                        type=access_config['type'],
                    ) for access_config in network_interface.get(
                        'accessConfigs', [])
                ],
            ) for network_interface in node_config.get('networkInterfaces', [])
        ],
        service_accounts=[
            compute_v1.ServiceAccount(email=service_account['email'],
                                      scopes=service_account['scopes'])
            for service_account in node_config.get('serviceAccounts', [])
        ],
        metadata=compute_v1.Metadata(items=[
            compute_v1.Items(key=item['key'], value=item['value'])
            for item in (node_config.get('metadata', {}).get('items', []) + [{
                'key': 'cluster-name',
                'value': cluster_name_on_cloud
            }])
        ]),
        # Create labels from node config
        labels=node_config.get('labels', {}))


def check_instance_template_exits(project_id, region, template_name) -> bool:
    with compute_v1.RegionInstanceTemplatesClient() as compute_client:
        request = compute_v1.ListRegionInstanceTemplatesRequest(
            filter=f'name eq {template_name}',
            project=project_id,
            region=region,
        )
        page_result = compute_client.list(request)
        return len(page_result.items) > 0 and (next(page_result.pages)
                                               is not None)


def create_regional_instance_template(project_id, region, template_name,
                                      node_config,
                                      cluster_name_on_cloud) -> None:
    with compute_v1.RegionInstanceTemplatesClient() as compute_client:
        # Create the regional instance template request

        request = compute_v1.InsertRegionInstanceTemplateRequest(
            project=project_id,
            region=region,
            instance_template_resource=compute_v1.InstanceTemplate(
                name=template_name,
                properties=create_regional_instance_template_properties(
                    cluster_name_on_cloud, node_config),
            ),
        )

        # Send the request to create the regional instance template
        response = compute_client.insert(request=request)
        # Wait for the operation to complete
        # logger.debug(response)
        wait_for_extended_operation(response,
                                    'create regional instance template', 600)
        # TODO: Error handling
        # operation = compute_client.wait(response.operation)
        # if operation.error:
        # raise Exception(f'Failed to create regional instance template: '
        #                 f'{operation.error}')

        list_request = compute_v1.ListRegionInstanceTemplatesRequest(
            filter=f'name eq {template_name}',
            project=project_id,
            region=region,
        )
        compute_client.list(list_request)
        # logger.debug(list_response)
        logger.debug(f'Regional instance template {template_name!r} '
                     'created successfully.')


def delete_regional_instance_template(
        project_id, zone,
        template_name) -> Optional['extended_operation.ExtendedOperation']:
    region = zone.rsplit('-', 1)[0]
    with compute_v1.RegionInstanceTemplatesClient() as compute_client:
        # Create the regional instance template request
        request = compute_v1.DeleteRegionInstanceTemplateRequest(
            project=project_id,
            region=region,
            instance_template=template_name,
        )
        try:
            # Send the request to delete the regional instance template
            response = compute_client.delete(request=request)
            return response
        except gcp.google.api_core.exceptions.NotFound as e:
            if re.search(_IT_RESOURCE_NOT_FOUND_PATTERN, str(e)) is None:
                raise
            logger.warning(f'Instance template {template_name!r} does not '
                           'exist. Skip deletion.')
        return None


def delete_managed_instance_group(
        project_id, zone,
        group_name) -> Optional['extended_operation.ExtendedOperation']:
    with compute_v1.InstanceGroupManagersClient() as compute_client:
        # Create the managed instance group request
        request = compute_v1.DeleteInstanceGroupManagerRequest(
            project=project_id,
            zone=zone,
            instance_group_manager=group_name,
        )

        try:
            # Send the request to delete the managed instance group
            response = compute_client.delete(request=request)
            # Do not wait for the deletion of MIG, so we can send the deletion
            # request for the instance template, immediately after this, which
            # is important when we are autodown a cluster from the head node.
            return response
        except gcp.google.api_core.exceptions.NotFound as e:
            if re.search(_MIG_RESOURCE_NOT_FOUND_PATTERN, str(e)) is None:
                raise
            logger.warning(f'MIG {group_name!r} does not exist. Skip '
                           'deletion.')
        return None


def create_managed_instance_group(project_id, zone, group_name,
                                  instance_template_url, size) -> None:
    with compute_v1.InstanceGroupManagersClient() as compute_client:
        # Create the managed instance group request
        request = compute_v1.InsertInstanceGroupManagerRequest(
            project=project_id,
            zone=zone,
            instance_group_manager_resource=compute_v1.InstanceGroupManager(
                name=group_name,
                instance_template=instance_template_url,
                target_size=size,
                instance_lifecycle_policy=compute_v1.
                InstanceGroupManagerInstanceLifecyclePolicy(
                    default_action_on_failure='DO_NOTHING',),
                update_policy=compute_v1.InstanceGroupManagerUpdatePolicy(
                    type='OPPORTUNISTIC',),
            ),
        )

        # Send the request to create the managed instance group
        response = compute_client.insert(request=request)

        # Wait for the operation to complete
        logger.debug(
            f'Request submitted, waiting for operation to complete. {response}')
        wait_for_extended_operation(response, 'create managed instance group',
                                    600)
        # TODO: Error handling
        logger.debug(
            f'Managed instance group {group_name!r} created successfully.')


def start_managed_instance_group(project_id, zone, group_name) -> None:
    with compute_v1.InstanceGroupManagersClient() as compute_client:
        # Create the managed instance group request
        request = compute_v1.ApplyUpdatesToInstancesInstanceGroupManagerRequest(
            instance_group_manager=group_name,
            project=project_id,
            zone=zone,
            instance_group_managers_apply_updates_request_resource=compute_v1.
            InstanceGroupManagersApplyUpdatesRequest(
                all_instances=True,
                minimal_action='NONE',
                most_disruptive_allowed_action='RESTART',
            ),
        )

        response = compute_client.apply_updates_to_instances(request)
        wait_for_extended_operation(response, 'restart managed instance group',
                                    600)
        logger.debug('Managed instance group restarted successfully.')


def check_managed_instance_group_exists(project_id, zone, group_name) -> bool:
    with compute_v1.InstanceGroupManagersClient() as compute_client:
        request = compute_v1.ListInstanceGroupManagersRequest(
            project=project_id,
            zone=zone,
            filter=f'name eq {group_name}',
        )
        page_result = compute_client.list(request)
        return len(page_result.items) > 0 and (next(page_result.pages)
                                               is not None)


def resize_managed_instance_group(project_id: str, zone: str, group_name: str,
                                  size: int) -> None:
    # try:
    with compute_v1.InstanceGroupManagersClient() as compute_client:
        response = compute_client.resize(project=project_id,
                                         zone=zone,
                                         instance_group_manager=group_name,
                                         size=size)
        wait_for_extended_operation(
            response,
            'resize managed instance group',
            timeout=constants.DEFAULT_MAANGED_INSTANCE_GROUP_CREATION_TIMEOUT)
    # resize_request_name = f'resize-request-{str(int(time.time()))}'

    # cmd = (
    #     f'gcloud beta compute instance-groups managed resize-requests '
    #     f'create {group_name} '
    #     f'--resize-request={resize_request_name} '
    #     f'--resize-by={size} '
    #     f'--requested-run-duration={run_duration} '
    #     f'--zone={zone} '
    #     f'--project={project_id} ')
    # logger.info(f'Resizing MIG {group_name} with command:\n{cmd}')
    # proc = subprocess.run(
    #     f'yes | {cmd}',
    #     stdout=subprocess.PIPE,
    #     stderr=subprocess.PIPE,
    #     shell=True,
    #     check=True,
    # )
    # stdout = proc.stdout.decode('ascii')
    # logger.info(stdout)
    wait_for_managed_group_to_be_stable(project_id, zone, group_name)

    # except subprocess.CalledProcessError as e:
    #     stderr = e.stderr.decode('ascii')
    #     logger.info(stderr)
    #     provisioner_err = common.ProvisionerError('Failed to resize MIG')
    #     provisioner_err.errors = [{
    #         'code': 'UNKNOWN',
    #         'domain': 'mig',
    #         'message': stderr
    #     }]
    #     # _log_errors(provisioner_err.errors, e, zone)
    #     raise provisioner_err from e


def view_resize_requests(project_id, zone, group_name) -> None:
    try:
        cmd = ('gcloud beta compute instance-groups managed resize-requests '
               f'list {group_name} '
               f'--zone={zone} '
               f'--project={project_id}')
        logger.info(
            f'Listing resize requests for MIG {group_name} with command:\n{cmd}'
        )
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


def wait_for_managed_group_to_be_stable(project_id, zone, group_name) -> None:
    """Wait until the managed instance group is stable."""
    try:
        cmd = (
            'gcloud compute instance-groups managed wait-until '
            f'{group_name} '
            '--stable '
            f'--zone={zone} '
            f'--project={project_id} '
            # TODO(zhwu): Allow users to specify timeout.
            # 20 minutes timeout
            '--timeout=1200')
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


def wait_for_extended_operation(
        operation: 'extended_operation.ExtendedOperation',
        verbose_name: str = 'operation',
        timeout: int = 300) -> Any:
    """Waits for the extended (long-running) operation to complete.

    Taken from Google's samples
    https://cloud.google.com/compute/docs/samples/compute-operation-extended-wait?hl=en

    If the operation is successful, it will return its result.
    If the operation ends with an error, an exception will be raised.
    If there were any warnings during the execution of the operation
    they will be printed to sys.stderr.

    Args:
        operation: a long-running operation you want to wait on.
        verbose_name: (optional) a more verbose name of the operation,
            used only during error and warning reporting.
        timeout: how long (in seconds) to wait for operation to finish.
            If None, wait indefinitely.

    Returns:
        Whatever the operation.result() returns.

    Raises:
        This method will raise the exception received from
        `operation.exception()` or RuntimeError if there is no exception set,
        but there is an `error_code` set for the `operation`.

        In case of an operation taking longer than `timeout` seconds to
        complete, a `concurrent.futures.TimeoutError` will be raised.
    """
    if operation is None:
        return None

    result = operation.result(timeout=timeout)

    if operation.error_code:
        logger.debug(
            f'Error during {verbose_name}: [Code: {operation.error_code}]: '
            f'{operation.error_message}',
            file=sys.stderr,
            flush=True,
        )
        logger.debug(f'Operation ID: {operation.name}',
                     file=sys.stderr,
                     flush=True)
        # TODO gurc: wrap this in a custom skypilot exception
        raise operation.exception() or RuntimeError(operation.error_message)

    if operation.warnings:
        logger.debug(f'Warnings during {verbose_name}:\n',
                     file=sys.stderr,
                     flush=True)
        for warning in operation.warnings:
            logger.debug(f' - {warning.code}: {warning.message}',
                         file=sys.stderr,
                         flush=True)

    return result
