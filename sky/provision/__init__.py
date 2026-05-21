"""Cloud provision interface.

This module provides a standard low-level interface that all
providers supported by SkyPilot need to follow.
"""
import dataclasses
import functools
import inspect
import typing
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple, Type

from sky import models
from sky import sky_logging
# These provision.<cloud> modules should never fail even if underlying cloud SDK
# dependencies are not installed. This is ensured by using sky.adaptors inside
# these modules, for lazy loading of cloud SDKs.
from sky.provision import aws
from sky.provision import azure
from sky.provision import common
from sky.provision import cudo
from sky.provision import fluidstack
from sky.provision import gcp
from sky.provision import hyperbolic
from sky.provision import kubernetes
from sky.provision import lambda_cloud
from sky.provision import mithril
from sky.provision import nebius
from sky.provision import oci
from sky.provision import primeintellect
from sky.provision import runpod
from sky.provision import scp
from sky.provision import seeweb
from sky.provision import shadeform
from sky.provision import slurm
from sky.provision import ssh
from sky.provision import vast
from sky.provision import verda
from sky.provision import vsphere
from sky.provision import yotta
from sky.utils import command_runner
from sky.utils import timeline

if typing.TYPE_CHECKING:
    from sky import task as task_lib
    from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)


@dataclasses.dataclass
class TemplateSpec:
    """A cluster config template path plus extra variables.

    Cluster config templates are the Jinja-rendered per-cloud YAMLs
    under ``sky/templates/`` (e.g. ``aws-ray.yml.j2``) that drive
    provisioning. See ``sky.backends.backend_utils.write_cluster_config``.

    ``template_path`` is either an absolute path (for plugin-shipped
    templates) or a bare filename relative to ``sky/templates/`` (for
    in-tree templates). ``variables`` are merged into the standard
    template variables when the template is rendered.
    """
    template_path: str
    variables: Dict[str, Any] = dataclasses.field(default_factory=dict)


class TemplateOverrideFn(Protocol):
    """Callable signature for ``Provisioner.template_override``.

    Called by the backend at launch time. Return a ``TemplateSpec`` to
    use a custom cluster config template instead of the cloud's
    default (``_get_cluster_config_template(cloud)`` in
    ``cloud_vm_ray_backend``), or ``None`` to use the cloud's default.
    """

    # pylint: disable=unnecessary-ellipsis

    def __call__(
        self,
        task: 'task_lib.Task',
        *,
        _extra_launch_context: Dict[str, Any],
        _is_launched_by_jobs_controller: bool,
    ) -> Optional[TemplateSpec]:
        ...


@dataclasses.dataclass
class Provisioner:
    """Registered provisioner for a cloud.

    ``module`` is a module-shaped object (typically a Python module,
    but any object with the relevant attributes works) providing the
    routed lifecycle functions: ``run_instances``,
    ``terminate_instances``, ``query_instances``, etc. Plugin authors
    look at any built-in cloud module (e.g.
    ``sky/provision/aws.py``, ``sky/provision/kubernetes/__init__.py``)
    for the canonical shape.

    ``template_override`` is an optional hook called at launch time —
    outside the routed lifecycle dispatch — that lets the plugin
    redirect a task to a custom Jinja template + extra variables.
    """
    module: Any
    template_override: Optional[TemplateOverrideFn] = None


_registered_provisioners: Dict[str, Provisioner] = {}


def register_provisioner(
    cloud_name: str,
    module: Any,
    *,
    template_override: Optional[TemplateOverrideFn] = None,
) -> None:
    """Register a Provisioner under a cloud name. Last registration wins.

    Plugins call this in their ``install()`` phase. ``cloud_name``
    matches the lowercase canonical cloud name (e.g. ``'kubernetes'``,
    ``'aws'``).
    """
    _registered_provisioners[cloud_name.lower()] = Provisioner(
        module=module, template_override=template_override)
    logger.debug(
        'Registered Provisioner for %r: module=%s, '
        'template_override=%s', cloud_name.lower(),
        type(module).__name__, template_override is not None)


def get_registered_provisioner(cloud_name: str) -> Optional[Provisioner]:
    """Return the Provisioner registered for ``cloud_name``, or None."""
    return _registered_provisioners.get(cloud_name.lower())


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
        if module_name == 'lambda':
            module_name = 'lambda_cloud'
        # Registered Provisioner methods take precedence over the static
        # cloud module's; if the registered Provisioner's module does not
        # define ``func.__name__``, dispatch falls through to the existing
        # cloud module.
        plugin = _registered_provisioners.get(module_name)
        plugin_module = plugin.module if plugin is not None else None
        existing_module = globals().get(module_name)
        assert (plugin_module is not None or existing_module
                is not None), (f'Unknown provider: {module_name}')

        impl = None
        if plugin_module is not None:
            impl = getattr(plugin_module, func.__name__, None)
        if impl is None and existing_module is not None:
            impl = getattr(existing_module, func.__name__, None)

        if impl is not None:
            return impl(*args, **kwargs)

        # Neither side implements it — fall back to the decorator's default
        # body (typically ``raise NotImplementedError``).
        return func(provider_name, *args, **kwargs)

    return _wrapper


# pylint: disable=unused-argument


@timeline.event
@_route_to_cloud_impl
def query_instances(
    provider_name: str,
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """Query instances.

    Returns a dictionary of instance IDs and a tuple of (status, reason for
    being in status if any).

    A None status means the instance is marked as "terminated"
    or "terminating".

    Args:
        retry_if_missing: Whether to retry the call to the cloud api if the
          cluster is not found when querying the live status on the cloud.
          NOTE: This is currently only used on kubernetes.
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
def apply_volume(provider_name: str,
                 volume_config: models.VolumeConfig) -> models.VolumeConfig:
    """Create or register a volume.

    This function creates or registers a volume with the provided configuration,
    and returns a VolumeConfig object with updated configuration.
    """
    raise NotImplementedError


@_route_to_cloud_impl
def delete_volume(provider_name: str,
                  volume_config: models.VolumeConfig) -> models.VolumeConfig:
    """Delete a volume."""
    raise NotImplementedError


@_route_to_cloud_impl
def get_volume_usedby(
    provider_name: str,
    volume_config: models.VolumeConfig,
) -> Tuple[List[str], List[str]]:
    """Get the usedby of a volume.

    Returns:
        usedby_pods: List of pods using the volume. These may include pods
                     not created by SkyPilot.
        usedby_clusters: List of clusters using the volume.
    """
    raise NotImplementedError


@_route_to_cloud_impl
def refresh_volume_config(
    provider_name: str,
    volume_config: models.VolumeConfig,
) -> Tuple[bool, models.VolumeConfig]:
    """Whether need to refresh the volume config in the cloud.

    Returns:
        need_refresh: Whether need to refresh the volume config.
        volume_config: The volume config to be refreshed.
    """
    return False, volume_config


@_route_to_cloud_impl
def get_all_volumes_usedby(
    provider_name: str, configs: List[models.VolumeConfig]
) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str]]:
    """Get the usedby of all volumes.

    Args:
        provider_name: Name of the provider.
        configs: List of VolumeConfig objects.

    Returns:
        usedby_pods: Dict of usedby pods.
        usedby_clusters: Dict of usedby clusters.
        failed_volume_names: Set of volume names whose usedby info
          failed to fetch.
    """
    raise NotImplementedError


@_route_to_cloud_impl
def map_all_volumes_usedby(
        provider_name: str, used_by_pods: Dict[str, Any],
        used_by_clusters: Dict[str, Any],
        config: models.VolumeConfig) -> Tuple[List[str], List[str]]:
    """Map the usedby resources of a volume."""
    raise NotImplementedError


@_route_to_cloud_impl
def get_all_volumes_errors(
        provider_name: str,
        configs: List[models.VolumeConfig]) -> Dict[str, Optional[str]]:
    """Get error messages for all volumes.

    Checks if volumes have errors (e.g., pending state due to
    misconfiguration) and returns appropriate error messages.

    Args:
        provider_name: Name of the provider.
        configs: List of VolumeConfig objects.

    Returns:
        Dictionary mapping volume name to error message (None if no error).
    """
    # Default implementation returns empty dict (no error checking)
    del provider_name, configs
    return {}


@_route_to_cloud_impl
def run_instances(provider_name: str, region: str, cluster_name: str,
                  cluster_name_on_cloud: str,
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
def cleanup_cluster_resources(
    provider_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Cleanup all cloud resources for a cluster (services, etc.).

    Called during post-teardown to ensure resources are cleaned up even when
    instances were deleted externally. Currently only Kubernetes needs this
    to clean up orphaned services.

    Args:
        provider_name: Name of the cloud provider
        cluster_name_on_cloud: The cluster name on cloud
        provider_config: Provider configuration dictionary
    """
    # Default implementation does nothing - only Kubernetes overrides this
    del provider_name, cluster_name_on_cloud, provider_config


@_route_to_cloud_impl
def cleanup_custom_multi_network(
    provider_name: str,
    cluster_name_on_cloud: str,
    provider_config: Dict[str, Any],
    failover: bool = False,
) -> None:
    """Cleanup custom multi-network."""
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

    The underlying implementation is responsible for retries and timeout, e.g.
    kubernetes will wait for the service that expose the ports to be ready
    before returning the endpoint info.

    Returns a dict with port as the key and a list of common.Endpoint.
    """
    del provider_name, provider_config, cluster_name_on_cloud  # unused
    return common.query_ports_passthrough(ports, head_ip)


@_route_to_cloud_impl
def wait_instances(provider_name: str, region: str, cluster_name_on_cloud: str,
                   state: Optional['status_lib.ClusterStatus']) -> None:
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
    **credentials: Dict[str, Any],
) -> List[command_runner.CommandRunner]:
    """Get a command runner for the given cluster."""
    ip_list = cluster_info.get_feasible_ips()
    port_list = cluster_info.get_ssh_ports()
    return command_runner.SSHCommandRunner.make_runner_list(
        node_list=zip(ip_list, port_list),
        **credentials,
    )
