"""Lambda Cloud."""
import json
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.clouds import service_catalog
from sky.skylet.providers.lambda_cloud import lambda_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

# Minimum set of files under ~/.lambda_cloud that grant Lambda Cloud access.
_CREDENTIAL_FILES = [
    'lambda_keys',
]


@clouds.CLOUD_REGISTRY.register
class Lambda(clouds.Cloud):
    """Lambda Labs GPU Cloud."""

    _REPR = 'Lambda'

    # Lamdba has a 64 char limit for cluster name.
    # Reference: https://cloud.lambdalabs.com/api/v1/docs#operation/launchInstance # pylint: disable=line-too-long
    _MAX_CLUSTER_NAME_LEN_LIMIT = 64
    # Currently, none of clouds.CloudImplementationFeatures are implemented
    # for Lambda Cloud.
    # STOP/AUTOSTOP: The Lambda cloud provider does not support stopping VMs.
    # MULTI_NODE: Multi-node is not supported by the implementation yet.
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Lambda cloud does not support stopping VMs.',
        clouds.CloudImplementationFeatures.AUTOSTOP: 'Lambda cloud does not support stopping VMs.',
        clouds.CloudImplementationFeatures.MULTI_NODE: 'Multi-node is not supported by the Lambda Cloud implementation yet.',
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
                clouds.Region('us-east-1'),
                clouds.Region('us-west-2'),
                clouds.Region('us-west-1'),
                clouds.Region('us-south-1'),

                # Everyone else
                clouds.Region('asia-northeast-1'),
                clouds.Region('asia-northeast-2'),
                clouds.Region('asia-south-1'),
                clouds.Region('australia-southeast-1'),
                clouds.Region('europe-central-1'),
                clouds.Region('europe-south-1'),
                clouds.Region('me-west-1'),
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
                instance_type, use_spot, 'lambda')

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
                                               clouds='lambda')

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
        return 'Lambda'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Lambda)

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         clouds='lambda')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='lambda')

    @classmethod
    def get_vcpus_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[float]:
        return service_catalog.get_vcpus_from_instance_type(instance_type,
                                                            clouds='lambda')

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

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
        }

    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources'):
        if resources.use_spot:
            return ([], [])
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Accelerators are part of the instance type in Lambda Cloud
            resources = resources.copy(accelerators=None)
            return ([resources], [])

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Lambda(),
                    instance_type=instance_type,
                    # Setting this to None as Lambda doesn't separately bill /
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
            default_instance_type = Lambda.get_default_instance_type(
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
            clouds='lambda')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        try:
            lambda_utils.LambdaCloudClient().list_instances()
        except (AssertionError, KeyError, lambda_utils.LambdaCloudError):
            return False, ('Failed to access Lambda Cloud with credentials. '
                           'To configure credentials, go to:\n    '
                           '  https://cloud.lambdalabs.com/api-keys\n    '
                           'to generate API key and add the line\n    '
                           '  api_key = [YOUR API KEY]\n    '
                           'to ~/.lambda_cloud/lambda_keys')
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.lambda_cloud/{filename}': f'~/.lambda_cloud/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    def get_current_user_identity(self) -> Optional[str]:
        # TODO(ewzeng): Implement get_current_user_identity for Lambda
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'lambda')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='lambda')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'lambda')
