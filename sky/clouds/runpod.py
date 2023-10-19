""" RunPod Cloud. """

import json
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import status_lib
from sky.clouds import service_catalog
import sky.skylet.providers.runpod.rp_helper as runpod_api

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib  # Renaming to avoid shadowing variables.

_CREDENTIAL_FILES = [
    'credentials.toml',
]


@clouds.CLOUD_REGISTRY.register
class RunPod(clouds.Cloud):
    """ RunPod GPU Cloud

    _REPR | The string representation for the RunPod GPU cloud object.
    """
    _REPR = 'RunPod'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.AUTOSTOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.MULTI_NODE: 'Multi-node unsupported.', # pylint: disable=line-too-long
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _regions: List[clouds.Region] = []

    @classmethod
    def _cloud_unsupported_features(
        cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        assert zone is None, 'RunPod does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'runpod')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(
            instance_type, clouds='runpod')

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
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
            assert r.zones is None, r
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
                                               clouds='runpod')

    def accelerators_to_hourly_cost(
            self,
            accelerators: Dict[str, int],
            use_spot: bool,
            region: Optional[str] = None,
            zone: Optional[str] = None
    ) -> float:
        '''Returns the hourly cost of the accelerators, in dollars/hour.'''
        del accelerators, use_spot, region, zone  # unused
        return 0.0  # RunPod includes accelerators in the hourly cost.

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return 'RunPod'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, RunPod)

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[str] = None) -> Optional[str]:
        """Returns the default instance type for RunPod."""
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='runpod')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
                    instance_type, clouds='runpod')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str, region: 'clouds.Region',
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        del zones

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'):
        """Returns a list of feasible resources for the given resources."""
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
                    cloud=RunPod(),
                    instance_type=instance_type,
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type
            default_instance_type = RunPod.get_default_instance_type(
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
            clouds='runpod')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """
        Verify that the user has valid credentials for RunPod.
        """
        try:
            import runpod
            valid, error = runpod.check_credentials()

            if not valid:
                return False, (
                    f'{error} \n'  # First line is indented by 4 spaces
                    '    Credentials can be set up by running: \n'
                    f'        $ pip install runpod \n'
                    f'        $ runpod store_api_key <YOUR_RUNPOD_API_KEY> \n'
                    '    For more information, see https://docs.runpod.io/docs/skypilot'
                )

            return True, None

        except ImportError:
            return False, (
                "Failed to import runpod."
                "'To install, run: 'pip install runpod' or 'pip install sky[runpod]' "
            )

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.runpod/{filename}': f'~/.runpod/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    def get_current_user_identity(self, cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'runpod')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(
                    region, zone, clouds='runpod')

    def accelerator_in_region_or_zone(
        self,
        accelerator: str,
        acc_count: int,
        region: Optional[str] = None,
        zone: Optional[str] = None
    ) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'runpod')

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        del tag_filters, region, zone, kwargs  # Unused.

        status_map = {
            'CREATED': status_lib.ClusterStatus.INIT,
            'RUNNING': status_lib.ClusterStatus.UP,
            'RESTARTING': status_lib.ClusterStatus.INIT,
            'EXITED': None,
            'PAUSED': status_lib.ClusterStatus.STOPPED,
            'DEAD': status_lib.ClusterStatus.INIT,
            'TERMINATED': None
        }
        status_list = []
        vms = runpod_api.list_instances()
        for instance_id, instance_property in vms.items():
            del instance_id
            if instance_property['name'] == name:
                node_status = status_map[instance_property['status']]
                if node_status is not None:
                    status_list.append(node_status)
        return status_list
