"""Utilities for GCP instances."""
from typing import Dict, List, Optional

from sky import sky_logging
from sky.adaptors import gcp

logger = sky_logging.init_logger(__name__)

# Using v2 according to
# https://cloud.google.com/tpu/docs/managing-tpus-tpu-vm#create-curl # pylint: disable=line-too-long
TPU_VERSION = 'v2'


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
    def delete_firewall_rule(
        cls,
        project_id: str,
        cluster_name_on_cloud: str,
    ) -> None:
        raise NotImplementedError

    @classmethod
    def get_vpc_name(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> Optional[str]:
        raise NotImplementedError

    @classmethod
    def get_firewall_rule_names_start_with(
        cls,
        project_id: str,
        prefix: str,
    ) -> List[str]:
        raise NotImplementedError

    @classmethod
    def create_firewall_rule(
        cls,
        project_id: str,
        cluster_name_on_cloud: str,
        ports: List[str],
        vpc_name: str,
    ) -> dict:
        raise NotImplementedError


def _get_firewall_rule_prefix(cluster_name_on_cloud: str) -> str:
    return f'user-ports-{cluster_name_on_cloud}-'


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
    def delete_firewall_rule(
        cls,
        project_id: str,
        cluster_name_on_cloud: str,
    ) -> None:
        prefix = _get_firewall_rule_prefix(cluster_name_on_cloud)
        previous_names = cls.get_firewall_rule_names_start_with(
            project_id, prefix)
        for rule_name in previous_names:
            cls.load_resource().firewalls().delete(
                project=project_id,
                firewall=rule_name,
            ).execute()

    @classmethod
    def get_vpc_name(
        cls,
        project_id: str,
        zone: str,
        instance: str,
    ) -> Optional[str]:
        # Any errors will be handled in the caller function.
        instance = cls.load_resource().instances().get(
            project=project_id,
            zone=zone,
            instance=instance,
        ).execute()
        # Format: projects/PROJECT_ID/global/networks/VPC_NAME
        vpc_link = instance['networkInterfaces'][0]['network']
        return vpc_link.split('/')[-1]

    @classmethod
    def get_firewall_rule_names_start_with(
        cls,
        project_id: str,
        prefix: str,
    ) -> List[str]:
        response = cls.load_resource().firewalls().list(
            project=project_id, filter=f'name eq {prefix}.*').execute()
        return [rule['name'] for rule in response.get('items', [])]

    @classmethod
    def create_firewall_rule(
        cls,
        project_id: str,
        cluster_name_on_cloud: str,
        ports: List[str],
        vpc_name: str,
    ) -> dict:
        prefix = _get_firewall_rule_prefix(cluster_name_on_cloud)
        previous_names = cls.get_firewall_rule_names_start_with(
            project_id, prefix)
        serial = 0
        while True:
            if f'{prefix}{serial}' not in previous_names:
                # GCP's firewall rule name can only contain 63 characters.
                # The prefix is 12 + len(cluster_name_on_cloud) characters,
                # which is up to 47 characters since cluster_name_on_cloud
                # is 35 characters at most (see gcp.py). That left 16 characters
                # for the serial number, which is enough to use.
                name = f'{prefix}{serial}'
                break
            serial += 1
        body = {
            'name': name,
            'description': f'Allow user-specified port {ports} for cluster {cluster_name_on_cloud}',
            'network': f'projects/{project_id}/global/networks/{vpc_name}',
            'selfLink': f'projects/{project_id}/global/firewalls/' + name,
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
