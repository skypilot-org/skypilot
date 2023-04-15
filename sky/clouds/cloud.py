"""Interfaces: clouds, regions, and zones."""
import collections
import enum
import re
import typing
from typing import Dict, Iterator, List, Optional, Set, Tuple, Type

from sky import exceptions
from sky.clouds import service_catalog
from sky.utils import log_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources


class CloudImplementationFeatures(enum.Enum):
    """Features that might not be implemented for all clouds.

    Used by Cloud.check_features_are_supported().

    Note: If any new feature is added, please check and update
    _cloud_unsupported_features in all clouds to make sure the
    check_features_are_supported() works as expected.
    """
    STOP = 'stop'
    AUTOSTOP = 'autostop'
    MULTI_NODE = 'multi-node'


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


class _CloudRegistry(dict):
    """Registry of clouds."""

    def from_str(self, name: Optional[str]) -> Optional['Cloud']:
        if name is None:
            return None
        if name.lower() not in self:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Cloud {name!r} is not a valid cloud among '
                                 f'{list(self.keys())}')
        return self.get(name.lower())

    def register(self, cloud_cls: Type['Cloud']) -> Type['Cloud']:
        name = cloud_cls.__name__.lower()
        assert name not in self, f'{name} already registered'
        self[name] = cloud_cls()
        return cloud_cls


CLOUD_REGISTRY = _CloudRegistry()


class Cloud:
    """A cloud provider."""

    _REPR = '<Cloud>'
    _DEFAULT_DISK_TIER = 'medium'

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[CloudImplementationFeatures, str]:
        """The features not supported by the cloud implementation.

        This method is used by check_features_are_supported() to check if the
        cloud implementation supports all the requested features.

        Returns:
            A dict of {feature: reason} for the features not supported by the
            cloud implementation.
        """
        raise NotImplementedError

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        """Returns the maximum length limit of a cluster name.

        This method is used by check_cluster_name_is_valid() to check if the
        cluster name is too long.

        None means no limit.
        """
        return None

    #### Regions/Zones ####

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[Region]:
        """Returns the regions that offer the specified resources.

        The order of the regions follow the order of the regions returned by
        service_catalog/common.py#get_region_zones().
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

    def get_egress_cost(self, num_gigabytes):
        """Returns the egress cost.

        TODO: takes into account "per month" accumulation per account.
        """
        raise NotImplementedError

    def is_same_cloud(self, other):
        raise NotImplementedError

    def make_deploy_resources_variables(
        self,
        resources: 'resources.Resources',
        region: 'Region',
        zones: Optional[List['Zone']],
    ) -> Dict[str, Optional[str]]:
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
    ) -> Optional[Dict[str, int]]:
        """Returns {acc: acc_count} held by 'instance_type', if any."""
        raise NotImplementedError

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        """Returns the default instance type with the given #vCPUs, memory and
        disk tier.

        For example, if cpus='4', this method returns the default instance type
        with 4 vCPUs.  If cpus='4+', this method returns the default instance
        type with 4 or more vCPUs.

        If 'memory=4', this method returns the default instance type with 4GB
        memory.  If 'memory=4+', this method returns the default instance
        type with 4GB or more memory.

        If disk_rier='medium', this method returns the default instance type
        that support medium disk tier.

        When cpus is None, memory is None or disk tier is None, this method will
        never return None. This method may return None if the cloud's default
        instance family does not have a VM with the given number of vCPUs
        (e.g., when cpus='7') or does not have a VM with the give disk tier
        (e.g. Azure, disk_tier='high').
        """
        raise NotImplementedError

    @classmethod
    def is_image_tag_valid(cls, image_tag: str, region: Optional[str]) -> bool:
        """Validates that the image tag is valid for this cloud."""
        return service_catalog.is_image_tag_valid(image_tag,
                                                  region,
                                                  clouds=cls._REPR.lower())

    def get_feasible_launchable_resources(self, resources):
        """Returns a list of feasible and launchable resources.

        Feasible resources refer to an offering respecting the resource
        requirements.  Currently, this function implements "filtering" the
        cloud's offerings only w.r.t. accelerators constraints.

        Launchable resources require a cloud and an instance type be assigned.
        """
        raise NotImplementedError

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud.

        Returns a boolean of whether the user can access this cloud, and a
        string describing the reason if the user cannot access.
        """
        raise NotImplementedError

    # TODO(zhwu): Make the return type immutable.
    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        """(Advanced) Returns currently active user identity of this cloud.

        The user "identity" is associated with each SkyPilot cluster they
        creates. This is used in protecting cluster operations, such as
        provision, teardown and status refreshing, in a multi-identity
        scenario, where the same user/device can switch between different
        cloud identities. We check that the user identity matches before:
            - Provisioning/starting a cluster
            - Stopping/tearing down a cluster
            - Refreshing the status of a cluster

        Design choice: we allow the operations that can correctly work with
        a different user identity, as a user should have full control over
        all their clusters (no matter which identity it belongs to), e.g.,
        submitting jobs, viewing logs, auto-stopping, etc.

        The choice of what constitutes an identity is up to each cloud's
        implementation. In general, to suffice for the above purposes,
        ensure that different identities should imply different sets of
        resources are used when the user invoked each cloud's default
        CLI/API.

        The returned identity is a list of strings. The list is in the order of
        strictness, i.e., the first element is the most strict identity, and
        the last element is the least strict identity.
        When performing an identity check between the current active identity
        and the owner identity associated with a cluster, we compare the two
        lists in order: if a position does not match, we go to the next. To
        see an example, see the docstring of the AWS.get_current_user_identity.


        Example identities (see cloud implementations):
            - AWS: [UserId, AccountId]
            - GCP: [email address + project ID]
            - Azure: [email address + subscription ID]

        Returns:
            None if the cloud does not have a concept of user identity
            (access protection will be disabled for these clusters);
            otherwise the currently active user identity.
        Raises:
            exceptions.CloudUserIdentityError: If the user identity cannot be
                retrieved.
        """
        return None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns the files necessary to access this cloud.

        Returns a dictionary that will be added to a task's file mounts.
        """
        raise NotImplementedError

    def get_image_size(self, image_id: str, region: Optional[str]) -> float:
        """Check the image size from the cloud.

        Returns: the image size in GB.
        Raises: ValueError if the image cannot be found.
        """
        raise NotImplementedError

    def instance_type_exists(self, instance_type):
        """Returns whether the instance type exists for this cloud."""
        raise NotImplementedError

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        """Validates the region and zone."""
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds=self._REPR.lower())

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        """Returns whether the accelerator is valid in the region or zone."""
        raise NotImplementedError

    def need_cleanup_after_preemption(self,
                                      resource: 'resources.Resources') -> bool:
        """Returns whether a spot resource needs cleanup after preeemption.

        In most cases, spot resources do not need cleanup after preemption,
        as long as the cluster can be relaunched with the same name and tag,
        no matter the preemption behavior is to terminate or stop the cluster.
        The only exception by far is GCP's Spot TPU VM. We override this method
        in gcp.py.
        """
        del resource
        return False

    @classmethod
    def check_features_are_supported(
            cls, requested_features: Set[CloudImplementationFeatures]) -> None:
        """Errors out if the cloud does not support all requested features.

        For instance, Lambda Cloud does not support autostop, so
        Lambda.check_features_are_supported({
            CloudImplementationFeatures.AUTOSTOP
        }) raises the exception.

        Raises:
            exceptions.NotSupportedError: If the cloud does not support all the
            requested features.
        """
        unsupported_features2reason = cls._cloud_unsupported_features()
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
    def check_cluster_name_is_valid(cls, cluster_name: str) -> None:
        """Errors out on invalid cluster names not supported by cloud providers.

        Bans (including but not limited to) names that:
        - are digits-only
        - contain underscore (_)

        Raises:
            exceptions.InvalidClusterNameError: If the cluster name is invalid.
        """
        if cluster_name is None:
            return
        max_cluster_name_len_limit = cls._max_cluster_name_length()
        valid_regex = '[a-z]([-a-z0-9]*[a-z0-9])?'
        if re.fullmatch(valid_regex, cluster_name) is None:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.InvalidClusterNameError(
                    f'Cluster name "{cluster_name}" is invalid; '
                    'ensure it is fully matched by regex (e.g., '
                    'only contains lower letters, numbers and dash): '
                    f'{valid_regex}')
        if (max_cluster_name_len_limit is not None and
                len(cluster_name) > max_cluster_name_len_limit):
            cloud_name = '' if cls is Cloud else f' on {cls._REPR}'
            with ux_utils.print_exception_no_traceback():
                raise exceptions.InvalidClusterNameError(
                    f'Cluster name {cluster_name!r} has {len(cluster_name)} '
                    'chars; maximum length is '
                    f'{max_cluster_name_len_limit} chars{cloud_name}.')

    @classmethod
    def check_disk_tier_enabled(cls, instance_type: str,
                                disk_tier: str) -> None:
        """Errors out if the disk tier is not supported by the cloud provider.

        Raises:
            exceptions.NotSupportedError: If the disk tier is not supported.
        """
        raise NotImplementedError

    def __repr__(self):
        return self._REPR
