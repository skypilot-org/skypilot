"""Utilities for GCP instances."""
import re
from typing import Dict, List, Optional

from sky import sky_logging
from sky.adaptors import gcp
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# Using v2 according to
# https://cloud.google.com/tpu/docs/managing-tpus-tpu-vm#create-curl # pylint: disable=line-too-long
TPU_VERSION = 'v2'

_FIREWALL_RESOURCE_NOT_FOUND_PATTERN = re.compile(
    r'The resource \'projects/.*/global/firewalls/.*\' was not found')


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
    NEED_TO_STOP_STATES: List[str] = []
    NON_STOPPED_STATES: List[str] = []
    NEED_TO_TERMINATE_STATES: List[str] = []

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
    ) -> List[str]:
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


class GCPComputeInstance(GCPInstance):
    """Instance handler for GCP compute instances."""
    NEED_TO_STOP_STATES = [
        'PROVISIONING',
        'STAGING',
        'RUNNING',
    ]

    NON_STOPPED_STATES = NEED_TO_STOP_STATES + ['STOPPING']

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
    ) -> List[str]:
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
        ).execute())
        instances = response.get('items', [])
        instances = [i['name'] for i in instances]
        if included_instances:
            instances = [i for i in instances if i in included_instances]
        if excluded_instances:
            instances = [i for i in instances if i not in excluded_instances]
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
            return vpc_link.split('/')[-1]
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
                'description': f'Allow user-specified port {ports} for cluster {cluster_name_on_cloud}',
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


class GCPTPUVMInstance(GCPInstance):
    """Instance handler for GCP TPU node."""
    NEED_TO_STOP_STATES = [
        'CREATING',
        'STARTING',
        'READY',
        'RESTARTING',
    ]

    NON_STOPPED_STATES = NEED_TO_STOP_STATES + ['STOPPING']

    @classmethod
    def load_resource(cls):
        return gcp.build(
            'tpu',
            TPU_VERSION,
            credentials=None,
            cache_discovery=False,
            discoveryServiceUrl='https://tpu.googleapis.com/$discovery/rest')

    @classmethod
    def wait_for_operation(cls, operation: dict, project_id: str,
                           zone: Optional[str]) -> bool:
        """Poll for TPU operation until finished."""
        del project_id, zone  # unused
        result = (cls.load_resource().projects().locations().operations().get(
            name=str(operation['name'])).execute())
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
    ) -> List[str]:
        path = f'projects/{project_id}/locations/{zone}'
        try:
            response = (cls.load_resource().projects().locations().nodes().list(
                parent=path).execute())
        except gcp.http_error_exception() as e:
            # SKY: Catch HttpError when accessing unauthorized region.
            # Return empty list instead of raising exception to not break
            # ray down.
            logger.warning(f'googleapiclient.errors.HttpError: {e.reason}')
            return []

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
        instances = [i['name'] for i in instances]

        if included_instances:
            instances = [i for i in instances if i in included_instances]
        if excluded_instances:
            instances = [i for i in instances if i not in excluded_instances]

        return instances

    @classmethod
    def stop(cls, project_id: str, zone: str, instance: str) -> dict:
        """Stop a TPU node."""
        del project_id, zone  # unused
        operation = cls.load_resource().projects().locations().nodes().stop(
            name=instance).execute()
        return operation

    @classmethod
    def terminate(cls, project_id: str, zone: str, instance: str) -> dict:
        """Terminate a TPU node."""
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
            return vpc_link.split('/')[-1]
        except gcp.http_error_exception() as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Failed to get VPC name for instance {instance}') from e
