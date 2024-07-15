"""Utilities for GCP instances."""
import copy
import enum
import functools
from multiprocessing import pool
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sky import sky_logging
from sky.adaptors import gcp
from sky.clouds import gcp as gcp_cloud
from sky.provision import common
from sky.provision import constants as provision_constants
from sky.provision.gcp import constants
from sky.provision.gcp import mig_utils
from sky.utils import common_utils
from sky.utils import ux_utils

# Tag for the name of the node
INSTANCE_NAME_MAX_LEN = 64
INSTANCE_NAME_UUID_LEN = 8

TPU_NODE_CREATION_FAILURE = 'Failed to provision TPU node.'

# This is the maximum number of times we will retry a GCP API call.
# The number is identical to those we use for AWS boto3.
GCP_MAX_RETRIES = 12
GCP_CREATE_MAX_RETRIES = 5
GCP_RETRY_INTERVAL_SECONDS = 5
GCP_TIMEOUT = 300

logger = sky_logging.init_logger(__name__)

_FIREWALL_RESOURCE_NOT_FOUND_PATTERN = re.compile(
    r'The resource \'projects/.*/global/firewalls/.*\' was not found')


def _retry_on_http_exception(
    regex: Optional[str] = None,
    max_retries: int = GCP_MAX_RETRIES,
    retry_interval_s: int = GCP_RETRY_INTERVAL_SECONDS,
):
    """Retry a function call n-times for as long as it throws an exception."""

    def dec(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            exception_type = gcp.http_error_exception()

            def try_catch_exc():
                try:
                    value = func(*args, **kwargs)
                    return value
                except Exception as e:  # pylint: disable=broad-except
                    if not isinstance(e, exception_type) or (
                            regex and not re.search(regex, str(e))):
                        raise
                    return e

            for _ in range(max_retries):
                ret = try_catch_exc()
                if not isinstance(ret, Exception):
                    break
                logger.debug(f'Retrying for exception: {ret}')
                time.sleep(retry_interval_s)
            if isinstance(ret, Exception):
                raise ret
            return ret

        return wrapper

    return dec


def _generate_node_name(cluster_name: str, node_suffix: str,
                        is_head: bool) -> str:
    """Generate node name from labels and suffix.

    This is required so that the correct resource can be selected
    when the only information autoscaler has is the name of the node.

    The suffix is expected to be one of 'compute' or 'tpu'
    (as in ``GCPNodeType``).
    """
    suffix_id = common_utils.base36_encode(uuid.uuid4().hex)
    suffix = f'-{suffix_id[:INSTANCE_NAME_UUID_LEN]}-{node_suffix}'
    if is_head:
        suffix = f'-head{suffix}'
    else:
        suffix = f'-worker{suffix}'
    node_name = cluster_name + suffix
    assert len(node_name) <= INSTANCE_NAME_MAX_LEN, cluster_name
    return node_name


def _format_and_log_message_from_errors(errors: List[Dict[str, str]], e: Any,
                                        zone: Optional[str]) -> str:
    """Format errors into a string and log it to the console."""
    if errors:
        plural = 's' if len(errors) > 1 else ''
        codes = ', '.join(repr(e.get('code', 'N/A')) for e in errors)
        messages = '; '.join(
            repr(e.get('message', 'N/A').strip('.')) for e in errors)
        zone_str = f' in {zone}' if zone else ''
        msg = f'Got return code{plural} {codes}{zone_str}: {messages}'
    else:
        msg = f'create_instances: Failed with reason: {e}'
    logger.warning(msg)
    return msg


def selflink_to_name(selflink: str) -> str:
    """Converts a selflink to a name.

    Args:
        selflink: The selflink to convert.

    Returns:
        The name of the resource.
    """
    return selflink.rsplit('/', 1)[-1]


def instance_to_handler(instance: str):
    instance_type = instance.split('-')[-1]
    if instance_type == 'compute':
        return GCPComputeInstance
    elif instance_type == 'tpu':
        return GCPTPUVMInstance
    elif instance.startswith(constants.MIG_NAME_PREFIX):
        return GCPManagedInstanceGroup
    else:
        raise ValueError(f'Unknown instance type: {instance_type}')


class GCPInstance:
    """Base class for GCP instance handlers."""
    PENDING_STATES: List[str] = []
    NEED_TO_STOP_STATES: List[str] = []
    NON_STOPPED_STATES: List[str] = []
    NEED_TO_TERMINATE_STATES: List[str] = []
    RUNNING_STATE: str = ''
    STOPPING_STATES: List[str] = []
    STOPPED_STATES: List[str] = []
    STATUS_FIELD: str = ''

    @classmethod
    def load_resource(cls):
        """Load the GCP API for the instance type.

        Do not cache the resource object, as it will not work
        when multiple threads are running.
        """
        raise NotImplementedError

    @classmethod
    def stop(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> dict:
        raise NotImplementedError

    @classmethod
    def terminate(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> dict:
        raise NotImplementedError

    @classmethod
    def wait_for_operation(cls,
                           operation: dict,
                           project_id: str,
                           region: Optional[str] = None,
                           zone: Optional[str] = None) -> None:
        raise NotImplementedError

    @classmethod
    def filter(
        cls,
        project_id: str,
        zone: str,
        label_filters: Optional[Dict[str, str]],
        status_filters: Optional[List[str]],
        included_instances: Optional[List[str]] = None,
        excluded_instances: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @classmethod
    def get_vpc_name(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> str:
        raise NotImplementedError

    @classmethod
    def delete_firewall_rule(
        cls,
        project_id: str,
        firewall_rule_name: str,
    ) -> None:
        raise NotImplementedError

    @classmethod
    def create_or_update_firewall_rule(
        cls,
        firewall_rule_name: str,
        project_id: str,
        vpc_name: str,
        cluster_name_on_cloud: str,
        ports: List[str],
    ) -> dict:
        raise NotImplementedError

    @classmethod
    def add_network_tag_if_not_exist(
        cls,
        project_id: str,
        zone: str,
        instance: str,
        tag: str,
    ) -> None:
        raise NotImplementedError

    @classmethod
    def create_instances(
        cls,
        cluster_name: str,
        project_id: str,
        zone: str,
        node_config: dict,
        labels: dict,
        count: int,
        total_count: int,
        include_head_node: bool,
    ) -> Tuple[Optional[List], List[str]]:
        """Creates multiple instances and returns result.

        Returns a tuple of (errors, list[instance_names]).
        """
        raise NotImplementedError

    @classmethod
    def start_instances(cls, cluster_name: str, project_id: str, zone: str,
                        instances: List[str], labels: Dict[str,
                                                           str]) -> List[str]:
        """Start multiple instances.

        Returns:
            List of instance names that are started.
        """
        del cluster_name  # Unused
        for instance_id in instances:
            cls.start_instance(instance_id, project_id, zone)
            cls.set_labels(project_id, zone, instance_id, labels)
        return instances

    @classmethod
    def start_instance(cls, node_id: str, project_id: str, zone: str) -> None:
        """Start a stopped instance."""
        raise NotImplementedError

    @classmethod
    def set_labels(cls, project_id: str, availability_zone: str, node_id: str,
                   labels: dict) -> None:
        raise NotImplementedError

    @classmethod
    def create_node_tag(cls,
                        project_id: str,
                        availability_zone: str,
                        target_instance_id: str,
                        is_head: bool = True) -> str:
        if is_head:
            node_tag = provision_constants.HEAD_NODE_TAGS
        else:
            node_tag = provision_constants.WORKER_NODE_TAGS
        cls.set_labels(project_id=project_id,
                       availability_zone=availability_zone,
                       node_id=target_instance_id,
                       labels=node_tag)

        return target_instance_id

    @classmethod
    def get_instance_info(cls, project_id: str, availability_zone: str,
                          instance_id: str) -> List[common.InstanceInfo]:
        raise NotImplementedError

    @classmethod
    def resize_disk(cls, project_id: str, availability_zone: str,
                    node_config: dict, instance_name: str) -> None:
        """Resize a Google Cloud disk based on the provided configuration.
        Returns the response of resize operation.
        """
        raise NotImplementedError


class GCPComputeInstance(GCPInstance):
    """Instance handler for GCP compute instances."""
    PENDING_STATES = ['PROVISIONING', 'STAGING', 'REPAIRING']
    STOPPING_STATES = ['STOPPING', 'SUSPENDING']
    STOPPED_STATES = ['TERMINATED', 'SUSPENDED']
    RUNNING_STATE = 'RUNNING'
    STATUS_FIELD = 'status'
    NEED_TO_STOP_STATES = PENDING_STATES + [RUNNING_STATE]

    NON_STOPPED_STATES = NEED_TO_STOP_STATES + STOPPING_STATES

    @classmethod
    def load_resource(cls):
        return gcp.build('compute',
                         'v1',
                         credentials=None,
                         cache_discovery=False)

    @classmethod
    def stop(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> dict:
        operation = cls.load_resource().instances().stop(
            project=project_id,
            zone=zone,
            instance=instance,
            # This is needed for the instance that has local SSDs attached by
            # default, such as a2-highgpu-8g. Otherwise, an error will be
            # raised. Refer to issue #2586
            # https://cloud.google.com/compute/docs/disks/local-ssd#stop_instance
            discardLocalSsd=False,
        ).execute()
        return operation

    @classmethod
    def terminate(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> dict:
        operation = cls.load_resource().instances().delete(
            project=project_id,
            zone=zone,
            instance=instance,
        ).execute()
        return operation

    @classmethod
    def filter(
        cls,
        project_id: str,
        zone: str,
        label_filters: Optional[Dict[str, str]],
        status_filters: Optional[List[str]],
        included_instances: Optional[List[str]] = None,
        excluded_instances: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if label_filters:
            label_filter_expr = ('(' + ' AND '.join([
                '(labels.{key} = {value})'.format(key=key, value=value)
                for key, value in label_filters.items()
            ]) + ')')
        else:
            label_filter_expr = ''

        if status_filters:
            instance_state_filter_expr = ('(' + ' OR '.join([
                '(status = {status})'.format(status=status)
                for status in status_filters
            ]) + ')')
        else:
            instance_state_filter_expr = ''

        not_empty_filters = [
            f for f in [
                label_filter_expr,
                instance_state_filter_expr,
            ] if f
        ]

        filter_expr = ' AND '.join(not_empty_filters)

        response = (cls.load_resource().instances().list(
            project=project_id,
            filter=filter_expr,
            zone=zone,
        ).execute(num_retries=GCP_MAX_RETRIES))
        instances = response.get('items', [])
        instances = {i['name']: i for i in instances}
        if included_instances:
            instances = {
                k: v for k, v in instances.items() if k in included_instances
            }
        if excluded_instances:
            instances = {
                k: v
                for k, v in instances.items()
                if k not in excluded_instances
            }
        return instances

    @classmethod
    def wait_for_operation(cls,
                           operation: dict,
                           project_id: str,
                           region: Optional[str] = None,
                           zone: Optional[str] = None,
                           timeout: int = GCP_TIMEOUT) -> None:
        if zone is not None:
            kwargs = {'zone': zone}
            operation_caller = cls.load_resource().zoneOperations()
        elif region is not None:
            kwargs = {'region': region}
            operation_caller = cls.load_resource().regionOperations()
        else:
            kwargs = {}
            operation_caller = cls.load_resource().globalOperations()
        logger.debug(
            f'Waiting GCP operation {operation["name"]} to be ready ...')

        @_retry_on_http_exception(
            f'Failed to wait for operation {operation["name"]}')
        def call_operation(fn, timeout: int):
            request = fn(
                project=project_id,
                operation=operation['name'],
                **kwargs,
            )
            request.http.timeout = timeout
            return request.execute(num_retries=GCP_MAX_RETRIES)

        wait_start = time.time()
        while time.time() - wait_start < timeout:
            # Retry the wait() call until it succeeds or times out.
            # This is because the wait() call is only best effort, and does not
            # guarantee that the operation is done when it returns.
            # Reference: https://cloud.google.com/workflows/docs/reference/googleapis/compute/v1/zoneOperations/wait # pylint: disable=line-too-long
            remaining_timeout = max(timeout - (time.time() - wait_start), 1)
            result = call_operation(operation_caller.wait, remaining_timeout)
            if result['status'] == 'DONE':
                # NOTE: Error example:
                # {
                #   'code': 'VM_MIN_COUNT_NOT_REACHED',
                #   'message': 'Requested minimum count of 4 VMs could not be created.'
                # }
                errors = result.get('error', {}).get('errors')
                if errors is not None:
                    logger.debug(
                        'wait_operations: Failed to create instances. Reason: '
                        f'{errors}')
                    msg = _format_and_log_message_from_errors(
                        errors, result, zone)
                    error = common.ProvisionerError('Operation failed')
                    setattr(error, 'detailed_reason', msg)
                    error.errors = errors
                    raise error
                return
            logger.debug(f'wait_for_operation: Retry waiting for operation '
                         f'{operation["name"]} to finish (result: {result})...')
        else:
            logger.warning('wait_for_operation: Timeout waiting for creation '
                           'operation, cancelling the operation ...')
            remaining_timeout = max(timeout - (time.time() - wait_start), 1)
            try:
                result = call_operation(operation_caller.delete,
                                        remaining_timeout)
            except gcp.http_error_exception() as e:
                logger.debug('wait_for_operation: failed to cancel operation '
                             f'due to error: {e}')
            errors = [{
                'code': 'TIMEOUT',
                'message': f'Timeout waiting for operation {operation["name"]}',
                'domain': 'wait_for_operation'
            }]
            msg = _format_and_log_message_from_errors(errors, None, zone)
            error = common.ProvisionerError('Operation timed out')
            # Used for usage collection only, to include in the usage message.
            setattr(error, 'detailed_reason', msg)
            error.errors = errors
            raise error

    @classmethod
    def get_vpc_name(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> str:
        try:
            response = cls.load_resource().instances().get(
                project=project_id,
                zone=zone,
                instance=instance,
            ).execute()
            # Format: projects/PROJECT_ID/global/networks/VPC_NAME
            vpc_link = response['networkInterfaces'][0]['network']
            return selflink_to_name(vpc_link)
        except gcp.http_error_exception() as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Failed to get VPC name for instance {instance}') from e

    @classmethod
    def add_network_tag_if_not_exist(
        cls,
        project_id: str,
        zone: str,
        instance: str,
        tag: str,
    ) -> None:
        try:
            # If we have multiple instances, they are in the same cluster,
            # i.e. the same VPC. So we can just pick one.
            response = cls.load_resource().instances().get(
                project=project_id,
                zone=zone,
                instance=instance,
            ).execute()
            existing_tags = response['tags'].get('items', [])
            if tag in existing_tags:
                return
            update_body = response['tags']
            update_body['items'] = existing_tags
            update_body['items'].append(tag)
            cls.load_resource().instances().setTags(
                project=project_id,
                zone=zone,
                instance=instance,
                body=update_body,
            ).execute()
        except gcp.http_error_exception() as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Failed to add network tags for instance {instance}'
                ) from e

    @classmethod
    def delete_firewall_rule(
        cls,
        project_id: str,
        firewall_rule_name: str,
    ) -> None:
        rule = cls.load_resource().firewalls().list(
            project=project_id, filter=f'name={firewall_rule_name}').execute()
        # For the return value format, please refer to
        # https://developers.google.com/resources/api-libraries/documentation/compute/alpha/python/latest/compute_alpha.firewalls.html#list # pylint: disable=line-too-long
        if 'items' not in rule:
            logger.warning(f'Firewall rule {firewall_rule_name} not found. '
                           'Skip cleanup.')
            return
        cls.load_resource().firewalls().delete(
            project=project_id,
            firewall=firewall_rule_name,
        ).execute()

    @classmethod
    def create_or_update_firewall_rule(
        cls,
        firewall_rule_name: str,
        project_id: str,
        vpc_name: str,
        cluster_name_on_cloud: str,
        ports: List[str],
    ) -> dict:
        try:
            body = cls.load_resource().firewalls().get(
                project=project_id, firewall=firewall_rule_name).execute()
            body['allowed'][0]['ports'] = ports
            operation = cls.load_resource().firewalls().update(
                project=project_id,
                firewall=firewall_rule_name,
                body=body,
            ).execute()
        except gcp.http_error_exception() as e:
            if _FIREWALL_RESOURCE_NOT_FOUND_PATTERN.search(e.reason) is None:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Failed to update firewall rule {firewall_rule_name}'
                    ) from e
            body = {
                'name': firewall_rule_name,
                'description': (f'Allow user-specified port {ports} for '
                                f'cluster {cluster_name_on_cloud}'),
                'network': f'projects/{project_id}/global/networks/{vpc_name}',
                'selfLink': f'projects/{project_id}/global/firewalls/' +
                            firewall_rule_name,
                'direction': 'INGRESS',
                'priority': 65534,
                'allowed': [{
                    'IPProtocol': 'tcp',
                    'ports': ports,
                }],
                'sourceRanges': ['0.0.0.0/0'],
                'targetTags': [cluster_name_on_cloud],
            }
            operation = cls.load_resource().firewalls().insert(
                project=project_id,
                body=body,
            ).execute()
        return operation

    @classmethod
    def set_labels(cls, project_id: str, availability_zone: str, node_id: str,
                   labels: dict) -> None:
        node = cls.load_resource().instances().get(
            project=project_id,
            instance=node_id,
            zone=availability_zone,
        ).execute(num_retries=GCP_CREATE_MAX_RETRIES)
        body = {
            'labels': dict(node['labels'], **labels),
            'labelFingerprint': node['labelFingerprint'],
        }
        operation = (cls.load_resource().instances().setLabels(
            project=project_id,
            zone=availability_zone,
            instance=node_id,
            body=body,
        ).execute(num_retries=GCP_CREATE_MAX_RETRIES))

        cls.wait_for_operation(operation, project_id, zone=availability_zone)

    @classmethod
    def create_instances(
        cls,
        cluster_name: str,
        project_id: str,
        zone: str,
        node_config: dict,
        labels: dict,
        count: int,
        total_count: int,
        include_head_node: bool,
    ) -> Tuple[Optional[List], List[str]]:
        # NOTE: The syntax for bulkInsert() is different from insert().
        # bulkInsert expects resource names without prefix. Otherwise
        # it causes a 503 error.
        config = copy.deepcopy(node_config)

        # removing TPU-specific default key set in config.py
        config.pop('networkConfig', None)

        head_tag_needed = [False] * count
        if include_head_node:
            head_tag_needed[0] = True

        names = []
        for i in range(count):
            names.append(
                _generate_node_name(cluster_name,
                                    GCPNodeType.COMPUTE.value,
                                    is_head=head_tag_needed[i]))

        labels = dict(config.get('labels', {}), **labels)

        config.update({
            'labels': dict(
                labels, **{
                    provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name,
                    provision_constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name
                }),
        })

        all_names = []
        if 'reservationAffinity' in config:
            specific_reservations = set(config['reservationAffinity']['values'])
            reservations = gcp_cloud.GCP().get_reservations_available_resources(
                config['machineType'],
                region=zone.rpartition('-')[0],
                zone=zone,
                specific_reservations=specific_reservations)
            # Filter the reservations by the user-specified ones, because
            # reservations contain auto reservations as well, which do not
            # need to explicitly specify in the config for creating instances.
            specific_reservations_to_count = {
                reservation: count
                for reservation, count in reservations.items()
                if reservation in specific_reservations
            }
            # Sort the reservations by the number of available resources
            specified_reservations_list = sorted(
                specific_reservations_to_count.items(),
                key=lambda x: x[1],
                reverse=True)
            # TODO(zhwu): Convert this to parallel execution.
            # TODO(zhwu): This is not atomic as the reservation count may change
            # between the time we check and the time we create the instances, as
            # other users may be creating instances at the same time.
            # Our current implementation will skip the current region if the
            # reservation count is not enough, which is suboptimal.
            for reservation, reservation_count in specified_reservations_list:
                if reservation_count <= 0:
                    continue
                reservation_count = min(reservation_count, count)
                logger.debug(f'Creating {reservation_count} instances '
                             f'with reservation {reservation}')
                config['reservationAffinity']['values'] = [reservation]
                created_names = names[:reservation_count]
                errors = cls._create_instances(
                    created_names, project_id, zone, config,
                    head_tag_needed[:reservation_count])
                all_names.extend(created_names)
                if errors:
                    return errors, all_names
                count -= reservation_count
                if count <= 0:
                    return None, all_names
                names = names[reservation_count:]
                head_tag_needed = head_tag_needed[reservation_count:]
            config.pop('reservationAffinity', None)

        errors = cls._create_instances(names, project_id, zone, config,
                                       head_tag_needed)

        all_names.extend(names)
        return errors, all_names

    @classmethod
    def _insert(cls, names: List[str], project_id: str, zone: str,
                config: dict) -> List[dict]:
        # Convert name to selflink
        existing_machine_type = config['machineType']
        if not re.search('.*/machineTypes/.*', existing_machine_type):
            config['machineType'] = (
                f'zones/{zone}/machineTypes/{config["machineType"]}')

        for accelerator in config.get('guestAccelerators', []):
            gpu_type = accelerator['acceleratorType']
            if not re.search('.*/acceleratorTypes/.*', gpu_type):
                accelerator['acceleratorType'] = (
                    f'projects/{project_id}/zones/{zone}/'
                    f'acceleratorTypes/{gpu_type}')

        logger.debug('Launching GCP instances with "insert" ...')
        operations = []
        for name in names:
            body = {
                'name': name,
                **config,
            }
            request = cls.load_resource().instances().insert(
                project=project_id,
                zone=zone,
                body=body,
            )
            # We need to retry the insert operation because it may fail with
            # RESOURCE_OPERATION_RATE_EXCEEDED error, which is normally caused
            # by creating VMs with machine images on different zones.
            operation = request.execute(num_retries=GCP_MAX_RETRIES)
            operations.append(operation)

        logger.debug('"insert" operation requested ...')
        return operations

    @classmethod
    def _convert_selflinks_in_config(cls, config: dict) -> None:
        """Convert selflinks to names in the config."""
        for disk in config.get('disks', []):
            disk_type = disk.get('initializeParams', {}).get('diskType')
            if disk_type is not None:
                disk['initializeParams']['diskType'] = selflink_to_name(
                    disk_type)
        config['machineType'] = selflink_to_name(config['machineType'])
        for accelerator in config.get('guestAccelerators', []):
            accelerator['acceleratorType'] = selflink_to_name(
                accelerator['acceleratorType'])

    @classmethod
    def _bulk_insert(cls, names: List[str], project_id: str, zone: str,
                     config: dict) -> List[dict]:
        source_instance_template = config.pop('sourceInstanceTemplate', None)
        if 'scheduling' in config and isinstance(config['scheduling'], list):
            # For backeward compatibility: converting the list of dictionaries
            # to a dictionary due to the use of deprecated API.
            # [{'preemptible': True}, {'onHostMaintenance': 'TERMINATE'}]
            # to {'preemptible': True, 'onHostMaintenance': 'TERMINATE'}
            config['scheduling'] = {
                k: v for d in config['scheduling'] for k, v in d.items()
            }

        cls._convert_selflinks_in_config(config)

        body = {
            'count': len(names),
            'instanceProperties': config,
            'sourceInstanceTemplate': source_instance_template,
            'perInstanceProperties': {n: {} for n in names}
        }
        logger.debug('Launching GCP instances with "bulkInsert" ...')
        request = cls.load_resource().instances().bulkInsert(
            project=project_id,
            zone=zone,
            body=body,
        )
        operation = request.execute(num_retries=0)
        return [operation]

    @classmethod
    def _use_bulk_insert(cls, config) -> bool:
        """Decide whether to use bulkInsert or not based on config."""
        # bulkInsert does not support overriding parameter sourceMachineImage
        # with disks (without a sourceImage), causing the following error:
        #   'Invalid value for field \'resource.instanceProperties.disks[0]\':
        #   \'{  "type": "PERSISTENT",  "boot": true,  "initializeParams": {
        #    "diskSizeGb": "256",    "diskType"...\'. Boot disk must have a
        #    source specified'
        # https://cloud.google.com/compute/docs/reference/rest/v1/instances/bulkInsert # pylint: disable=line-too-long
        if config.get('sourceMachineImage') is not None:
            return False
        return True

    @classmethod
    def _create_instances(
        cls,
        names: List[str],
        project_id: str,
        zone: str,
        config: dict,
        head_tag_needed: List[bool],
    ) -> Optional[List]:

        def _handle_http_error(e):
            # NOTE: Error example:
            # {
            #   'message': "Quota '...' exceeded. Limit: ... in region xx-xxxx.", # pylint: disable=line-too-long
            #   'domain': 'usageLimits',
            #   'reason': 'quotaExceeded'
            # }
            error_details = getattr(e, 'error_details', [])
            errors = []
            for detail in error_details:
                # To be consistent with error messages returned by operation wait.
                errors.append({
                    'code': detail.get('reason'),
                    'domain': detail.get('domain'),
                    'message': detail.get('message', str(e)),
                })
            logger.debug(
                f'create_instances: googleapiclient.errors.HttpError: {e}')
            _format_and_log_message_from_errors(errors, e, zone)
            return errors

        # Allow Google Compute Engine instance templates.
        #
        # Config example:
        #
        #     ...
        #     node_config:
        #         sourceInstanceTemplate: global/instanceTemplates/worker-16
        #         machineType: e2-standard-16
        #     ...
        #
        # node_config parameters override matching template parameters, if any.
        #
        # https://cloud.google.com/compute/docs/instance-templates
        # https://cloud.google.com/compute/docs/reference/rest/v1/instances/insert
        try:
            if cls._use_bulk_insert(config):
                operations = cls._bulk_insert(names, project_id, zone, config)
            else:
                operations = cls._insert(names, project_id, zone, config)
        except gcp.http_error_exception() as e:
            return _handle_http_error(e)

        for operation in operations:
            errors = operation.get('error', {}).get('errors', [])
            if errors:
                logger.debug('create_instances: Failed to create instances. '
                             f'Reason: {errors}')
                _format_and_log_message_from_errors(errors, operations, zone)
                return errors

        logger.debug('Waiting GCP instances to be ready ...')
        try:
            for operation in operations:
                cls.wait_for_operation(operation, project_id, zone=zone)
        except common.ProvisionerError as e:
            return e.errors
        except gcp.http_error_exception() as e:
            return _handle_http_error(e)

        # assign labels for head node
        with pool.ThreadPool() as p:
            p.starmap(cls.create_node_tag,
                      [(project_id, zone, names[i], head_tag_needed[i])
                       for i in range(len(names))])
        return None

    @classmethod
    def start_instance(cls, node_id: str, project_id: str, zone: str) -> None:
        operation = (cls.load_resource().instances().start(
            project=project_id,
            zone=zone,
            instance=node_id,
        ).execute())

        cls.wait_for_operation(operation, project_id, zone=zone)

    @classmethod
    def get_instance_info(cls, project_id: str, availability_zone: str,
                          instance_id: str) -> List[common.InstanceInfo]:
        result = cls.load_resource().instances().get(
            project=project_id,
            zone=availability_zone,
            instance=instance_id,
        ).execute()
        external_ip = (result.get('networkInterfaces',
                                  [{}])[0].get('accessConfigs',
                                               [{}])[0].get('natIP', None))
        internal_ip = result.get('networkInterfaces', [{}])[0].get('networkIP')

        return [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=internal_ip,
                external_ip=external_ip,
                tags=result.get('labels', {}),
            )
        ]

    @classmethod
    def resize_disk(cls, project_id: str, availability_zone: str,
                    node_config: dict, instance_name: str) -> None:
        """Resize a Google Cloud disk based on the provided configuration."""

        # Extract the specified disk size from the configuration
        new_size_gb = node_config['disks'][0]['initializeParams']['diskSizeGb']

        # Fetch the instance details to get the disk name and current disk size
        response = (cls.load_resource().instances().get(
            project=project_id,
            zone=availability_zone,
            instance=instance_name,
        ).execute())
        disk_name = selflink_to_name(response['disks'][0]['source'])

        try:
            # Execute the resize request and return the response
            operation = (cls.load_resource().disks().resize(
                project=project_id,
                zone=availability_zone,
                disk=disk_name,
                body={
                    'sizeGb': str(new_size_gb),
                },
            ).execute())
        except gcp.http_error_exception() as e:
            # Catch HttpError when provided with invalid value for new disk
            # size. Allowing users to create instances with the same size as the
            # image.
            # TODO(zhwu): We should only match the error message that are using
            # the disk with same size as the image.
            logger.warning(f'googleapiclient.errors.HttpError: {e.reason}')
            return

        cls.wait_for_operation(operation, project_id, zone=availability_zone)


class GCPManagedInstanceGroup(GCPComputeInstance):
    """Handler for GCP Managed Instance Group."""

    @classmethod
    def create_instances(
        cls,
        cluster_name: str,
        project_id: str,
        zone: str,
        node_config: dict,
        labels: dict,
        count: int,
        total_count: int,
        include_head_node: bool,
    ) -> Tuple[Optional[List], List[str]]:
        logger.debug(f'Creating cluster with MIG: {cluster_name!r}')
        config = copy.deepcopy(node_config)
        labels = dict(config.get('labels', {}), **labels)

        config.update({
            'labels': dict(
                labels,
                **{
                    provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name,
                    # Assume all nodes are workers, we can update the head node
                    # once the instances are created.
                    **provision_constants.WORKER_NODE_TAGS,
                    provision_constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name,
                }),
        })
        cls._convert_selflinks_in_config(config)

        # Convert label values to string and lowercase per MIG API requirement.
        region = zone.rpartition('-')[0]
        instance_template_name = mig_utils.get_instance_template_name(
            cluster_name)
        managed_instance_group_name = mig_utils.get_managed_instance_group_name(
            cluster_name)

        instance_template_exists = mig_utils.check_instance_template_exits(
            project_id, region, instance_template_name)
        mig_exists = mig_utils.check_managed_instance_group_exists(
            project_id, zone, managed_instance_group_name)

        label_filters = {
            provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name,
        }
        potential_head_instances = []
        if mig_exists:
            instances = cls.filter(
                project_id,
                zone,
                label_filters={
                    provision_constants.TAG_RAY_NODE_KIND: 'head',
                    **label_filters,
                },
                status_filters=cls.NEED_TO_TERMINATE_STATES)
            potential_head_instances = list(instances.keys())

        config['labels'] = {
            k: str(v).lower() for k, v in config['labels'].items()
        }
        if instance_template_exists:
            if mig_exists:
                logger.debug(
                    f'Instance template {instance_template_name} already '
                    'exists. Skip creating it.')
            else:
                logger.debug(
                    f'Instance template {instance_template_name!r} '
                    'exists and no instance group is using it. This is a '
                    'leftover of a previous autodown. Delete it and recreate '
                    'it.')
                # TODO(zhwu): this is a bit hacky as we cannot delete instance
                # template during an autodown, we can only defer the deletion
                # to the next launch of a cluster with the same name. We should
                # find a better way to handle this.
                cls._delete_instance_template(project_id, zone,
                                              instance_template_name)
                instance_template_exists = False

        if not instance_template_exists:
            operation = mig_utils.create_region_instance_template(
                cluster_name, project_id, region, instance_template_name,
                config)
            cls.wait_for_operation(operation, project_id, region=region)
        # create managed instance group
        instance_template_url = (f'projects/{project_id}/regions/{region}/'
                                 f'instanceTemplates/{instance_template_name}')
        if not mig_exists:
            # Create a new MIG with size 0 and resize it later for triggering
            # DWS, according to the doc: https://cloud.google.com/compute/docs/instance-groups/create-mig-with-gpu-vms # pylint: disable=line-too-long
            operation = mig_utils.create_managed_instance_group(
                project_id,
                zone,
                managed_instance_group_name,
                instance_template_url,
                size=0)
            cls.wait_for_operation(operation, project_id, zone=zone)

        managed_instance_group_config = config[
            constants.MANAGED_INSTANCE_GROUP_CONFIG]
        if count > 0:
            # Use resize to trigger DWS for creating VMs.
            operation = mig_utils.resize_managed_instance_group(
                project_id,
                zone,
                managed_instance_group_name,
                count,
                run_duration=managed_instance_group_config['run_duration'])
            cls.wait_for_operation(operation, project_id, zone=zone)

        # This will block the provisioning until the nodes are ready, which
        # makes the failover not effective. We rely on the request timeout set
        # by user to trigger failover.
        mig_utils.wait_for_managed_group_to_be_stable(
            project_id,
            zone,
            managed_instance_group_name,
            timeout=managed_instance_group_config.get(
                'provision_timeout',
                constants.DEFAULT_MANAGED_INSTANCE_GROUP_PROVISION_TIMEOUT))

        pending_running_instance_names = cls._add_labels_and_find_head(
            cluster_name, project_id, zone, labels, potential_head_instances)
        assert len(pending_running_instance_names) == total_count, (
            pending_running_instance_names, total_count)
        cls.create_node_tag(
            project_id,
            zone,
            pending_running_instance_names[0],
            is_head=True,
        )
        return None, pending_running_instance_names

    @classmethod
    def _delete_instance_template(cls, project_id: str, zone: str,
                                  instance_template_name: str) -> None:
        logger.debug(f'Deleting instance template {instance_template_name}...')
        region = zone.rpartition('-')[0]
        try:
            operation = cls.load_resource().regionInstanceTemplates().delete(
                project=project_id,
                region=region,
                instanceTemplate=instance_template_name).execute()
            cls.wait_for_operation(operation, project_id, region=region)
        except gcp.http_error_exception() as e:
            if re.search(mig_utils.IT_RESOURCE_NOT_FOUND_PATTERN,
                         str(e)) is None:
                raise
            logger.warning(
                f'Instance template {instance_template_name!r} does not exist. '
                'Skip deletion.')

    @classmethod
    def delete_mig(cls, project_id: str, zone: str, cluster_name: str) -> None:
        mig_name = mig_utils.get_managed_instance_group_name(cluster_name)
        # Get all resize request of the MIG and cancel them.
        mig_utils.cancel_all_resize_request_for_mig(project_id, zone, mig_name)
        logger.debug(f'Deleting MIG {mig_name!r} ...')
        try:
            operation = cls.load_resource().instanceGroupManagers().delete(
                project=project_id, zone=zone,
                instanceGroupManager=mig_name).execute()
            cls.wait_for_operation(operation, project_id, zone=zone)
        except gcp.http_error_exception() as e:
            if re.search(mig_utils.MIG_RESOURCE_NOT_FOUND_PATTERN,
                         str(e)) is None:
                raise
            logger.warning(f'MIG {mig_name!r} does not exist. Skip '
                           'deletion.')

        # In the autostop case, the following deletion of instance template
        # will not be executed as the instance that runs the deletion will be
        # terminated with the managed instance group. It is ok to leave the
        # instance template there as when a user creates a new cluster with the
        # same name, the instance template will be updated in our
        # create_instances method.
        cls._delete_instance_template(
            project_id, zone,
            mig_utils.get_instance_template_name(cluster_name))

    @classmethod
    def _add_labels_and_find_head(
            cls, cluster_name: str, project_id: str, zone: str,
            labels: Dict[str, str],
            potential_head_instances: List[str]) -> List[str]:
        pending_running_instances = cls.filter(
            project_id,
            zone,
            {provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name},
            # Find all provisioning and running instances.
            status_filters=cls.NEED_TO_STOP_STATES)
        for running_instance_name in pending_running_instances.keys():
            if running_instance_name in potential_head_instances:
                head_instance_name = running_instance_name
                break
        else:
            head_instance_name = list(pending_running_instances.keys())[0]
        # We need to update the node's label if mig already exists, as the
        # config is not updated during the resize operation.
        for instance_name in pending_running_instances.keys():
            cls.set_labels(project_id=project_id,
                           availability_zone=zone,
                           node_id=instance_name,
                           labels=labels)

        pending_running_instance_names = list(pending_running_instances.keys())
        pending_running_instance_names.remove(head_instance_name)
        # Label for head node type will be set by caller
        return [head_instance_name] + pending_running_instance_names


class GCPTPUVMInstance(GCPInstance):
    """Instance handler for GCP TPU VM."""
    PENDING_STATES = ['CREATING', 'STARTING', 'RESTARTING', 'REPAIRING']
    RUNNING_STATE = 'READY'
    STOPPING_STATES = ['STOPPING']
    STOPPED_STATES = ['STOPPED']
    STATUS_FIELD = 'state'
    NEED_TO_STOP_STATES = PENDING_STATES + [RUNNING_STATE]

    NON_STOPPED_STATES = NEED_TO_STOP_STATES + STOPPING_STATES

    @classmethod
    def load_resource(cls):
        return gcp.build(
            'tpu',
            constants.TPU_VM_VERSION,
            credentials=None,
            cache_discovery=False,
            discoveryServiceUrl='https://tpu.googleapis.com/$discovery/rest')

    @classmethod
    def wait_for_operation(cls,
                           operation: dict,
                           project_id: str,
                           region: Optional[str] = None,
                           zone: Optional[str] = None) -> None:
        """Poll for TPU operation until finished."""
        del project_id, region, zone  # unused

        @_retry_on_http_exception(
            f'Failed to wait for operation {operation["name"]}')
        def call_operation(fn, timeout: int):
            request = fn(name=operation['name'])
            request.http.timeout = timeout
            return request.execute(num_retries=GCP_MAX_RETRIES)

        wait_start = time.time()
        while time.time() - wait_start < GCP_TIMEOUT:
            timeout = max(GCP_TIMEOUT - (time.time() - wait_start), 1)
            result = call_operation(
                cls.load_resource().projects().locations().operations().get,
                timeout)
            if result['done']:
                break
            logger.debug('wait_for_tpu_operation: '
                         f'Waiting for operation {operation["name"]} to '
                         'finish ...')

        if 'error' in result:
            error = common.ProvisionerError('Operation failed')
            errors = []
            errors.append({
                'code': result['error']['code'],
                'message': result['error']['message'],
                'domain': 'wait_for_operation',
            })
            for detail in result['error'].get('details', []):
                errors.append({
                    'code': detail.pop('@type', ''),
                    'domain': 'wait_for_operation',
                    'message': str(detail),
                })
            error.errors = errors
            raise error

        if 'response' in result:
            logger.debug('wait_for_tpu_operation: '
                         f'Operation {operation["name"]} finished.')

    @classmethod
    def filter(
        cls,
        project_id: str,
        zone: str,
        label_filters: Optional[Dict[str, str]],
        status_filters: Optional[List[str]],
        included_instances: Optional[List[str]] = None,
        excluded_instances: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        path = f'projects/{project_id}/locations/{zone}'
        try:
            response = (cls.load_resource().projects().locations().nodes().list(
                parent=path).execute(num_retries=GCP_MAX_RETRIES))
        except gcp.http_error_exception() as e:
            # SKY: Catch HttpError when accessing unauthorized region.
            # Return empty dict instead of raising exception to not break.
            if 'is not found or access is unauthorized.' in str(e):
                return {}
            if 'Permission \'tpu.nodes.list\' denied on' in str(e):
                return {}
            logger.debug(f'filter: googleapiclient.errors.HttpError: {e}')
            raise

        instances = response.get('nodes', [])

        # filter_expr cannot be passed directly to API
        # so we need to filter the results ourselves

        # same logic as in GCPCompute.list_instances
        label_filters = label_filters or {}

        def filter_instance(instance) -> bool:
            labels = instance.get('labels', {})
            if label_filters:
                for key, value in label_filters.items():
                    if key not in labels:
                        return False
                    if value != labels[key]:
                        return False

            if status_filters:
                if instance.get('state') not in status_filters:
                    return False

            return True

        instances = list(filter(filter_instance, instances))
        instances = {i['name']: i for i in instances}

        if included_instances:
            instances = {
                k: v for k, v in instances.items() if k in included_instances
            }
        if excluded_instances:
            instances = {
                k: v
                for k, v in instances.items()
                if k not in excluded_instances
            }
        return instances

    @classmethod
    def stop(cls, project_id: str, zone: str, instance: str) -> dict:
        """Stop a TPU VM."""
        del project_id, zone  # unused
        operation = cls.load_resource().projects().locations().nodes().stop(
            name=instance).execute()
        return operation

    @classmethod
    def terminate(cls, project_id: str, zone: str, instance: str) -> dict:
        """Terminate a TPU VM."""
        del project_id, zone  # unused
        operation = cls.load_resource().projects().locations().nodes().delete(
            name=instance).execute()
        return operation

    @classmethod
    def add_network_tag_if_not_exist(
        cls,
        project_id: str,
        zone: str,
        instance: str,
        tag: str,
    ) -> None:
        # https://cloud.google.com/tpu/docs/reference/rest/v2alpha1/projects.locations.nodes  # pylint: disable=line-too-long
        # https://cloud.google.com/tpu/docs/reference/rest/v2alpha1/projects.locations.nodes/patch  # pylint: disable=line-too-long
        del project_id, zone  # unused
        try:
            response = cls.load_resource().projects().locations().nodes().get(
                name=instance).execute()
            existing_tags = response.get('tags', [])
            if tag in existing_tags:
                return
            existing_tags.append(tag)
            update_body = response
            update_body['tags'] = existing_tags
            cls.load_resource().projects().locations().nodes().patch(
                name=instance,
                body=update_body,
                updateMask='tags',
            ).execute()
        except gcp.http_error_exception() as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Failed to add network tags for instance {instance}'
                ) from e

    @classmethod
    def get_vpc_name(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> str:
        del project_id, zone  # unused
        try:
            response = cls.load_resource().projects().locations().nodes().get(
                name=instance).execute()
            vpc_link = response['networkConfig']['network']
            return selflink_to_name(vpc_link)
        except gcp.http_error_exception() as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Failed to get VPC name for instance {instance}') from e

    @classmethod
    @_retry_on_http_exception('unable to queue the operation')
    def set_labels(cls, project_id: str, availability_zone: str, node_id: str,
                   labels: dict) -> None:
        while True:
            # wait until the instance become ready before setting labels
            # as Cloud TPU API does not allow setting labels on pending
            # instances
            instances = cls.filter(
                project_id=project_id,
                zone=availability_zone,
                label_filters=None,
                status_filters=cls.PENDING_STATES,
                included_instances=[node_id],
            )
            if not instances:
                break
            logger.debug(f'set_labels: Waiting for instance {node_id} to be '
                         'ready...')
            time.sleep(constants.POLL_INTERVAL)

        node = (cls.load_resource().projects().locations().nodes().get(
            name=node_id).execute(num_retries=GCP_CREATE_MAX_RETRIES))
        body = {
            'labels': dict(node['labels'], **labels),
        }
        update_mask = 'labels'

        operation = (cls.load_resource().projects().locations().nodes().patch(
            name=node_id,
            updateMask=update_mask,
            body=body,
        ).execute(num_retries=GCP_CREATE_MAX_RETRIES))

        cls.wait_for_operation(operation, project_id, availability_zone)

    @classmethod
    def create_instances(
        cls,
        cluster_name: str,
        project_id: str,
        zone: str,
        node_config: dict,
        labels: dict,
        count: int,
        total_count: int,
        include_head_node: bool,
    ) -> Tuple[Optional[List], List[str]]:
        config = copy.deepcopy(node_config)
        # removing Compute-specific default key set in config.py
        config.pop('networkInterfaces', None)

        head_tag_needed = [False] * count
        if include_head_node:
            head_tag_needed[0] = True

        names = []
        for i in range(count):
            names.append(
                _generate_node_name(cluster_name,
                                    GCPNodeType.TPU.value,
                                    is_head=head_tag_needed[i]))

        labels = dict(config.get('labels', {}), **labels)

        config.update({
            'labels': dict(
                labels, **{
                    provision_constants.TAG_RAY_CLUSTER_NAME: cluster_name,
                    provision_constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name
                }),
        })

        if 'reservationAffinity' in config:
            raise NotImplementedError(
                'TPU VMs do not support reservations yet.')

        # Allow Google Compute Engine instance templates.
        #
        # Config example:
        #
        #     ...
        #     node_config:
        #         sourceInstanceTemplate: global/instanceTemplates/worker-16
        #         machineType: e2-standard-16
        #     ...
        #
        # node_config parameters override matching template parameters, if any.
        #
        # https://cloud.google.com/compute/docs/instance-templates
        # https://cloud.google.com/compute/docs/reference/rest/v1/instances/insert
        operations = []
        for i, name in enumerate(names):
            node_config = config.copy()
            if i == 0:
                node_config['labels'].update(provision_constants.HEAD_NODE_TAGS)
            else:
                node_config['labels'].update(
                    provision_constants.WORKER_NODE_TAGS)
            try:
                logger.debug('Launching GCP TPU VM ...')
                request = (
                    cls.load_resource().projects().locations().nodes().create(
                        parent=f'projects/{project_id}/locations/{zone}',
                        body=node_config,
                        nodeId=name,
                    ))
                operation = request.execute(num_retries=0)
                operations.append(operation)
            except gcp.http_error_exception() as e:
                # NOTE: Error example:
                # {
                #   'message': "Quota '...' exceeded. Limit: ... in region xx-xxxx.", # pylint: disable=line-too-long
                #   'domain': 'usageLimits',
                #   'reason': 'quotaExceeded'
                # }
                error_details = getattr(e, 'error_details', [])
                logger.debug(
                    f'create_instances: googleapiclient.errors.HttpError: {e}')
                errors = []
                if isinstance(error_details, str):
                    errors.append({
                        'code': 'CREATION_FAILED',
                        'domain': 'create_instances',
                        'message': error_details,
                    })
                    _format_and_log_message_from_errors(errors, e, zone)
                    return errors, names
                for detail in error_details:
                    # To be consistent with error messages returned by operation
                    # wait.
                    viloations = detail.get('violations', [])
                    if not viloations:
                        errors.append({
                            'code': detail.get('reason'),
                            'domain': detail.get('domain'),
                            'message': detail.get('message', str(e)),
                        })
                    else:
                        for violation in viloations:
                            errors.append({
                                'code': detail.get('@type'),
                                'domain': violation.get('subject'),
                                'message': violation.get('description'),
                            })
                _format_and_log_message_from_errors(errors, e, zone)
                return errors, names
        errors = []
        for operation in operations:
            error = operation.get('error')
            if error:
                error['domain'] = 'create_instances'
                errors.append(error)
            details = operation.get('error', {}).get('details', [])
            for detail in details:
                detail['code'] = detail.pop('@type', '')
                detail['message'] = str(detail)
                detail['domain'] = 'create_instances'
            if details:
                errors.extend(details)
        if errors:
            logger.debug('create_instances: Failed to create instances. '
                         f'Reason: {errors}')
            _format_and_log_message_from_errors(errors, operations, zone)
            return errors, names

        logger.debug('Waiting GCP instances to be ready ...')
        wait_start = time.time()
        success = [False] * len(operations)
        results: List[dict] = [{} for _ in range(len(operations))]
        while time.time() - wait_start < GCP_TIMEOUT:
            # Retry the wait() call until it succeeds or times out.
            # This is because the wait() call is only best effort, and does not
            # guarantee that the operation is done when it returns.
            # Reference: https://cloud.google.com/workflows/docs/reference/googleapis/compute/v1/zoneOperations/wait # pylint: disable=line-too-long
            for i, operation in enumerate(operations):
                if success[i]:
                    continue
                request = (
                    cls.load_resource().projects().locations().operations().get(
                        name=operation['name'],))
                request.http.timeout = GCP_TIMEOUT - (time.time() - wait_start)
                result = request.execute(num_retries=GCP_CREATE_MAX_RETRIES)
                results[i] = result
                success[i] = result['done']
            if all(success):
                logger.debug(f'create_instances: Finished {results}')
                break
            logger.debug('create_instances: Retry waiting for TPU operations '
                         f'to finish: {results}...')
        else:
            logger.warning('create_instances: Timeout waiting for TPU creation '
                           'operation, cancelling the operation ...')
            for i, operation in enumerate(operations):
                if success[i]:
                    continue
                request = cls.load_resource().projects().locations().operations(
                ).cancel(name=operation['name'],)
            request.http.timeout = GCP_TIMEOUT - (time.time() - wait_start)
            request.execute(num_retries=GCP_CREATE_MAX_RETRIES)
            errors = [{
                'code': 'TIMEOUT',
                'message': 'Timeout waiting for creation operation',
                'domain': 'create_instances'
            }]
            _format_and_log_message_from_errors(errors, None, zone)
            return errors, names

        # NOTE: Error example:
        # {
        #    'code': 8,
        #    'message': 'There is no more capacity in the zone ...
        # }
        errors = []
        for result in results:
            error = result.get('error', {})
            if error:
                errors.append(error)
        if errors:
            logger.debug(
                'create_instances: Failed to create instances. Reason: '
                f'{errors}')
            _format_and_log_message_from_errors(errors, results, zone)
            return errors, names
        assert all(success), (
            'Failed to create instances, but there is no error. '
            f'Instance status: {results}')
        return None, names

    @classmethod
    def start_instance(cls, node_id: str, project_id: str, zone: str) -> None:
        operation = (cls.load_resource().projects().locations().nodes().start(
            name=node_id).execute())

        cls.wait_for_operation(operation, project_id, zone)

    @classmethod
    def resize_disk(cls, project_id: str, availability_zone: str,
                    node_config: dict, instance_name: str) -> None:
        """Resize the disk a machine image with a different size is used.

        TODO: Implement the feature to attach persistent disks for TPU VMs.
        The boot disk of TPU VMs is not resizable, and users need to add a
        persistent disk to expand disk capacity. Related issue: #2387
        """
        return

    @classmethod
    def get_instance_info(cls, project_id: str, availability_zone: str,
                          instance_id: str) -> List[common.InstanceInfo]:
        del project_id, availability_zone  # unused
        result = cls.load_resource().projects().locations().nodes().get(
            name=instance_id).execute()
        network_endpoints = result.get('networkEndpoints', [{}])
        external_ips = []
        internal_ips = []
        for endpoint in network_endpoints:
            external_ips.append(
                endpoint.get('accessConfig', {}).get('externalIp', None))
            internal_ips.append(endpoint.get('ipAddress', None))

        return [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=internal_ip,
                external_ip=external_ip,
                tags=result.get('labels', {}),
            ) for internal_ip, external_ip in zip(internal_ips, external_ips)
        ]


class GCPNodeType(enum.Enum):
    """Enum for GCP node types (compute & tpu)"""

    COMPUTE = 'compute'
    MIG = 'mig'
    TPU = 'tpu'


def get_node_type(config: Dict[str, Any]) -> GCPNodeType:
    """Returns node type based on the keys in ``node``.

    This is a very simple check. If we have a ``machineType`` key,
    this is a Compute instance. If we don't have a ``machineType`` key,
    but we have ``acceleratorType``, this is a TPU. Otherwise, it's
    invalid and an exception is raised.

    This works for both node configs and API returned nodes.
    """
    if ('machineType' not in config and 'acceleratorType' not in config):
        raise ValueError(
            'Invalid node. For a Compute instance, "machineType" is '
            'required. '
            'For a TPU instance, "acceleratorType" and no "machineType" '
            'is required. '
            f'Got {list(config)}')

    if 'machineType' not in config and 'acceleratorType' in config:
        return GCPNodeType.TPU

    if (config.get(constants.MANAGED_INSTANCE_GROUP_CONFIG, None) is not None
            and config.get('guestAccelerators', None) is not None):
        # DWS in MIG only works for machine with GPUs.
        return GCPNodeType.MIG

    return GCPNodeType.COMPUTE


def create_tpu_node(project_id: str, zone: str, tpu_node_config: Dict[str, str],
                    vpc_name: str):
    """Create a TPU node with gcloud CLI."""
    # TODO(suquark, zhwu): move this to GcpTpuNodeInstance.
    tpu_name = tpu_node_config['name']
    tpu_type = tpu_node_config['acceleratorType']
    try:
        cmd = (f'gcloud compute tpus create {tpu_name} '
               f'--project={project_id} '
               f'--zone={zone} '
               f'--version={tpu_node_config["runtimeVersion"]} '
               f'--accelerator-type={tpu_type} '
               f'--network={vpc_name}')
        logger.debug(f'Creating TPU {tpu_name} with command:\n{cmd}')
        proc = subprocess.run(
            f'yes | {cmd}',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            check=True,
        )
        stdout = proc.stdout.decode('ascii')
        logger.debug(stdout)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode('ascii')
        logger.debug(stderr)
        if 'ALREADY_EXISTS' in stderr:
            # FIXME: should use 'start' on stopped TPUs, replacing
            # 'create'. Or it can be in a "deleting" state. Investigate the
            # right thing to do (force kill + re-provision?).
            logger.warning(f'TPU {tpu_name} already exists; skipped creation.')
            return
        provisioner_err = common.ProvisionerError(TPU_NODE_CREATION_FAILURE)
        if 'RESOURCE_EXHAUSTED' in stderr:
            provisioner_err.errors = [{
                'code': 'RESOURCE_EXHAUSTED',
                'domain': 'tpu',
                'message': f'TPU {tpu_name} creation failed due to quota '
                           'exhaustion. Please visit '
                           'https://console.cloud.google.com/iam-admin/quotas '
                           'for more information.'
            }]
            _format_and_log_message_from_errors(provisioner_err.errors, e, zone)
            raise provisioner_err from e

        if 'PERMISSION_DENIED' in stderr:
            provisioner_err.errors = [{
                'code': 'PERMISSION_DENIED',
                'domain': 'tpu',
                'message': 'TPUs are not available in this zone.'
            }]
            _format_and_log_message_from_errors(provisioner_err.errors, e, zone)
            raise provisioner_err from e

        if 'no more capacity in the zone' in stderr:
            provisioner_err.errors = [{
                'code': 'CapacityExceeded',
                'domain': 'tpu',
                'message': 'No more capacity in this zone.'
            }]
            _format_and_log_message_from_errors(provisioner_err.errors, e, zone)
            raise provisioner_err from e

        if 'CloudTpu received an invalid AcceleratorType' in stderr:
            # INVALID_ARGUMENT: CloudTpu received an invalid
            # AcceleratorType, "v3-8" for zone "us-central1-c". Valid
            # values are "v2-8, ".
            provisioner_err.errors = [{
                'code': 'INVALID_ARGUMENT',
                'domain': 'tpu',
                'message': (f'TPU type {tpu_type} is not available in this '
                            f'zone {zone}.')
            }]
            _format_and_log_message_from_errors(provisioner_err.errors, e, zone)
            raise provisioner_err from e

        # TODO(zhwu): Add more error code handling, if needed.
        provisioner_err.errors = [{
            'code': 'UNKNOWN',
            'domain': 'tpu',
            'message': stderr
        }]
        _format_and_log_message_from_errors(provisioner_err.errors, e, zone)
        raise provisioner_err from e


def delete_tpu_node(project_id: str, zone: str, tpu_node_config: Dict[str,
                                                                      str]):
    """Delete a TPU node with gcloud CLI.

    This is used for both stopping and terminating a cluster with a TPU node. It
    is ok to call this function to delete the TPU node when stopping the cluster
    because the host VM will be stopped and have all the information preserved.
    """
    tpu_name = tpu_node_config['name']
    try:
        cmd = (f'gcloud compute tpus delete {tpu_name} '
               f'--project={project_id} '
               f'--zone={zone}')
        logger.debug(f'Deleting TPU {tpu_name} with cmd:\n{cmd}')
        proc = subprocess.run(
            f'yes | {cmd}',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            check=True,
        )
        stdout = proc.stdout.decode('ascii')
        logger.debug(stdout)
    except subprocess.CalledProcessError as e:
        stdout = e.stdout.decode('ascii')
        stderr = e.stderr.decode('ascii')
        if 'ERROR: (gcloud.compute.tpus.delete) NOT_FOUND' in stderr:
            logger.warning(f'TPU {tpu_name} does not exist; skipped deletion.')
        else:
            raise RuntimeError(f'\nFailed to terminate TPU node {tpu_name} for '
                               'cluster {cluster_name}:\n'
                               '**** STDOUT ****\n'
                               f'{stdout}\n'
                               '**** STDERR ****\n'
                               f'{stderr}') from e
