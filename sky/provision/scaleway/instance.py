"""Scaleway instance provisioning."""
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Type

from sky import sky_logging
from sky import status_lib
from sky.provision import common
from sky.provision.scaleway import instance_utils

logger = sky_logging.init_logger(__name__)

# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'

_INSTANCE_RESOURCE_NOT_FOUND_PATTERN = re.compile(
    r'The resource \'projects/.*/zones/.*/instances/.*\' was not found')


def _filter_instances(
    handlers: Iterable[Type[instance_utils.ScalewayInstance]],
    project_id: str,
    zone: str,
    label_filters: Dict[str, str],
    status_filters_fn: Callable[[Type[instance_utils.ScalewayInstance]],
                                Optional[List[str]]],
    included_instances: Optional[List[str]] = None,
    excluded_instances: Optional[List[str]] = None,
) -> Dict[Type[instance_utils.ScalewayInstance], List[str]]:
    """Filter instances using all instance handlers."""
    raise NotImplementedError


def _wait_for_operations(
    handlers_to_operations: Dict[Type[instance_utils.ScalewayInstance],
                                 List[dict]],
    project_id: str,
    zone: Optional[str],
) -> None:
    """Poll for compute zone / global operation until finished.

    If zone is None, then the operation is global.
    """
    raise NotImplementedError


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    assert provider_config is not None, cluster_name_on_cloud
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    label_filters = {TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    if worker_only:
        label_filters[TAG_RAY_NODE_KIND] = 'worker'
    raise NotImplementedError


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']

    label_filters = {TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    if worker_only:
        label_filters[TAG_RAY_NODE_KIND] = 'worker'

    raise NotImplementedError
    # We don't wait for the instances to be terminated, as it can take a long
    # time (same as what we did in ray's node_provider).


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    zone = provider_config['availability_zone']
    project_id = provider_config['project_id']
    raise NotImplementedError


def cleanup_ports(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    raise NotImplementedError


def bootstrap_instances(
        provider_name: str, region: str, cluster_name_on_cloud: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstrap configurations for a cluster.

    This function sets up auxiliary resources for a specified cluster
    with the provided configuration,
    and returns a ProvisionConfig object with updated configuration.
    These auxiliary resources could include security policies, network
    configurations etc. These resources tend to be free or very cheap,
    but it takes time to set them up from scratch. So we generally
    cache or reuse them when possible.
    """
    raise NotImplementedError


def run_instances(provider_name: str, region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Start instances with bootstrapped configuration."""
    raise NotImplementedError


def wait_instances(provider_name: str, region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Wait instances until they ends up in the given state."""
    raise NotImplementedError


def get_cluster_info(provider_name: str, region: str,
                     cluster_name_on_cloud: str) -> common.ClusterInfo:
    """Get the metadata of instances in a cluster."""
    raise NotImplementedError


def query_instances(
    provider_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """Query instances.

    Returns a dictionary of instance IDs and status.

    A None status means the instance is marked as "terminated"
    or "terminating".
    """
    raise NotImplementedError
