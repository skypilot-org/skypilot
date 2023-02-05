import json
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.clouds import service_catalog

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

_CREDENTIAL_FILES = [
    'config',
]


@clouds.CLOUD_REGISTRY.register
class Kubernetes(clouds.Cloud):

    _REPR = 'Kubernetes'
    _regions: List[clouds.Region] = ['kubernetes']

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        return cls._regions

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators, zone  # unused
        if use_spot:
            return []
        if instance_type is None:
            # Fall back to default regions
            regions = cls.regions()
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'kubernetes')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def region_zones_provision_loop(
        cls,
        *,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Tuple[clouds.Region, List[clouds.Zone]]]:
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=None,
                                            zone=None)
        for region in regions:
            yield region, region.zones

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return service_catalog.get_hourly_cost(instance_type,
                                               use_spot=use_spot,
                                               region=region,
                                               zone=zone,
                                               clouds='kubernetes')

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
        return self._REPR

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        return isinstance(other, Kubernetes)

    @classmethod
    def get_default_instance_type(cls) -> str:
        return 'cpu1'

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='kubernetes')

    @classmethod
    def get_vcpus_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[float]:
        return service_catalog.get_vcpus_from_instance_type(instance_type,
                                                            clouds='kubernetes')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
        del zones
        if region is None:
            region = self._get_default_region()

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        # TODO: return number of CPUs and memory here
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
        }

    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources'):
        if resources.use_spot:
            return ([], [])
        fuzzy_candidate_list: List[str] = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return ([resources], fuzzy_candidate_list)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Kubernetes(),
                    instance_type=instance_type,
                    accelerators=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # No requirements to filter, so just return a default VM type.
            return (_make([Kubernetes.get_default_instance_type()]),
                    fuzzy_candidate_list)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list
        ) = service_catalog.get_instance_type_for_accelerator(
            acc,
            acc_count,
            use_spot=resources.use_spot,
            region=resources.region,
            zone=resources.zone,
            clouds='kubernetes')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        # TODO(romilb): Check credential validity using k8s api
        return (os.path.exists(os.path.expanduser(f'~/.kube/config')),
                              "Kubeconfig doesn't exist")

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.kube/{filename}': f'~/.kube/{filename}'
            for filename in _CREDENTIAL_FILES
            if os.path.exists(os.path.expanduser(f'~/.kube/{filename}'))
        }

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'kubernetes')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='kubernetes')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'kubernetes')
