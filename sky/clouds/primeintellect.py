""" Prime Intellect Cloud. """
import json
import os
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

CredentialCheckResult = Tuple[bool, Optional[Union[str, Dict[str, str]]]]

# The lane's local credential store (the API-skill manual's): the env var
# PRIME_INTELLECT_API_KEY is the task secret channel's spelling; this file
# (mode 600, key material only) is the local fallback.
_CREDENTIAL_FILES = [
    'credentials',
]


@registry.CLOUD_REGISTRY.register
class PrimeIntellect(clouds.Cloud):
    """Prime Intellect GPU Cloud"""

    # `_REPR` IS THE CATALOG NAME (sky.catalog.primeintellect_catalog is
    # resolved through it). Upstream set it correctly; kept verbatim so
    # the catalog never regresses to the ABC's `<Cloud>` placeholder.
    _REPR = 'PrimeIntellect'

    _MAX_CLUSTER_NAME_LEN_LIMIT = 120

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP:
            ('there is no stop endpoint on this API — DELETE is the only '
             'teardown and nothing on the pod\'s disk survives it — so stop '
             'is refused rather than silently destroy.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('the lane rents on-demand offers only (spot exists upstream '
             'but is interruptible and unpriced for our identities).'),
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node clusters are not supported on this lane.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Offers ship fixed included disks; disk tier is not '
             'selectable.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Network tier is not selectable on Prime Intellect.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Object storage mounting is not supported on Prime '
             'Intellect (persistent network disks are a separate product, '
             'not SkyPilot storage).'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS:
            ('Host controllers are not supported on Prime Intellect.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on Prime '
             'Intellect.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            ('Disk cloning is not supported on the lane (DELETE teardown '
             'does not preserve the pod\'s disk).'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            ('Images are per-offer platform templates (ubuntu_22_cuda_12, '
             '...); an arbitrary image id cannot be requested.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            ('VM-class offers boot the OS directly; docker images are the '
             'container-class upstreams\' product, not this lane\'s.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Custom multi-network is not supported on Prime Intellect.'),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            ('Local disk is not selectable on Prime Intellect.'),
    }
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    _regions: List[clouds.Region] = []

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources, region  # unused
        return cls._CLOUD_UNSUPPORTED_FEATURES

    def __repr__(self):
        return 'PrimeIntellect'

    # -- catalog-backed lookups -------------------------------------------

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources_lib.Resources'] = None,
    ) -> List[clouds.Region]:
        """Returns the regions that offer the specified resources."""
        del accelerators, resources
        assert zone is None, 'Prime Intellect does not support zones.'
        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'primeintellect')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Returns the #vCPUs and memory that the instance type offers."""
        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='primeintellect')

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Optional[List['clouds.Zone']]]:
        """Returns an iterator over zones for provisioning.

        One yield PER region that has an offering (the base contract): a
        single yield for "any region" turned a first-region capacity
        failure into the end of provisioning on the Spheron lane
        (urun-sh/skypilot#5/#6). Zoneless here — the dataCenter token IS
        the region — so every yielded zones value is None.
        """
        del num_nodes, accelerators, use_spot
        regions = cls.regions_with_offering(instance_type,
                                            None,
                                            False,
                                            region=region,
                                            zone=None,
                                            resources=None)
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        """Returns the cost, or the cheapest cost among all zones for spot."""
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='primeintellect')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        """Returns cost, cheapest cost among all zones for spot."""
        del accelerators, use_spot, region, zone  # Unused.
        # An offer's hourly price is for the whole box (GPUs included);
        # there is no separate per-GPU line item to add.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        del num_gigabytes
        # The broker publishes no egress schedule; nothing to price here.
        # Revisit if overage billing ever appears.
        return 0.0

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        return isinstance(other, PrimeIntellect)

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None,
    ) -> Optional[str]:
        """Returns the default instance type for Prime Intellect."""
        return catalog.get_default_instance_type(
            cpus=cpus,
            memory=memory,
            disk_tier=disk_tier,
            local_disk=local_disk,
            region=region,
            zone=zone,
            use_spot=use_spot,
            max_hourly_cost=max_hourly_cost,
            clouds='primeintellect')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(
            instance_type, clouds='primeintellect')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: resources_utils.ClusterName,
        region: 'clouds.Region',
        zones: Optional[List['clouds.Zone']],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None
    ) -> Dict[str, Optional[str]]:
        del cluster_name, dryrun, num_nodes, volume_mounts
        assert zones is None, 'Prime Intellect does not support zones.'

        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        # `region.name` is the dataCenter token (the same token the
        # catalog row's Region column carries and the create body's
        # dataCenterId takes); the provisioner re-resolves the LIVE offer
        # for the InstanceType token inside it.
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
        """Returns a list of feasible resources for the given resources."""
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=PrimeIntellect(),
                    instance_type=instance_type,
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            default_instance_type = PrimeIntellect.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                local_disk=resources.local_disk,
                use_spot=resources.use_spot,
                max_hourly_cost=resources.max_hourly_cost)
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            return resources_utils.FeasibleResources(
                _make([default_instance_type]), [], None)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list,
         fuzzy_candidate_list) = catalog.get_instance_type_for_accelerator(
             acc,
             acc_count,
             use_spot=resources.use_spot,
             cpus=resources.cpus,
             local_disk=resources.local_disk,
             region=resources.region,
             zone=resources.zone,
             max_hourly_cost=resources.max_hourly_cost,
             clouds='primeintellect')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    # -- identity / credentials -------------------------------------------

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        return None

    @classmethod
    def get_current_user_identity_str(cls) -> Optional[str]:
        return None

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        return None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns a dict of credential file paths to mount paths."""
        return {
            f'~/.prime-intellect/{filename}': f'~/.prime-intellect/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Verify we can talk to Prime Intellect (GET /user/whoami)."""
        # pylint: disable=import-outside-toplevel
        from sky.adaptors import primeintellect as api

        key = os.environ.get(api.API_KEY_ENV, '').strip()
        if not key:
            path = os.path.expanduser(api.API_KEY_FILE)
            if os.path.exists(path):
                with open(path, encoding='utf-8') as handle:
                    key = handle.read().strip()
        if not key:
            return False, (
                f'{api.API_KEY_ENV} is not set and {api.API_KEY_FILE} does '
                'not exist. Create a key at '
                'https://app.primeintellect.ai/dashboard/tokens (the lane '
                'needs Instances Read and write + Availability Read).')
        try:
            api.PrimeIntellectClient(key).get_whoami()
        except api.PrimeintellectError as exc:
            return False, str(exc)
        return True, None

    @classmethod
    def check_credentials(
            cls, cloud_capability: clouds.CloudCapability
    ) -> Tuple[bool, Optional[str]]:
        """Check Prime Intellect credentials for the requested capability.

        MUST accept ``cloud_capability``: ``sky check`` calls this with
        the capability positionally, so a no-arg override raises
        TypeError, the cloud is reported DISABLED, and every launch fails
        with "Task requires primeintellect which is not enabled" — with
        nothing pointing at the real cause (the bug urun-sh/skypilot#4
        fixed for Spheron; the same fix QuantaCloud carries).
        """
        if cloud_capability == clouds.CloudCapability.COMPUTE:
            return cls._check_compute_credentials()
        return False, (
            f'Prime Intellect does not support {cloud_capability.value}.')

    # -- provisioning ------------------------------------------------------

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'primeintellect')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region,
                                            zone,
                                            clouds='primeintellect')

    @classmethod
    def query_status(
        cls,
        name: str,
        tag_filters: Dict[str, str],
        region: Optional[str],
        zone: Optional[str],
        **kwargs,
    ) -> List[Any]:
        # STATUS_VERSION is SKYPILOT, so the provisioner's query_instances
        # is the authority and this path is not used.
        raise NotImplementedError(
            'Prime Intellect uses StatusVersion.SKYPILOT; status comes '
            'from sky.provision.primeintellect.query_instances.')

    @classmethod
    def get_image_size(cls, image_id: str, region: Optional[str]) -> float:
        del image_id, region
        # Images are provider-side platform templates; their size is not
        # exposed. 0.0 lets every image through (same policy as the
        # Vast/Latitude/QuantaCloud clouds).
        return 0.0
