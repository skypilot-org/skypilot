"""Utilities for GCP instances."""
import copy
import enum
import functools
from multiprocessing import pool
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sky import sky_logging
from sky.adaptors import gcp
from sky.clouds import gcp as gcp_cloud
from sky.provision import common
from sky.provision.gcp import constants
from sky.utils import common_utils
from sky.utils import ux_utils

# Tag uniquely identifying all nodes of a cluster
TAG_SKYPILOT_CLUSTER_NAME = 'skypilot-cluster-name'
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
# Tag for the name of the node
INSTANCE_NAME_MAX_LEN = 64
INSTANCE_NAME_UUID_LEN = 8
TAG_SKYPILOT_HEAD_NODE = 'skypilot-head-node'
TAG_RAY_NODE_KIND = 'ray-node-type'

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


def _log_errors(errors: List[Dict[str, str]], e: Any, zone: str) -> None:
    """Format errors into a string."""
    if errors:
        plural = 's' if len(errors) > 1 else ''
        codes = ', '.join(repr(e.get('code', 'N/A')) for e in errors)
        messages = '; '.join(
            repr(e.get('message', 'N/A').strip('.')) for e in errors)
        logger.warning(f'create_instances: Got return code{plural} {codes} in '
                       f'{zone}: {messages}')
    else:
        logger.warning(f'create_instances: Failed with reason: {e}')


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
    def wait_for_operation(cls, operation: dict, project_id: str,
                           zone: Optional[str]) -> bool:
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
        include_head_node: bool,
    ) -> Tuple[Optional[List], List[str]]:
        """Creates multiple instances and returns result.

        Returns a tuple of (errors, list[instance_names]).
        """
        raise NotImplementedError

    @classmethod
    def start_instance(cls, node_id: str, project_id: str, zone: str) -> bool:
        """Start a stopped instance."""
        raise NotImplementedError

    @classmethod
    def set_labels(cls, project_id: str, availability_zone: str, node_id: str,
                   labels: dict) -> bool:
        raise NotImplementedError

    @classmethod
    def create_node_tag(cls,
                        project_id: str,
                        availability_zone: str,
                        target_instance_id: str,
                        is_head: bool = True) -> str:
        if is_head:
            node_tag = {
                TAG_SKYPILOT_HEAD_NODE: '1',
                TAG_RAY_NODE_KIND: 'head',
            }
        else:
            node_tag = {
                TAG_SKYPILOT_HEAD_NODE: '0',
                TAG_RAY_NODE_KIND: 'worker',
            }
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
                    node_config: dict, instance_name: str) -> bool:
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
    def wait_for_operation(cls, operation: dict, project_id: str,
                           zone: Optional[str]) -> bool:
        if zone is not None:
            op_type = 'zone'
            result = (cls.load_resource().zoneOperations().get(
                project=project_id,
                operation=operation['name'],
                zone=zone,
            ).execute())
        else:
            op_type = 'global'
            result = (cls.load_resource().globalOperations().get(
                project=project_id,
                operation=operation['name'],
            ).execute())
        if 'error' in result:
            raise Exception(result['error'])

        if result['status'] == 'DONE':
            logger.debug(f'wait_for_compute_{op_type}_operation: '
                         f'Operation {operation["name"]} finished.')
            return True
        return False

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
                   labels: dict) -> bool:
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

        result = cls.wait_for_operation(operation, project_id,
                                        availability_zone)
        return result

    @classmethod
    def create_instances(
        cls,
        cluster_name: str,
        project_id: str,
        zone: str,
        node_config: dict,
        labels: dict,
        count: int,
        include_head_node: bool,
    ) -> Tuple[Optional[List], List[str]]:
        # NOTE: The syntax for bulkInsert() is different from insert().
        # bulkInsert expects resource names without prefix. Otherwise
        # it causes a 503 error.
        config = copy.deepcopy(node_config)

        if 'scheduling' in config and isinstance(config['scheduling'], list):
            # For backeward compatibility: converting the list of dictionaries
            # to a dictionary due to the use of deprecated API.
            # [{'preemptible': True}, {'onHostMaintenance': 'TERMINATE'}]
            # to {'preemptible': True, 'onHostMaintenance': 'TERMINATE'}
            config['scheduling'] = {
                k: v for d in config['scheduling'] for k, v in d.items()
            }

        for disk in config.get('disks', []):
            disk_type = disk.get('initializeParams', {}).get('diskType')
            if disk_type:
                disk['initializeParams']['diskType'] = selflink_to_name(
                    disk_type)
        config['machineType'] = selflink_to_name(config['machineType'])
        for accelerator in config.get('guestAccelerators', []):
            accelerator['acceleratorType'] = selflink_to_name(
                accelerator['acceleratorType'])

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
                    TAG_RAY_CLUSTER_NAME: cluster_name,
                    TAG_SKYPILOT_CLUSTER_NAME: cluster_name
                }),
        })

        all_names = []
        if 'reservationAffinity' in config:
            reservations = gcp_cloud.GCP().get_reservations_available_resources(
                config['machineType'],
                region=zone.rpartition('-')[0],
                zone=zone,
                specific_reservations=set(
                    config['reservationAffinity']['values']))
            # Sort the reservations by the number of available resources
            reservation_list = sorted(reservations.items(),
                                      key=lambda x: x[1],
                                      reverse=True)
            # TODO(zhwu): Convert this to parallel execution.
            # TODO(zhwu): This is not atomic as the reservation count may change
            # between the time we check and the time we create the instances, as
            # other users may be creating instances at the same time.
            # Our current implementation will skip the current region if the
            # reservation count is not enough, which is suboptimal.
            for reservation, reservation_count in reservation_list:
                if reservation_count <= 0:
                    continue
                reservation_count = min(reservation_count, count)
                logger.debug(f'Creating {reservation_count} instances '
                             f'with reservation {reservation}')
                config['reservationAffinity']['values'] = [reservation]
                errors, created_names = cls._create_instances(
                    names[:reservation_count], project_id, zone, config,
                    reservation_count, head_tag_needed[:reservation_count])
                all_names.extend(names)
                if errors:
                    return errors, all_names
                count -= reservation_count
                if count <= 0:
                    return None, all_names
                names = names[reservation_count:]
                head_tag_needed = head_tag_needed[reservation_count:]
            config.pop('reservationAffinity', None)

        errors, created_names = cls._create_instances(names, project_id, zone,
                                                      config, count,
                                                      head_tag_needed)

        all_names.extend(created_names)
        return errors, all_names

    @classmethod
    def _create_instances(
        cls,
        names: List[str],
        project_id: str,
        zone: str,
        config: dict,
        count: int,
        head_tag_needed: List[bool],
    ) -> Tuple[Optional[List], List[str]]:
        source_instance_template = config.pop('sourceInstanceTemplate', None)
        body = {
            'count': count,
            'instanceProperties': config,
            'sourceInstanceTemplate': source_instance_template,
            'perInstanceProperties': {n: {} for n in names}
        }

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
            logger.debug('Launching GCP instances with "bulkInsert" ...')
            request = cls.load_resource().instances().bulkInsert(
                project=project_id,
                zone=zone,
                body=body,
            )
            operation = request.execute(num_retries=0)
        except gcp.http_error_exception() as e:
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
            _log_errors(errors, e, zone)
            return errors, names
        errors = operation.get('error', {}).get('errors')
        if errors:
            logger.debug('create_instances: Failed to create instances. '
                         f'Reason: {errors}')
            _log_errors(errors, operation, zone)
            return errors, names

        logger.debug('Waiting GCP instances to be ready ...')
        wait_start = time.time()
        success = False
        while time.time() - wait_start < GCP_TIMEOUT:
            # Retry the wait() call until it succeeds or times out.
            # This is because the wait() call is only best effort, and does not
            # guarantee that the operation is done when it returns.
            # Reference: https://cloud.google.com/workflows/docs/reference/googleapis/compute/v1/zoneOperations/wait # pylint: disable=line-too-long
            request = cls.load_resource().zoneOperations().wait(
                project=project_id,
                operation=operation['name'],
                zone=zone,
            )
            request.http.timeout = GCP_TIMEOUT - (time.time() - wait_start)
            result = request.execute(num_retries=GCP_CREATE_MAX_RETRIES)
            success = result['status'] == 'DONE'
            if success:
                break
            logger.debug(f'create_instances: Retry waiting for operation '
                         f'{operation["name"]} to finish (result: {result})...')
        else:
            logger.warning('create_instances: Timeout waiting for creation '
                           'operation, cancelling the operation ...')
            request = cls.load_resource().zoneOperations().delete(
                project=project_id,
                operation=operation['name'],
                zone=zone,
            )
            request.http.timeout = GCP_TIMEOUT - (time.time() - wait_start)
            request.execute(num_retries=GCP_CREATE_MAX_RETRIES)
            errors = [{
                'code': 'TIMEOUT',
                'message': 'Timeout waiting for creation operation',
                'domain': 'create_instances'
            }]
            _log_errors(errors, None, zone)
            return errors, names

        # NOTE: Error example:
        # {
        #   'code': 'VM_MIN_COUNT_NOT_REACHED',
        #   'message': 'Requested minimum count of 4 VMs could not be created.'
        # }
        errors = result.get('error', {}).get('errors')
        if errors:
            logger.debug(
                'create_instances: Failed to create instances. Reason: '
                f'{errors}')
            _log_errors(errors, result, zone)
            return errors, names
        assert success, ('Failed to create instances, but there is no error. '
                         f'Instance status: {result}')
        # assign labels for head node
        with pool.ThreadPool() as p:
            p.starmap(cls.create_node_tag,
                      [(project_id, zone, names[i], head_tag_needed[i])
                       for i in range(count)])
        return None, names

    @classmethod
    def start_instance(cls, node_id: str, project_id: str, zone: str) -> bool:
        operation = (cls.load_resource().instances().start(
            project=project_id,
            zone=zone,
            instance=node_id,
        ).execute())

        result = cls.wait_for_operation(operation, project_id, zone)
        return result

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
                    node_config: dict, instance_name: str) -> bool:
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
            logger.warning(f'googleapiclient.errors.HttpError: {e.reason}')
            return False

        result = cls.wait_for_operation(operation, project_id,
                                        availability_zone)

        return result


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
            constants.TPU_VERSION,
            credentials=None,
            cache_discovery=False,
            discoveryServiceUrl='https://tpu.googleapis.com/$discovery/rest')

    @classmethod
    def wait_for_operation(cls, operation: dict, project_id: str,
                           zone: Optional[str]) -> bool:
        """Poll for TPU operation until finished."""
        del project_id, zone  # unused
        result = (cls.load_resource().projects().locations().operations().get(
            name=str(operation['name'])).execute(num_retries=GCP_MAX_RETRIES))
        if 'error' in result:
            raise Exception(result['error'])

        if 'response' in result:
            logger.debug('wait_for_tpu_operation: '
                         f'Operation {operation["name"]} finished.')
            return True
        return False

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
                   labels: dict) -> bool:
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

        result = cls.wait_for_operation(operation, project_id,
                                        availability_zone)

        return result

    @classmethod
    def create_instances(
        cls,
        cluster_name: str,
        project_id: str,
        zone: str,
        node_config: dict,
        labels: dict,
        count: int,
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
                    TAG_RAY_CLUSTER_NAME: cluster_name,
                    TAG_SKYPILOT_CLUSTER_NAME: cluster_name
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
                node_config['labels'][TAG_SKYPILOT_HEAD_NODE] = '1'
                node_config['labels'][TAG_RAY_NODE_KIND] = 'head'
            else:
                node_config['labels'][TAG_SKYPILOT_HEAD_NODE] = '0'
                node_config['labels'][TAG_RAY_NODE_KIND] = 'worker'
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
                    _log_errors(errors, e, zone)
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
                _log_errors(errors, e, zone)
                return errors, names
        errors = []
        logger.info(str(operations))
        for operation in operations:
            error = operation.get('error', {}).get('details')
            if error:
                errors.extend(error)
        if errors:
            logger.debug('create_instances: Failed to create instances. '
                         f'Reason: {errors}')
            _log_errors(errors, operations, zone)
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
            _log_errors(errors, None, zone)
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
            _log_errors(errors, results, zone)
            return errors, names
        assert all(success), (
            'Failed to create instances, but there is no error. '
            f'Instance status: {results}')
        return None, names

    @classmethod
    def start_instance(cls, node_id: str, project_id: str, zone: str) -> bool:
        operation = (cls.load_resource().projects().locations().nodes().start(
            name=node_id).execute())

        # FIXME: original implementation has the 'max_polls=MAX_POLLS' option.
        result = cls.wait_for_operation(operation, project_id, zone)

        return result

    @classmethod
    def resize_disk(cls, project_id: str, availability_zone: str,
                    node_config: dict, instance_name: str) -> bool:
        """Resize the disk a machine image with a different size is used.

        TODO: Implement the feature to attach persistent disks for TPU VMs.
        The boot disk of TPU VMs is not resizable, and users need to add a
        persistent disk to expand disk capacity. Related issue: #2387
        """
        return False

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
    TPU = 'tpu'


def get_node_type(node: dict) -> GCPNodeType:
    """Returns node type based on the keys in ``node``.

    This is a very simple check. If we have a ``machineType`` key,
    this is a Compute instance. If we don't have a ``machineType`` key,
    but we have ``acceleratorType``, this is a TPU. Otherwise, it's
    invalid and an exception is raised.

    This works for both node configs and API returned nodes.
    """

    if 'machineType' not in node and 'acceleratorType' not in node:
        raise ValueError(
            'Invalid node. For a Compute instance, "machineType" is '
            'required. '
            'For a TPU instance, "acceleratorType" and no "machineType" '
            'is required. '
            f'Got {list(node)}')

    if 'machineType' not in node and 'acceleratorType' in node:
        return GCPNodeType.TPU
    return GCPNodeType.COMPUTE
