"""AWS instance provisioning."""
from typing import Dict, List, Any, Optional

from botocore import config

from sky.adaptors import aws
from sky import status_lib

BOTO_MAX_RETRIES = 12
# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'


def _default_ec2_resource(region: str) -> Any:
    return aws.resource(
        'ec2',
        region_name=region,
        config=config.Config(retries={'max_attempts': BOTO_MAX_RETRIES}))


def _cluster_name_filter(cluster_name: str) -> List[Dict[str, Any]]:
    return [{
        'Name': f'tag:{TAG_RAY_CLUSTER_NAME}',
        'Values': [cluster_name],
    }]


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


# TODO(suquark): Does it make sense to not expose this and always assume
# non_terminated_only=True?
# Will there be callers who would want this to be False?
# stop() and terminate() for example already implicitly assume non-terminated.
def query_instances(
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name, provider_config)
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    filters = _cluster_name_filter(cluster_name)
    instances = ec2.instances.filter(Filters=filters)
    status_map = {
        'pending': status_lib.ClusterStatus.INIT,
        'running': status_lib.ClusterStatus.UP,
        # TODO(zhwu): stopping and shutting-down could occasionally fail
        # due to internal errors of AWS. We should cover that case.
        'stopping': status_lib.ClusterStatus.STOPPED,
        'stopped': status_lib.ClusterStatus.STOPPED,
        'shutting-down': None,
        'terminated': None,
    }
    statuses = {}
    for inst in instances:
        status = status_map[inst.state['Name']]
        if non_terminated_only and status is None:
            continue
        statuses[inst.id] = status
    return statuses


def stop_instances(
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name, provider_config)
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    filters: List[Dict[str, Any]] = [
        {
            'Name': 'instance-state-name',
            'Values': ['pending', 'running'],
        },
        *_cluster_name_filter(cluster_name),
    ]
    if worker_only:
        filters.append({
            'Name': f'tag:{TAG_RAY_NODE_KIND}',
            'Values': ['worker'],
        })
    instances = _filter_instances(ec2, filters, None, None)
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
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, (cluster_name, provider_config)
    region = provider_config['region']
    ec2 = _default_ec2_resource(region)
    filters = [
        {
            'Name': 'instance-state-name',
            # exclude 'shutting-down' or 'terminated' states
            'Values': ['pending', 'running', 'stopping', 'stopped'],
        },
        *_cluster_name_filter(cluster_name),
    ]
    if worker_only:
        filters.append({
            'Name': f'tag:{TAG_RAY_NODE_KIND}',
            'Values': ['worker'],
        })
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Instance
    instances = _filter_instances(ec2, filters, None, None)
    instances.terminate()
    # TODO(suquark): Currently, the implementation of GCP and Azure will
    #  wait util the cluster is fully terminated, while other clouds just
    #  trigger the termination process (via http call) and then return.
    #  It's not clear that which behavior should be expected. We will not
    #  wait for the termination for now, since this is the default behavior
    #  of most cloud implementations (including AWS).
