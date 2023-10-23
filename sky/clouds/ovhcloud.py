import json
from os import environ, path
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import status_lib
from sky.clouds import service_catalog
from sky.skylet.providers.ovhcloud.ovhai_client import OVHCloudClient

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib


@clouds.CLOUD_REGISTRY.register
class OVHCloud(clouds.Cloud):

    _REPR = 'OVHCloud'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.SPOT_INSTANCE: 'OVHCloud does not support spot VMs.',
        clouds.CloudImplementationFeatures.MULTI_NODE: 'OVHCloud does not support multi nodes.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER: 'OVHCloud does not support custom disk tiers',
    }

    _MAX_CLUSTER_NAME_LEN_LIMIT = 50

    _regions: List[clouds.Region] = []

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
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
            cls._regions = [
                clouds.Region(...),
            ]
        return cls._regions

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators  # unused
        if use_spot:
            return []
        if instance_type is None:
            # Fall back to default regions
            regions = cls.regions()
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'ovhcloud')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        if zone is not None:
            for r in regions:
                assert r.zones is not None, r
                r.set_zones([z for z in r.zones if z.name == zone])
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
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        for r in regions:
            yield r.zones

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return service_catalog.get_hourly_cost(instance_type,
                                               use_spot=use_spot,
                                               region=region,
                                               zone=zone,
                                               clouds='ovhcloud')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return 'OVHCloud'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, OVHCloud)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         clouds='ovhcloud')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='ovhcloud')

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ):
        return service_catalog.get_vcpus_mem_from_instance_type(
            instance_type, clouds='ovhcloud')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def _get_feasible_launchable_resources(
            self, resources: 'resources_lib.Resources'):
        ready_resources = []
        for resource in resources:
            ready_resources.append(
                resources.copy(
                    cloud=OVHCloud(),
                    instance_type=resource.instance_type,
                    # Setting this to None as Lambda doesn't separately bill /
                    # attach the accelerators.  Billed as part of the VM type.
                    accelerators=None,
                    cpus=None,
                    memory=None,
                ))
        return (ready_resources, [])

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str, region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        target_deploy_site = self._get_default_region()
        if region is not None:
            target_deploy_site = region
        if zones:
            target_deploy_site = zones[0].name

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': target_deploy_site,
            'zones': ','.join([zone.name for zone in zones])
        }

    @classmethod
    def _get_default_region(cls):
        return "GRA11"

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List['status_lib.ClusterStatus']:
        # print("Query status:", name, tag_filters, region, zone, kwargs)
        status_map = {
            'booting': status_lib.ClusterStatus.INIT,
            'active': status_lib.ClusterStatus.UP,
            'unhealthy': status_lib.ClusterStatus.INIT,
            'terminated': None,
        }
        # TODO(ewzeng): filter by hash_filter_string to be safe
        status_list = []
        vms = OVHCloudClient(cls._get_default_region())._get_filtered_nodes(
            tag_filters=tag_filters)
        possible_names = [f'{name}-head', f'{name}-worker']
        for node in vms:
            if node.get('name') in possible_names:
                node_status = status_map[node['status']]
                if node_status is not None:
                    status_list.append(node_status)
        return status_list

    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources',
                                          num_nodes: int = 1):
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
                    cloud=OVHCloud(),
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
            default_instance_type = OVHCloud.get_default_instance_type(
                cpus=resources.cpus)
            if default_instance_type is None:
                return ([], [])
            else:
                return (_make([default_instance_type]), [])

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list
        ) = service_catalog.get_instance_type_for_accelerator(
            acc,
            acc_count,
            use_spot=resources.use_spot,
            cpus=resources.cpus,
            region=resources.region,
            zone=resources.zone,
            clouds='ovhcloud')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    def check_credentials(self):
        creds_file_path = list(self.get_credential_file_mounts().keys())[0]
        creds_file_exists = path.exists(path.expanduser(creds_file_path))
        if creds_file_exists:
            return [creds_file_exists, 'ok']
        return [creds_file_exists, f'Please create a correct credentials file at {creds_file_path}']

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            "~/.config/openstack/clouds.yaml": "~/.config/openstack/clouds.yaml"
        }

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'ovhcloud')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='ovhcloud')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'OVHCloud')
