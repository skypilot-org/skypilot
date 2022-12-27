"""Lambda."""
import json
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky.clouds import service_catalog
from sky.skylet.providers.lambda_labs.lambda_utils import LambdaLabsClient, LambdaLabsError

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

# Minimum set of files under ~/.lambda_labs that grant Lambda Labs access.
_CREDENTIAL_FILES = [
    'lambda_keys',
]


@clouds.CLOUD_REGISTRY.register
class Lambda(clouds.Cloud):
    """Lambda Labs GPU Cloud."""

    @classmethod
    def regions(cls):
        cls._regions = [
            clouds.Region('us-east-1'),
            clouds.Region('us-west-2'),
            clouds.Region('australia-southeast-1'),
            clouds.Region('europe-central-1'),
            clouds.Region('asia-south-1'),
            clouds.Region('me-west-1'),
            clouds.Region('europe-south-1'),
            clouds.Region('asia-northeast-1'),
            clouds.Region('asia-northeast-2'),
            clouds.Region('us-west-1'),
            clouds.Region('us-south-1'),
        ]
        return cls._regions

    @classmethod
    def region_zones_provision_loop(
        cls,
        *,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool,
    ) -> Iterator[Tuple[clouds.Region, List[clouds.Zone]]]:
        del instance_type
        del use_spot
        del accelerators  # unused

        for region in cls.regions():
            yield region, region.zones

    def instance_type_to_hourly_cost(self, instance_type: str,
                                     use_spot: bool) -> float:
        return service_catalog.get_hourly_cost(instance_type,
                                               region=None,
                                               use_spot=use_spot,
                                               clouds='lambda')

    def accelerators_to_hourly_cost(self, accelerators,
                                    use_spot: bool) -> float:
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
    def get_default_instance_type(cls) -> str:
        return 'gpu_1x_a100_sxm4'

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
    ) -> float:
        return service_catalog.get_vcpus_from_instance_type(instance_type,
                                                            clouds='lambda')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            region: Optional['clouds.Region'],
            zones: Optional[List['clouds.Zone']]) -> Dict[str, str]:
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

    def get_feasible_launchable_resources(self,
                                          resources: 'resources_lib.Resources'):
        # TODO: In some cases, launching a larger VM will be cheaper than
        # launching a smaller VM with the exact requirements on another cloud.
        # This is not implemented yet.
        fuzzy_candidate_list = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            # Treat Resources(AWS, p3.2x, V100) as Resources(AWS, p3.2x).
            resources = resources.copy(accelerators=None)
            return ([resources], fuzzy_candidate_list)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Lambda(),
                    instance_type=instance_type,
                    # Setting this to None as Lambda doesn't separately bill /
                    # attach the accelerators.  Billed as part of the VM type.
                    accelerators=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # No requirements to filter, so just return a default VM type.
            return (_make([Lambda.get_default_instance_type()]),
                    fuzzy_candidate_list)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list
        ) = service_catalog.get_instance_type_for_accelerator(acc,
                                                              acc_count,
                                                              clouds='lambda')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        try:
            LambdaLabsClient().ls()
        except (AssertionError, LambdaLabsError):
            return False, ('Failed to access Lambda Labs with credentials. '
                           'To configure credentials, go to:\n    '
                           '  https://cloud.lambdalabs.com/api-keys\n    '
                           'to generate API key and add the line\n    '
                           '  api_key=[YOUR API KEY]\n    '
                           'to ~/.lambda_labs/lambda_keys')
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.lambda_labs/{filename}': f'~/.lambda_labs/{filename}'
            for filename in _CREDENTIAL_FILES
        }

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
