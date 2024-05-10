"""Cloud provision interface.

This module provides a standard low-level interface that all
providers supported by SkyPilot need to follow.
"""
import functools
import inspect
from typing import Any, Dict, List, Optional, Type

from sky import sky_logging
from sky import status_lib
# These provision.<cloud> modules should never fail even if underlying cloud SDK
# dependencies are not installed. This is ensured by using sky.adaptors inside
# these modules, for lazy loading of cloud SDKs.
from sky.provision import aws
from sky.provision import azure
from sky.provision import common
from sky.provision import cudo
from sky.provision import fluidstack
from sky.provision import gcp
from sky.provision import kubernetes
from sky.provision import runpod
from sky.provision import vsphere
from sky.utils import command_runner

logger = sky_logging.init_logger(__name__)


def _route_to_cloud_impl(func):

    @functools.wraps(func)
    def _wrapper(*args, **kwargs):
        # check the signature to fail early
        inspect.signature(func).bind(*args, **kwargs)
        if args:
            provider_name = args[0]
            args = args[1:]
        else:
            provider_name = kwargs.pop('provider_name')

        module_name = provider_name.lower()
        module = globals().get(module_name)
        assert module is not None, f'Unknown provider: {module_name}'

        impl = getattr(module, func.__name__, None)
        if impl is not None:
            return impl(*args, **kwargs)

        # If implementation does not exist, fall back to default implementation
        return func(provider_name, *args, **kwargs)

    return _wrapper


# pylint: disable=unused-argument


@_route_to_cloud_impl
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


@_route_to_cloud_impl
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


@_route_to_cloud_impl
def run_instances(provider_name: str, region: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Start instances with bootstrapped configuration."""
    raise NotImplementedError


@_route_to_cloud_impl
def stop_instances(
    provider_name: str,
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    worker_only: bool = False,
) -> None:
    """Stop running instances."""
    raise NotImplementedError


@_route_to_cloud_impl
def terminate_instances(
    provider_name: str,
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    worker_only: bool = False,
) -> None:
    """Terminate running or stopped instances."""
    raise NotImplementedError


@_route_to_cloud_impl
def open_ports(
    provider_name: str,
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Open ports for inbound traffic."""
    raise NotImplementedError


@_route_to_cloud_impl
def cleanup_ports(
    provider_name: str,
    cluster_name_on_cloud: str,
    # TODO: make ports optional and allow cleaning up only specified ports.
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Delete any opened ports."""
    raise NotImplementedError


@_route_to_cloud_impl
def query_ports(
    provider_name: str,
    cluster_name_on_cloud: str,
    ports: List[str],
    head_ip: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[common.Endpoint]]:
    """Query details about ports on a cluster.

    If head_ip is provided, it may be used by the cloud implementation to
    return the endpoint without querying the cloud provider. If head_ip is not
    provided, the cloud provider will be queried to get the endpoint info.

    Returns a dict with port as the key and a list of common.Endpoint.
    """
    del provider_name, provider_config, cluster_name_on_cloud  # unused
    return common.query_ports_passthrough(ports, head_ip)


@_route_to_cloud_impl
def wait_instances(provider_name: str, region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """Wait instances until they ends up in the given state."""
    raise NotImplementedError


@_route_to_cloud_impl
def get_cluster_info(
        provider_name: str,
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    """Get the metadata of instances in a cluster."""
    raise NotImplementedError


@_route_to_cloud_impl
def get_command_runners(
    provider_name: str,
    cluster_info: common.ClusterInfo,
    **crednetials: Dict[str, Any],
) -> List[command_runner.CommandRunner]:
    """Get a command runner for the given cluster."""
    ip_list = cluster_info.get_feasible_ips()
    port_list = cluster_info.get_ssh_ports()
    return command_runner.SSHCommandRunner.make_runner_list(
        node_list=zip(ip_list, port_list),
        **crednetials,
    )
