"""SCP Cloud."""
import json
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.clouds import service_catalog
from sky.skylet.providers.scp import scp_utils
from sky import exceptions
if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

# Minimum set of files under ~/.lambda_cloud that grant Lambda Cloud access.
_CREDENTIAL_FILES = [
    'scp_credential',
]


@clouds.CLOUD_REGISTRY.register
class SCP(clouds.Cloud):
    """SCP Labs GPU Cloud."""

    _REPR = 'SCP'

    # Lamdba has a 64 char limit for cluster name.
    # Reference: https://cloud.lambdalabs.com/api/v1/docs#operation/launchInstance # pylint: disable=line-too-long
    _MAX_CLUSTER_NAME_LEN_LIMIT = 64
    # Currently, none of clouds.CloudImplementationFeatures are implemented
    # for SCP Cloud.
    # STOP/AUTOSTOP: The SCP cloud provider does not support stopping VMs.
    # MULTI_NODE: Multi-node is not supported by the implementation yet.
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'SCP cloud does not support stopping VMs.',
        clouds.CloudImplementationFeatures.AUTOSTOP: 'SCP cloud does not support stopping VMs.',
        clouds.CloudImplementationFeatures.MULTI_NODE: 'Multi-node is not supported by the SCP Cloud implementation yet.',
    }

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
            cls._regions = [
                # Popular US regions
                clouds.Region('KOREA-WEST-2-SCP-B001'),
                clouds.Region('KOREA-EAST-1-SCP-B001'),
                clouds.Region('KOREA-EAST-1-CCN-FIN-B001'),
            ]
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
                instance_type, use_spot, 'scp')

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
                                               clouds='scp')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # Lambda includes accelerators as part of the instance type.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return 'SCP'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, SCP)

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None) -> Optional[str]:
        return 's1v1m2'
        # return service_catalog.get_default_instance_type(cpus=cpus,
        #                                                  clouds='scp')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='scp')

    @classmethod
    def get_vcpus_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[float]:


        return service_catalog.get_vcpus_from_instance_type(instance_type,
                                                            clouds='scp')

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
        image_id = self._get_image_id(r.image_id, region.name, r.instance_type)
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'image_id': image_id,
        }

    @classmethod
    def _get_image_id(
            cls,
            image_id: Optional[Dict[Optional[str], str]],
            region_name: str,
            instance_type: str,
    ) -> str:
        if image_id is None:
            return cls._get_default_ami(region_name, instance_type)
        if None in image_id:
            image_id_str = image_id[None]
        else:
            assert region_name in image_id, image_id
            image_id_str = image_id[region_name]
        if image_id_str.startswith('skypilot:'):
            image_id_str = service_catalog.get_image_id_from_tag(image_id_str,
                                                                 region_name,
                                                                 clouds='scp')
            if image_id_str is None:
                # Raise ResourcesUnavailableError to make sure the failover
                # in CloudVMRayBackend will be correctly triggered.
                # TODO(zhwu): This is a information leakage to the cloud
                # implementor, we need to find a better way to handle this.
                raise exceptions.ResourcesUnavailableError(
                    f'No image found for region {region_name}')
        return image_id_str


    @classmethod
    def _get_default_ami(cls, region_name: str, instance_type: str) -> str:
        acc = cls.get_accelerators_from_instance_type(instance_type)
        image_id = service_catalog.get_image_id_from_tag(
            'skypilot:gpu-ubuntu-2004', region_name, clouds='scp')
        if acc is not None:
            assert len(acc) == 1, acc
            acc_name = list(acc.keys())[0]
            if acc_name == 'K80':
                image_id = service_catalog.get_image_id_from_tag(
                    'skypilot:k80-ubuntu-2004', region_name, clouds='scp')
        if image_id is not None:
            return image_id
        # Raise ResourcesUnavailableError to make sure the failover in
        # CloudVMRayBackend will be correctly triggered.
        # TODO(zhwu): This is a information leakage to the cloud implementor,
        # we need to find a better way to handle this.
        raise exceptions.ResourcesUnavailableError(
            'No image found in catalog for region '
            f'{region_name}. Try setting a valid image_id.')



    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources'):
        if resources.use_spot:
            return ([], [])
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Accelerators are part of the instance type in SCP Cloud
            resources = resources.copy(accelerators=None)
            return ([resources], [])

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=SCP(),
                    instance_type=instance_type,
                    # Setting this to None as SCP doesn't separately bill /
                    # attach the accelerators.  Billed as part of the VM type.
                    accelerators=None,
                    cpus=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            default_instance_type = SCP.get_default_instance_type(
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
            clouds='scp')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        # return True, None
        try:
            scp_utils.SCPClient().list_instances()
        except (AssertionError, KeyError, scp_utils.SCPError):
            return False, ('Failed to access SCP with credentials. '
                           'To configure credentials, go to:\n    '
                           '  https://cloud.samsungsds.com/openapiguide\n    '
                           'to generate API key and add the line\n    '
                           '  api_key = [YOUR API KEY]\n    '
                           'to ~/.scp/scp_credential')
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.scp/{filename}': f'~/.scp/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    def get_current_user_identity(self) -> Optional[str]:
        # TODO(ewzeng): Implement get_current_user_identity for SCP
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'scp')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='scp')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'scp')
