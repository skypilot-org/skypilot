import json
import os
import sys
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

    _DEFAULT_NUM_VCPUS = 4
    _DEFAULT_MEMORY_CPU_RATIO = 1
    _REPR = 'Kubernetes'
    _regions: List[clouds.Region] = [clouds.Region('kubernetes')]
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Kubernetes does not support stopping VMs.',
        clouds.CloudImplementationFeatures.AUTOSTOP: 'Kubernetes does not support stopping VMs.',
        clouds.CloudImplementationFeatures.MULTI_NODE: 'Multi-node is not supported by the Kubernetes implementation yet.',
    }

    IMAGE = 'us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest'

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        return cls._regions

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        # No notion of regions in Kubernetes - return a single region.
        return cls.regions()

    @classmethod
    def region_zones_provision_loop(
        cls,
        *,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Tuple[clouds.Region, List[clouds.Zone]]]:
        # No notion of regions in Kubernetes - return a single region.
        for region in cls.regions():
            yield region, region.zones

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        # Assume zero cost for Kubernetes clusters
        return 0.0

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
    def get_port(cls, svc_name, namespace):
        from sky.skylet.providers.kubernetes.utils import get_port
        return get_port(svc_name, namespace)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        del disk_tier # Unused.
        virtual_instance_type = ''
        if cpus is not None:
            virtual_instance_type += f'{cpus}vCPU-'
        else:
            virtual_instance_type += f'{cls._DEFAULT_NUM_VCPUS}vCPU'
        if memory is not None:
            virtual_instance_type += f'{memory}GB'
        else:
            virtual_instance_type += f'{cls._DEFAULT_NUM_VCPUS * cls._DEFAULT_MEMORY_CPU_RATIO}GB'
        return virtual_instance_type


    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        # TODO(romilb): Add GPU support.
        return None

    @classmethod
    def get_vcpus_mem_from_instance_type(
            cls, instance_type: str) -> Tuple[Optional[float], Optional[float]]:
        """Returns the #vCPUs and memory that the instance type offers."""
        vcpus = cls.get_vcpus_from_instance_type(instance_type)
        mem = cls.get_mem_from_instance_type(instance_type)
        return vcpus, mem




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
        del num_nodes  # Unused.
        for r in cls.regions():
            yield r.zones

    @classmethod
    def get_vcpus_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[float]:
        """Returns the #vCPUs that the instance type offers."""
        if instance_type is None:
            return None
        # TODO(romilb): Better parsing
        return float(instance_type.split('vCPU')[0])

    @classmethod
    def get_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[float]:
        """Returns the memory that the instance type offers."""
        if instance_type is None:
            return None
        return float(instance_type.split('vCPU-')[1].split('GB')[0])

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

        # resources.memory and resources.cpus are None if they are not explicitly set.
        # We fetch the default values for the instance type in that case.
        cpus, mem = self.get_vcpus_mem_from_instance_type(resources.instance_type)
        # TODO(romilb): Allow fractional resources here
        cpus = int(cpus)
        mem = int(mem)
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'cpus': cpus,
            'memory': mem
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
            # Return a default instance type with the given number of vCPUs.
            default_instance_type = Kubernetes.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier)
            if default_instance_type is None:
                return ([], [])
            else:
                return (_make([default_instance_type]), [])

        assert len(accelerators) == 1, resources
        # TODO(romilb): Add GPU support.
        raise NotImplementedError("GPU support not implemented yet.")

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        # TODO(romilb): Check credential validity using k8s api
        if os.path.exists(os.path.expanduser(f'~/.kube/config')):
            return True, None
        else:
            return False, "Kubeconfig doesn't exist"

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {}
        # TODO(romilb): Fix the file mounts optimization ('config' here clashes with azure config file)
        # return {
        #     f'~/.kube/{filename}': f'~/.kube/{filename}'
        #     for filename in _CREDENTIAL_FILES
        #     if os.path.exists(os.path.expanduser(f'~/.kube/{filename}'))
        # }

    def instance_type_exists(self, instance_type: str) -> bool:
        # TODO(romilb): All instance types are supported for now. In the future
        #  we should check if the instance type is supported by the cluster.
        return True

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        # Kubernetes doesn't have regions or zones, so we don't need to validate
        return region, zone

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        # TODO(romilb): All accelerators are marked as available for now. In the
        #  future, we should return false for accelerators that we know are not
        #  supported by the cluster.
        return True
