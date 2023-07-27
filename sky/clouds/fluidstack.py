import json
import typing
import os
from typing import Dict, Iterator, List, Optional, Tuple
from sky.skylet.providers.fluidstack.fluidstack_utils import FluidstackClient
from sky import clouds
from sky.clouds import service_catalog
from sky import status_lib
from sky.skylet.providers.fluidstack.fluidstack_utils import (
    FLUIDSTACK_API_KEY_PATH,
    FLUIDSTACK_API_TOKEN_PATH,
    FluidstackClient,
)

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

_CREDENTIAL_FILES = [
    # credential files for Fluidstack,
    FLUIDSTACK_API_KEY_PATH,
    FLUIDSTACK_API_TOKEN_PATH,
]


@clouds.CLOUD_REGISTRY.register
class Fluidstack(clouds.Cloud):
    _REPR = "Fluidstack"
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: "Fluidstack does not support stopping VMs.",
        clouds.CloudImplementationFeatures.AUTOSTOP: "Fluidstack does not support stopping VMs.",
        clouds.CloudImplementationFeatures.MULTI_NODE: "Multi-node is not supported by the Fluidstack implementation yet.",
    }
    ########
    # TODO #
    ########
    _MAX_CLUSTER_NAME_LEN_LIMIT = 64  # TODO

    _regions: List[clouds.Region] = []

    @classmethod
    def _cloud_unsupported_features(
        cls,
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        if not cls._regions:
            ########
            # TODO #
            ########
            # Add the region from catalog entry
            client = FluidstackClient()
            cls._regions = [clouds.Region(name=key) for key in client.list_regions()]
        return cls._regions

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: Optional[str],
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
    ) -> List[clouds.Region]:
        assert zone is None, "Fluidstack does not support zones."
        del accelerators, zone  # unused
        if use_spot:
            return []
        if instance_type is None:
            # Fall back to default regions
            regions = cls.regions()
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, "fluidstack"
            )

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[None]:
        del num_nodes  # unused
        regions = cls.regions_with_offering(
            instance_type, accelerators, use_spot, region=region, zone=None
        )
        for r in regions:
            assert r.zones is None, r
            yield r.zones

    def instance_type_to_hourly_cost(
        self,
        instance_type: str,
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        return service_catalog.get_hourly_cost(
            instance_type,
            use_spot=use_spot,
            region=region,
            zone=zone,
            clouds="fluidstack",
        )

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        del accelerators, use_spot, region, zone  # unused
        ########
        # TODO #
        ########
        # This function assumes accelerators are included as part of instance
        # type. If not, you will need to change this. (However, you can do
        # this later; `return 0.0` is a good placeholder.)
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        ########
        # TODO #
        ########
        # Change if your cloud has egress cost. (You can do this later;
        # `return 0.0` is a good placeholder.)
        return 0.0

    def __repr__(self):
        return "Fluidstack"

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Fluidstack)

    @classmethod
    def get_default_instance_type(cls, cpus: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus, clouds="fluidstack")

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds="fluidstack"
        )

    @classmethod
    def get_vcpus_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[float]:
        return service_catalog.get_vcpus_from_instance_type(
            instance_type, clouds="fluidstack"
        )

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
        self,
        resources: "resources_lib.Resources",
        region: Optional["clouds.Region"],
        zones: Optional[List["clouds.Zone"]],
    ) -> Dict[str, Optional[str]]:
        del zones
        if region is None:
            region = self._get_default_region()

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(",", ":"))
        else:
            custom_resources = None

        return {
            "instance_type": resources.instance_type,
            "custom_resources": custom_resources,
            "region": region.name,
        }

    def get_feasible_launchable_resources(self, resources: "resources_lib.Resources"):
        if resources.use_spot:
            return ([], [])
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return ([resources], [])

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Fluidstack(),
                    instance_type=instance_type,
                    ########
                    # TODO #
                    ########
                    # Set to None if don't separately bill / attach
                    # accelerators.
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type
            default_instance_type = Fluidstack.get_default_instance_type(
                cpus=resources.cpus
            )
            if default_instance_type is None:
                return ([], [])
            else:
                return (_make([default_instance_type]), [])

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (
            instance_list,
            fuzzy_candidate_list,
        ) = service_catalog.get_instance_type_for_accelerator(
            acc,
            acc_count,
            resources.use_spot,
            resources.cpus,
            region=resources.region,
            zone=resources.zone,
            clouds="fluidstack",
        )
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        ########
        # TODO #
        ########
        # Verify locally stored credentials are correct.
        assert os.path.exists(os.path.expanduser(FLUIDSTACK_API_KEY_PATH))
        assert os.path.exists(os.path.expanduser(FLUIDSTACK_API_TOKEN_PATH))
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        ########
        # TODO #
        ########
        # Return dictionary of credential file paths. This may look
        # something like:
        return {filename: filename for filename in _CREDENTIAL_FILES}

    def get_current_user_identity(self) -> Optional[str]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, "fluidstack")

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region, zone, clouds="fluidstack")

    def accelerator_in_region_or_zone(
        self,
        accelerator: str,
        acc_count: int,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, "fluidstack"
        )

    @classmethod
    def query_status(
        cls,
        name: str,
        tag_filters: Dict[str, str],
        region: Optional[str],
        zone: Optional[str],
        **kwargs,
    ) -> List[status_lib.ClusterStatus]:
        status_map = {
            "Provisioning": status_lib.ClusterStatus.INIT,
            "Starting Up": status_lib.ClusterStatus.INIT,
            "Running": status_lib.ClusterStatus.UP,
            "Error Creating": status_lib.ClusterStatus.INIT,
        }
        # TODO(ewzeng): filter by hash_filter_string to be safe
        status_list = []
        vms = FluidstackClient().list_instances()

        for node in vms:
            node_status = status_map.get(node["status"], None)
            if node_status is not None:
                status_list.append(node_status)
        return status_list
