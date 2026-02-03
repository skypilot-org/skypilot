"""Interfaces: clouds, regions, and zones.

clouds.Cloud is lightweight stateless objects. SkyPilot may create multiple such
objects; therefore, subclasses should take care to make methods inexpensive to
call, and should not store heavy state. If state needs to be queried from the
cloud provider and cached, create a module in sky/clouds/utils/ and probably add
caches for the return value (e.g., sky/clouds/utils/gcp_utils), so they can be
reused across cloud object creation.
"""
import collections
import enum
import math
import typing
from typing import (Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple,
                    Union)

from typing_extensions import assert_never

from sky import catalog
from sky import exceptions
from sky import skypilot_config
from sky.utils import log_utils
from sky.utils import resources_utils
from sky.utils import timeline
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import status_lib
    from sky.utils import volume as volume_lib


class CloudImplementationFeatures(enum.Enum):
    """Features that might not be implemented for all clouds.

    Used by Cloud.check_features_are_supported().

    NOTE: If any new feature is added, please check and update
    _cloud_unsupported_features in all clouds to make sure the
    check_features_are_supported() works as expected.
    """
    STOP = 'stop'
    MULTI_NODE = 'multi-node'
    CLONE_DISK_FROM_CLUSTER = 'clone_disk_from_cluster'
    IMAGE_ID = 'image_id'
    DOCKER_IMAGE = 'docker_image'
    SPOT_INSTANCE = 'spot_instance'
    CUSTOM_DISK_TIER = 'custom_disk_tier'
    CUSTOM_NETWORK_TIER = 'custom_network_tier'
    OPEN_PORTS = 'open_ports'
    STORAGE_MOUNTING = 'storage_mounting'
    HOST_CONTROLLERS = 'host_controllers'  # Can run jobs/serve controllers
    HIGH_AVAILABILITY_CONTROLLERS = ('high_availability_controllers'
                                    )  # Controller can auto-restart
    AUTO_TERMINATE = 'auto_terminate'  # Pod/VM can stop or down itself
    AUTOSTOP = 'autostop'  # Pod/VM can stop itself
    AUTODOWN = 'autodown'  # Pod/VM can down itself
    # Pod/VM can have customized multiple network interfaces
    # e.g. GCP GPUDirect TCPX
    CUSTOM_MULTI_NETWORK = 'custom_multi_network'
    # Some cloud providers provide additional, directly connected
    # storage devices to VMs (eg: AWS Instance Storage).
    LOCAL_DISK = 'local_disk'


# Use str, enum.Enum to allow CloudCapability to be used as a string.
class CloudCapability(str, enum.Enum):
    # Compute capability.
    COMPUTE = 'compute'
    # Storage capability.
    STORAGE = 'storage'


ALL_CAPABILITIES = [CloudCapability.COMPUTE, CloudCapability.STORAGE]


class Region(collections.namedtuple('Region', ['name'])):
    """A region."""
    name: str
    zones: Optional[List['Zone']] = None

    def set_zones(self, zones: List['Zone']):
        self.zones = zones
        for zone in self.zones:
            zone.region = self
        return self


class Zone(collections.namedtuple('Zone', ['name'])):
    """A zone, typically grouped under a region."""
    name: str
    region: Region


class ProvisionerVersion(enum.Enum):
    """The version of the provisioner.

    1: [Deprecated] ray node provider based implementation
    2: [Deprecated] ray node provider for provisioning and SkyPilot provisioner
    for stopping and termination
    3: SkyPilot provisioner for both provisioning and stopping
    """
    RAY_AUTOSCALER = 1
    RAY_PROVISIONER_SKYPILOT_TERMINATOR = 2
    SKYPILOT = 3

    def __ge__(self, other):
        return self.value >= other.value


class StatusVersion(enum.Enum):
    """The version of the status query.

    1: [Deprecated] cloud-CLI based implementation
    2: SkyPilot provisioner based implementation
    """
    CLOUD_CLI = 1
    SKYPILOT = 2

    def __ge__(self, other):
        return self.value >= other.value


class OpenPortsVersion(enum.Enum):
    """The version of the open ports implementation.

    1: Open ports on launching of the cluster only, cannot be modified after
    provisioning of the cluster. This is for clouds like RunPod which only
    accepts port argument on VM creation API, and requires Web GUI and an VM
    restart to update ports. We currently do not support this.
    2: Open ports after provisioning of the cluster, updatable. This is for most
    of the cloud providers which allow opening ports using an programmable API
    and won't affect the running VMs.
    """
    LAUNCH_ONLY = 'LAUNCH ONLY'
    UPDATABLE = 'UPDATABLE'

    def __le__(self, other):
        versions = list(OpenPortsVersion)
        return versions.index(self) <= versions.index(other)


class Cloud:
    """A cloud provider."""

    _REPR = '<Cloud>'
    _DEFAULT_DISK_TIER = resources_utils.DiskTier.MEDIUM
    _BEST_DISK_TIER = resources_utils.DiskTier.ULTRA
    _SUPPORTED_DISK_TIERS = {resources_utils.DiskTier.BEST}
    _SUPPORTED_NETWORK_TIERS = {
        resources_utils.NetworkTier.STANDARD, resources_utils.NetworkTier.BEST
    }
    _SUPPORTS_SERVICE_ACCOUNT_ON_REMOTE = False

    # The version of provisioner and status query. This is used to determine
    # the code path to use for each cloud in the backend.
    # NOTE: new clouds being added should use the latest version, i.e. SKYPILOT.
    PROVISIONER_VERSION = ProvisionerVersion.RAY_AUTOSCALER
    STATUS_VERSION = StatusVersion.CLOUD_CLI
    OPEN_PORTS_VERSION = OpenPortsVersion.UPDATABLE

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
        """Returns the maximum length limit of a cluster name.

        This method is used by check_cluster_name_is_valid() to check if the
        cluster name is too long.

        None means no limit.
        """
        return None

    @classmethod
    def supports_service_account_on_remote(cls) -> bool:
        """Returns whether the cloud supports service account on remote cluster.

        This method is used by backend_utils.write_cluster_config() to decide
        whether to upload user's local cloud credential files to the remote
        cluster.

        If a cloud supports service account on remote cluster, the user's local
        cloud credential files are not needed to be uploaded to the remote
        instance, as the remote instance can be assigned with a service account
        that has the necessary permissions to access the cloud resources.
        """
        return cls._SUPPORTS_SERVICE_ACCOUNT_ON_REMOTE

    @classmethod
    def uses_ray(cls) -> bool:
        """Returns whether this cloud uses Ray as the distributed
        execution framework.
        """
        return True

    #### Regions/Zones ####

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources_lib.Resources'] = None,
    ) -> List[Region]:
        """Returns the regions that offer the specified resources.

        The order of the regions follow the order of the regions returned by
        sky/catalog/common.py#get_region_zones().
        When region or zone is not None, the returned value will be limited to
        the specified region/zone.

        Returns:
            A set of `Region`s that have the offerings for the specified
            resources.
            For each `Region` in the set, `region.zones` is the list of `Zone`s
            which have the offerings. For the clouds that do not expose `Zone`s,
            `region.zones` is an empty list.
        """
        raise NotImplementedError

    @classmethod
    def optimize_by_zone(cls) -> bool:
        """Returns whether to optimize this cloud by zone (default: region)."""
        return False

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Optional[List[Zone]]]:
        """Loops over zones to retry for provisioning in a given region.

        Certain clouds' provisioners may handle batched requests, retrying for
        itself a list of zones under a region.  Others may need a specific zone
        per provision request (in that case, yields a one-element list for each
        zone).
        Optionally, caller can filter the yielded region/zones by specifying the
        instance_type, accelerators, and use_spot.

        Args:
            region: The region to provision.
            num_nodes: The number of nodes to provision.
            instance_type: The instance type to provision.
            accelerators: The accelerators to provision.
            use_spot: Whether to use spot instances.

        Yields:
            A list of zones that offer the requested resources in the given
            region, in the order of price.
            (1) If there is no zone that offers the specified resources, nothing
                is yielded. For example, Azure does not support zone, and
                calling this method with non-existing instance_type in the given
                region, will yield nothing, i.e. raise StopIteration.
                ```
                for zone in Azure.zones_provision_loop(region=region,
                                           instance_type='non-existing'):
                    # Will not reach here.
                ```
            (2) If the cloud's provisioner does not support `Zone`s, `None` will
                be yielded.
                ```
                for zone in Azure.zones_provision_loop(region=region,
                                           instance_type='existing-instance'):
                    assert zone is None
                ```
            This means if something is yielded, either it's None (zones are not
            supported and the region offers the resources) or it's a non-empty
            list (zones are supported and they offer the resources).

        Typical usage:

            for zones in cloud.region_zones_provision_loop(
                region,
                num_nodes,
                instance_type,
                accelerators,
                use_spot
            ):
                success = try_provision(region, zones, resources)
                if success:
                    break
        """
        raise NotImplementedError

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        """Returns the shell command to obtain the zone of instance."""
        raise NotImplementedError

    #### Normal methods ####

    def instance_type_to_hourly_cost(self, instance_type: str, use_spot: bool,
                                     region: Optional[str],
                                     zone: Optional[str]) -> float:
        """Returns the hourly on-demand/spot price for an instance type."""
        raise NotImplementedError

    def accelerators_to_hourly_cost(self, accelerators: Dict[str, int],
                                    use_spot: bool, region: Optional[str],
                                    zone: Optional[str]) -> float:
        """Returns the hourly on-demand price for accelerators."""
        raise NotImplementedError

    def get_egress_cost(self, num_gigabytes: float):
        """Returns the egress cost.

        TODO: takes into account "per month" accumulation per account.
        """
        raise NotImplementedError

    def is_same_cloud(self, other: 'Cloud') -> bool:
        return isinstance(other, self.__class__)

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: resources_utils.ClusterName,
        region: 'Region',
        zones: Optional[List['Zone']],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Any]:
        """Converts planned sky.Resources to cloud-specific resource variables.

        These variables are used to fill the node type section (instance type,
        any accelerators, etc.) in the cloud's deployment YAML template.

        Cloud-agnostic sections (e.g., commands to run) need not be returned by
        this function.

        Returns:
          A dictionary of cloud-specific node type variables.
        """
        raise NotImplementedError

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        """Returns the #vCPUs and memory that the instance type offers."""
        raise NotImplementedError

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        """Returns {acc: acc_count} held by 'instance_type', if any."""
        raise NotImplementedError

    @classmethod
    def get_arch_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[str]:
        """Returns the arch of the instance type, if any."""
        raise NotImplementedError

    @classmethod
    def get_local_disk_spec_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[str]:
        """Returns the local disk specs from instance type, if any."""
        del instance_type  # unused
        return None

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[
                                      resources_utils.DiskTier] = None,
                                  local_disk: Optional[str] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        """Returns the default instance type with the given #vCPUs, memory,
        disk tier, local disk, region, and zone.

        For example, if cpus='4', this method returns the default instance type
        with 4 vCPUs.  If cpus='4+', this method returns the default instance
        type with 4 or more vCPUs.

        If 'memory=4', this method returns the default instance type with 4GB
        memory.  If 'memory=4+', this method returns the default instance
        type with 4GB or more memory.

        If disk_tier=DiskTier.MEDIUM, this method returns the default instance
        type that support medium disk tier.

        If local_disk='nvme:300+', this method returns the default instance
        type that supports NVMe compatible 300GB+ on-instance storage. This is
        different from disk_tier in that local disks are directly attached to
        underlying VMs.

        When cpus is None, memory is None or disk_tier is None, this method will
        never return None. This method may return None if the cloud's default
        instance family does not have a VM with the given number of vCPUs
        (e.g., when cpus='7') or does not have a VM with the give disk tier
        (e.g. Azure, disk_tier='high').
        """
        raise NotImplementedError

    @classmethod
    def is_image_tag_valid(cls, image_tag: str, region: Optional[str]) -> bool:
        """Validates that the image tag is valid for this cloud."""
        return catalog.is_image_tag_valid(image_tag,
                                          region,
                                          clouds=cls._REPR.lower())

    @classmethod
    def is_label_valid(cls, label_key: str,
                       label_value: str) -> Tuple[bool, Optional[str]]:
        """Validates that the label key and value are valid for this cloud.

        Labels can be implemented in different ways across clouds. For example,
        on AWS we use instance tags, on GCP we use labels, and on Kubernetes we
        use labels. This method should be implemented to validate the label
        format for the cloud.

        Returns:
            A tuple of a boolean indicating whether the label is valid and an
            optional string describing the reason if the label is invalid.
        """
        # If a cloud does not support labels, they are ignored. Only clouds
        # that support labels implement this method.
        del label_key, label_value
        return True, None

    @classmethod
    def is_volume_name_valid(cls,
                             volume_name: str) -> Tuple[bool, Optional[str]]:
        """Validates that the volume name is valid for this cloud.

        Returns:
            A tuple of a boolean indicating whether the volume name is valid
            and an optional string describing the reason if the volume name
            is invalid.
        """
        # If a cloud does not support volume, they are ignored. Only clouds
        # that support volume implement this method.
        del volume_name
        return True, None

    @timeline.event
    def get_feasible_launchable_resources(
            self,
            resources: 'resources_lib.Resources',
            num_nodes: int = 1) -> 'resources_utils.FeasibleResources':
        """Returns FeasibleResources for the given resources.

        Feasible resources refer to an offering respecting the resource
        requirements.  Currently, this function implements "filtering" the
        cloud's offerings only w.r.t. accelerators constraints.

        Launchable resources require a cloud and an instance type be assigned.

        The returned dataclass object FeasibleResources contains three fields:

        - resources_list: a list of resources that are feasible to launch
        - fuzzy_candidate_list: a list of resources that loosely match requested
            resources. E.g., when A100:1 GPU is requested but is not available
            in a cloud/region, the fuzzy candidates are results of a fuzzy
            search in the catalog that are offered in the location. E.g.,
            ['A100-80GB:1', 'A100-80GB:2', 'A100-80GB:4', 'A100:8']
        - hint: an optional string hint if no feasible resources are found.
        """
        if resources.is_launchable():
            self._check_instance_type_accelerators_combination(resources)
        resources_required_features = resources.get_required_cloud_features()
        if num_nodes > 1:
            resources_required_features.add(
                CloudImplementationFeatures.MULTI_NODE)

        try:
            self.check_features_are_supported(resources,
                                              resources_required_features)
        except exceptions.NotSupportedError as e:
            # TODO(zhwu): The resources are now silently filtered out. We
            # should have some logging telling the user why the resources
            # are not considered.
            # UPDATE(kyuds): passing in NotSupportedError reason string
            # to hint for issue #5344. Did not remove above comment as
            # reason is not displayed when other resources are valid.
            return resources_utils.FeasibleResources(resources_list=[],
                                                     fuzzy_candidate_list=[],
                                                     hint=str(e))
        return self._get_feasible_launchable_resources(resources)

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """See get_feasible_launchable_resources()."""
        # TODO: Currently only the Kubernetes implementation of this method
        #  returns hints when no feasible resources are found. This should be
        #  implemented for all clouds.
        raise NotImplementedError

    def get_reservations_available_resources(
        self,
        instance_type: str,
        region: str,
        zone: Optional[str],
        specific_reservations: Set[str],
    ) -> Dict[str, int]:
        """"
        Returns the number of available resources per reservation for the given
        instance type in the given region/zone.
        Default implementation returns 0 for non-implemented clouds.
        """
        del instance_type, region, zone
        return {reservation: 0 for reservation in specific_reservations}

    @classmethod
    def check_credentials(
        cls, cloud_capability: CloudCapability
    ) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to this cloud.

        Returns a boolean of whether the user can access this cloud, and:
          - For SSH and Kubernetes, a dictionary that maps context names to
            the status of the context.
          - For others, a string describing the reason if cannot access.

        Raises NotSupportedError if the capability is
        not supported by this cloud.
        """
        if cloud_capability == CloudCapability.COMPUTE:
            return cls._check_compute_credentials()
        elif cloud_capability == CloudCapability.STORAGE:
            return cls._check_storage_credentials()
        assert_never(cloud_capability)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to
        this cloud's compute service."""
        raise exceptions.NotSupportedError(
            f'{cls._REPR} does not support {CloudCapability.COMPUTE.value}.')

    @classmethod
    def _check_storage_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to
        this cloud's storage service."""
        raise exceptions.NotSupportedError(
            f'{cls._REPR} does not support {CloudCapability.STORAGE.value}.')

    @classmethod
    def expand_infras(cls) -> List[str]:
        """Returns a list of enabled infrastructures for this cloud.

        For Kubernetes and SSH, return a list of resource pools.
        For all other clouds, return self.
        """
        return [cls.canonical_name()]

    # TODO(zhwu): Make the return type immutable.
    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        """(Advanced) Returns all available user identities of this cloud.

        The user "identity" is associated with each SkyPilot cluster they
        create. This is used in protecting cluster operations, such as
        provision, teardown and status refreshing, in a multi-identity
        scenario, where the same user/device can switch between different
        cloud identities. We check that the user identity matches before:
            - Provisioning/starting a cluster
            - Stopping/tearing down a cluster
            - Refreshing the status of a cluster

        Design choices:
          1. We allow the operations that can correctly work with a different
             user identity, as a user should have full control over all their
             clusters (no matter which identity it belongs to), e.g.,
             submitting jobs, viewing logs, auto-stopping, etc.
          2. A cloud implementation can optionally switch between different
             identities if required for cluster operations. In this case,
             the cloud implementation should return multiple identities
             as a list. E.g., our Kubernetes implementation can use multiple
             kubeconfig contexts to switch between different identities.

        The choice of what constitutes an identity is up to each cloud's
        implementation. In general, to suffice for the above purposes,
        ensure that different identities should imply different sets of
        resources are used when the user invoked each cloud's default
        CLI/API.

        An identity is a list of strings. The list is in the order of
        strictness, i.e., the first element is the most strict identity, and
        the last element is the least strict identity.
        When performing an identity check between the current active identity
        and the owner identity associated with a cluster, we compare the two
        lists in order: if a position does not match, we go to the next. To
        see an example, see the docstring of the AWS.get_user_identities.

        Example identities (see cloud implementations):
            - AWS: [UserId, AccountId]
            - GCP: [email address + project ID]
            - Azure: [email address + subscription ID]
            - Kubernetes: [context name]

        Example return values:
            - AWS: [[UserId, AccountId]]
            - GCP: [[email address + project ID]]
            - Azure: [[email address + subscription ID]]
            - Kubernetes: [[current active context], [context 2], ...]

        Returns:
            None if the cloud does not have a concept of user identity
            (access protection will be disabled for these clusters);
            otherwise a list of available identities with the current active
            identity being the first element. Most clouds have only one identity
            available, so the returned list will only have one element: the
            current active identity.

        Raises:
            exceptions.CloudUserIdentityError: If the user identity cannot be
                retrieved.
        """
        return None

    @classmethod
    def get_active_user_identity_str(cls) -> Optional[str]:
        """Returns a user friendly representation of the active identity."""
        user_identity = cls.get_active_user_identity()
        if user_identity is None:
            return None
        return ', '.join(user_identity)

    @classmethod
    def get_active_user_identity(cls) -> Optional[List[str]]:
        """Returns currently active user identity of this cloud

        See get_user_identities for definition of user identity.

        Returns:
            None if the cloud does not have a concept of user identity;
            otherwise the current active identity.
        """
        identities = cls.get_user_identities()
        return identities[0] if identities is not None else None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns the files necessary to access this cloud.

        Returns a dictionary that will be added to a task's file mounts.
        """
        raise NotImplementedError

    def can_credential_expire(self) -> bool:
        """Returns whether the cloud credential can expire."""
        return False

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        """Check the image size from the cloud.

        Returns: the image size in GB.
        Raises: ValueError if the image cannot be found.
        """
        raise NotImplementedError

    def instance_type_exists(self, instance_type):
        """Returns whether the instance type exists for this cloud."""
        raise NotImplementedError

    def validate_region_zone(
            self, region: Optional[str],
            zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """Validates whether region and zone exist in the catalog.

        Returns:
            A tuple of region and zone, if validated.

        Raises:
            ValueError: If region or zone is invalid or not supported.
        """
        return catalog.validate_region_zone(region,
                                            zone,
                                            clouds=self._REPR.lower())

    def need_cleanup_after_preemption_or_failure(
            self, resources: 'resources_lib.Resources') -> bool:
        """Whether a resource needs cleanup after preemption or failure.

        In most cases, spot resources do not need cleanup after preemption,
        as long as the cluster can be relaunched with the same name and tag,
        no matter the preemption behavior is to terminate or stop the cluster.
        Similar for on-demand resources that go into maintenance mode. The
        only exception by far is GCP's TPU VM. We override this method in
        gcp.py.
        """
        del resources
        return False

    @classmethod
    def check_features_are_supported(
        cls,
        resources: 'resources_lib.Resources',
        requested_features: Set[CloudImplementationFeatures],
        region: Optional[str] = None,
    ) -> None:
        """Errors out if the cloud does not support all requested features.

        For instance, Lambda Cloud does not support stop, so
        Lambda.check_features_are_supported(to_provision, {
            CloudImplementationFeatures.STOP
        }) raises the exception.

        Resources are also passed as some features may depend on the resources
        requested. For example, some clouds support stopping normal instances,
        but not spot instances, e.g., AWS; or, GCP supports stopping TPU VMs but
        not TPU VM pods.

        Raises:
            exceptions.NotSupportedError: If the cloud does not support all the
            requested features.
        """
        unsupported_features2reason = cls._unsupported_features_for_resources(
            resources, region)

        # Docker image is not compatible with ssh proxy command.
        if skypilot_config.get_effective_region_config(
                cloud=str(cls).lower(),
                region=None,
                keys=('ssh_proxy_command',),
                default_value=None) is not None:
            unsupported_features2reason.update({
                CloudImplementationFeatures.DOCKER_IMAGE: (
                    f'Docker image is currently not supported on {cls._REPR} '
                    'when proxy command is set. Please remove proxy command in '
                    'the config.'),
            })

        unsupported_features = set(unsupported_features2reason.keys())
        unsupported_features = requested_features.intersection(
            unsupported_features)
        if unsupported_features:
            table = log_utils.create_table(['Feature', 'Reason'])
            for feature in unsupported_features:
                table.add_row(
                    [feature.value, unsupported_features2reason[feature]])
            with ux_utils.print_exception_no_traceback():
                raise exceptions.NotSupportedError(
                    f'The following features are not supported by {cls._REPR}:'
                    '\n\t' + table.get_string().replace('\n', '\n\t'))

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[CloudImplementationFeatures, str]:
        """The features not supported based on the resources provided.

        This method is used by check_features_are_supported() to check if the
        cloud implementation supports all the requested features.

        Returns:
            A dict of {feature: reason} for the features not supported by the
            cloud implementation.
        """
        del resources, region
        raise NotImplementedError

    @classmethod
    def check_disk_tier_enabled(cls, instance_type: Optional[str],
                                disk_tier: resources_utils.DiskTier) -> None:
        """Errors out if the disk tier is not supported by the cloud provider.

        Raises:
            exceptions.NotSupportedError: If the disk tier is not supported.
        """
        del instance_type  # unused
        if disk_tier not in cls._SUPPORTED_DISK_TIERS:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.NotSupportedError(
                    f'{disk_tier} is not supported by {cls._REPR}.')

    @classmethod
    def check_network_tier_enabled(
            cls, instance_type: Optional[str],
            network_tier: resources_utils.NetworkTier) -> None:
        """Errors out if the network tier is not supported by the
        cloud provider.

        For BEST tier: always succeeds, will use best available tier.

        Raises:
            exceptions.NotSupportedError: If the network tier is not supported.
        """
        del instance_type  # unused

        # For other tiers, check if supported
        if network_tier not in cls._SUPPORTED_NETWORK_TIERS:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.NotSupportedError(
                    f'{network_tier} is not supported by {cls._REPR}.')

    @classmethod
    def _translate_disk_tier(
        cls, disk_tier: Optional[resources_utils.DiskTier]
    ) -> resources_utils.DiskTier:
        if disk_tier is None:
            return cls._DEFAULT_DISK_TIER
        if disk_tier == resources_utils.DiskTier.BEST:
            return cls._BEST_DISK_TIER
        return disk_tier

    @classmethod
    def _check_instance_type_accelerators_combination(
            cls, resources: 'resources_lib.Resources') -> None:
        """Errors out if the accelerator is not supported by the instance type.

        This function is overridden by GCP for host-accelerator logic.

        Raises:
            ResourcesMismatchError: If the accelerator is not supported.
        """
        resources = resources.assert_launchable()

        def _equal_accelerators(
            acc_requested: Optional[Dict[str, Union[int, float]]],
            acc_from_instance_type: Optional[Dict[str, Union[int,
                                                             float]]]) -> bool:
            """Check the requested accelerators equals to the instance type

            Check the requested accelerators equals to the accelerators
            from the instance type (both the accelerator type and the
            count).
            """
            if acc_requested is None:
                return acc_from_instance_type is None
            if acc_from_instance_type is None:
                return False

            for requested_acc in acc_requested:
                for instance_acc in acc_from_instance_type:
                    # The requested accelerator can be canonicalized based on
                    # the accelerator registry, which may not has the same case
                    # as the cloud's catalog, e.g., 'RTXPro6000' in Shadeform
                    # catalog, and 'RTXPRO6000' in RunPod catalog.
                    if requested_acc.lower() == instance_acc.lower():
                        # Found the requested accelerator in the instance type.
                        break
                else:
                    # Requested accelerator not found in instance type.
                    return False
                # Avoid float point precision issue.
                if not math.isclose(acc_requested[requested_acc],
                                    acc_from_instance_type[instance_acc]):
                    return False
            return True

        acc_from_instance_type = cls.get_accelerators_from_instance_type(
            resources.instance_type)
        if not _equal_accelerators(resources.accelerators,
                                   acc_from_instance_type):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesMismatchError(
                    'Infeasible resource demands found:'
                    '\n  Instance type requested: '
                    f'{resources.instance_type}\n'
                    f'  Accelerators for {resources.instance_type}: '
                    f'{acc_from_instance_type}\n'
                    f'  Accelerators requested: {resources.accelerators}\n'
                    f'To fix: either only specify instance_type, or '
                    'change the accelerators field to be consistent.')

    @classmethod
    def check_quota_available(cls,
                              resources: 'resources_lib.Resources') -> bool:
        """Check if quota is available based on `resources`.

        The _retry_zones function in cloud_vm_ray_backend goes through different
        candidate regions and attempts to provision the requested
        `instance_type` or `accelerator` accelerators in the `region`
        (the `instance_type` or `accelerator`, and `region`, as defined in
        `resources`) until a successful provisioning happens or all regions
        with the requested accelerator have been looked at. Previously,
        SkyPilot would attempt to provision resources in all of these regions.
        However, many regions would have a zero quota or inadequate quota,
        meaning these attempted provisions were destined to fail from
        the get-go.

        Checking the quota is substantially faster than attempting a failed
        provision (~1 second vs 30+ seconds) so this function attempts to
        check the resource quota and return False if it is found to be zero,
        or True otherwise. If False is returned, _retry_zones will not attempt
        a provision in the region, saving time.

        We are only checking for a nonzero quota, instead of also factoring in
        quota utilization because many cloud providers' APIs don't have a
        built-in command for checking the real-time utilization. Checking
        real-time utilization is a more difficult endeavor that involves
        observability etc., so we are holding off on that for now.

        If for at any point the function fails, whether it's because we can't
        import the necessary dependencies or a query using a cloud provider's
        API fails, we will return True, because we cannot conclusively say the
        relevant quota is zero in these cases, and we don't want to
        preemptively exclude regions from an attempted provision if they may
        have an adequate quota.

        Design choice: We chose a just-in-time approach where
        check_quota_available is called immediately before a potential
        attempted provision, rather than checking all region quotas
        beforehand, storing them, and using those values on-demand. This is
        because, for example, _retry_zones may only need to go through one or
        a few regions before a successful region, and running a query to check
        *every* region's quota beforehand would cause an unnecessary delay.

        Returns:
            False if the quota is found to be zero, and true otherwise.
        """
        del resources  # unused

        return True

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List['status_lib.ClusterStatus']:
        """Queries the latest status of the cluster from the cloud.

        The global_user_state caches the status of the clusters, but the
        actual status of the clusters may change on the cloud, e.g., the
        autostop happens, or the user manually stops the cluster. This
        method queries the cloud to get the latest cluster status.

        Returns:
            A list of ClusterStatus representing the status of all the
            alive nodes in the cluster.

        Raises:
            exceptions.ClusterStatusFetchingError: raised if the status of the
                cluster cannot be fetched.
        """
        raise NotImplementedError

    # === Image related methods ===
    # These three methods are used to create, move and delete images. They
    # are currently only used in `sky launch --clone-disk-from` to clone a
    # cluster's disk to launch a new cluster.
    # It is not required to implement these methods for clouds that do not
    # support `--clone-disk-from`. If not implemented,
    # CloudImplementationFeatures.CLONE_DISK_FROM should be added to the
    # cloud._cloud_unsupported_features().

    @classmethod
    def create_image_from_cluster(cls,
                                  cluster_name: resources_utils.ClusterName,
                                  region: Optional[str],
                                  zone: Optional[str]) -> str:
        """Creates an image from the cluster.

        Returns: the image ID.
        """
        raise NotImplementedError

    @classmethod
    def maybe_move_image(cls, image_id: str, source_region: str,
                         target_region: str, source_zone: Optional[str],
                         target_zone: Optional[str]) -> str:
        """Move an image if required.

        If the image cannot be accessed in the target region, move the image
        from the source region to the target region.

        Returns: the image ID in the target region.
        """
        raise NotImplementedError

    @classmethod
    def delete_image(cls, image_id: str, region: Optional[str]) -> None:
        """Deletes the image with image_id in the region."""
        raise NotImplementedError

    # === End of image related methods ===

    @classmethod
    def canonical_name(cls) -> str:
        return cls.__name__.lower()

    @classmethod
    def display_name(cls) -> str:
        """Name of the cloud used in messages displayed to the user."""
        return cls.canonical_name()

    # === Misc Failovers ===

    @classmethod
    def yield_cloud_specific_failover_overrides(cls,
                                                region: Optional[str] = None
                                               ) -> Iterable[Dict[str, Any]]:
        """Some clouds may have configurations that require them to have
        non-region/zone failovers. This method yields override keys for the
        cluster config. Refer to the implementation for AWS for an example."""
        del region  # unused
        yield {}
        return

    # === End of Misc Failovers ===

    def __repr__(self):
        return self._REPR

    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop('PROVISIONER_VERSION', None)
        state.pop('STATUS_VERSION', None)
        return state


class DummyCloud(Cloud):
    """A dummy Cloud that has zero egress cost from/to for optimization
    purpose."""
    pass


# === Helper functions ===
def cloud_in_iterable(cloud: Cloud, cloud_list: Iterable[Cloud]) -> bool:
    """Returns whether the cloud is in the given cloud list."""
    return any(cloud.is_same_cloud(c) for c in cloud_list)
