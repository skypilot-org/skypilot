"""AWS instance provisioning."""
from typing import Dict, List, Any, Optional

from botocore import config
from sky.adaptors import aws

BOTO_MAX_RETRIES = 12
# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'


def _filter_instances(ec2, filters: List[Dict[str, Any]],
                      included_instances: Optional[List[str]],
                      excluded_instances: Optional[List[str]]):
    instances = ec2.instances.filter(Filters=filters)
    if included_instances is not None and excluded_instances is not None:
        raise ValueError('"included_instances" and "exclude_instances"'
                         'cannot be specified at the same time.')
    if included_instances is not None:
        instances = instances.filter(InstanceIds=included_instances)
    elif excluded_instances is not None:
        included_instances = []
        for inst in list(instances):
            if inst.id not in excluded_instances:
                included_instances.append(inst.id)
        instances = instances.filter(InstanceIds=included_instances)
    return instances


def stop_instances(
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    included_instances: Optional[List[str]] = None,
    excluded_instances: Optional[List[str]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name, provider_config)
    region = provider_config['region']
    ec2 = aws.resource(
        'ec2',
        region_name=region,
        config=config.Config(retries={'max_attempts': BOTO_MAX_RETRIES}))
    filters = [
        {
            'Name': 'instance-state-name',
            'Values': ['pending', 'running'],
        },
        {
            'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
            'Values': [cluster_name],
        },
    ]
    instances = _filter_instances(ec2, filters, included_instances,
                                  excluded_instances)
    instances.stop()
    # TODO(suquark): Currently, the implementation of GCP and Azure will
    #  wait util the cluster is fully terminated, while other clouds just
    #  trigger the termination process (via http call) and then return.
    #  It's not clear that which behavior should be expected. We will not
    #  wait for the termination for now, since this is the default behavior
    #  of most cloud implementations (including AWS).


def terminate_instances(
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    included_instances: Optional[List[str]] = None,
    excluded_instances: Optional[List[str]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name, provider_config)
    region = provider_config['region']
    ec2 = aws.resource(
        'ec2',
        region_name=region,
        config=config.Config(retries={'max_attempts': BOTO_MAX_RETRIES}))
    filters = [
        {
            'Name': 'instance-state-name',
            # exclude 'shutting-down' or 'terminated' states
            'Values': ['pending', 'running', 'stopping', 'stopped'],
        },
        {
            'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
            'Values': [cluster_name],
        },
    ]
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Instance
    instances = _filter_instances(ec2, filters, included_instances,
                                  excluded_instances)
    instances.terminate()
    # TODO(suquark): Currently, the implementation of GCP and Azure will
    #  wait util the cluster is fully terminated, while other clouds just
    #  trigger the termination process (via http call) and then return.
    #  It's not clear that which behavior should be expected. We will not
    #  wait for the termination for now, since this is the default behavior
    #  of most cloud implementations (including AWS).
