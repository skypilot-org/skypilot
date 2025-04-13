""" Nebius Cloud. """
import logging
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import clouds
from sky.adaptors import nebius
from sky.clouds import service_catalog
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

_CREDENTIAL_FILES = [
    # credential files for Nebius
    nebius.NEBIUS_TENANT_ID_FILENAME,
    nebius.NEBIUS_IAM_TOKEN_FILENAME,
    nebius.NEBIUS_PROJECT_ID_FILENAME,
    nebius.NEBIUS_CREDENTIALS_FILENAME
]

_INDENT_PREFIX = '    '


def nebius_profile_in_aws_cred_and_config() -> bool:
    """Checks if Nebius Object Storage profile is set in aws credentials
    and profile."""

    credentials_path = os.path.expanduser('~/.aws/credentials')
    nebius_profile_exists_in_credentials = False
    if os.path.isfile(credentials_path):
        with open(credentials_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{nebius.NEBIUS_PROFILE_NAME}]' in line:
                    nebius_profile_exists_in_credentials = True

    config_path = os.path.expanduser('~/.aws/config')
    nebius_profile_exists_in_config = False
    if os.path.isfile(config_path):
        with open(config_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[profile {nebius.NEBIUS_PROFILE_NAME}]' in line:
                    nebius_profile_exists_in_config = True

    return (nebius_profile_exists_in_credentials and
            nebius_profile_exists_in_config)


@registry.CLOUD_REGISTRY.register
class Nebius(clouds.Cloud):
    """Nebius GPU Cloud"""
    _REPR = 'Nebius'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.AUTODOWN:
            ('Autodown not supported. Can\'t delete OS disk.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Spot is not supported, as Nebius API does not implement spot.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            (f'Migrating disk is currently not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            (f'Docker image is currently not supported on {_REPR}. '
             'You can try running docker command inside the '
             '`run` section in task.yaml.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            (f'Custom disk tier is currently not supported on {_REPR}.'),
    }
    # Nebius maximum instance name length defined as <= 63 as a hostname length
    # 63 - 8 - 5 = 50 characters since
    # we add 4 character from UUID to make uniq `-xxxx`
    # our provisioner adds additional `-worker`.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 50
    _regions: List[clouds.Region] = []

    # Using the latest SkyPilot provisioner API to provision and check status.
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        del resources  # unused
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        assert zone is None, 'Nebius does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []
        regions = service_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'nebius')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                                clouds='nebius')

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
                                               clouds='nebius')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        """Returns the hourly cost of the accelerators, in dollars/hour."""
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return self._REPR

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Nebius)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[resources_utils.DiskTier] = None
    ) -> Optional[str]:
        """Returns the default instance type for Nebius."""
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='nebius')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='nebius')

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
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        del dryrun, cluster_name
        assert zones is None, ('Nebius does not support zones', zones)

        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)
        platform, _ = resources.instance_type.split('_')

        if platform in ('cpu-d3', 'cpu-e2'):
            image_family = 'ubuntu22.04-driverless'
        elif platform in ('gpu-h100-sxm', 'gpu-h200-sxm', 'gpu-l40s-a'):
            image_family = 'ubuntu22.04-cuda12'
        else:
            raise RuntimeError('Unsupported instance type for Nebius cloud:'
                               f' {resources.instance_type}')
        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'image_id': image_family,
            # Nebius does not support specific zones.
            'zones': None,
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
                    cloud=Nebius(),
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
            default_instance_type = Nebius.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier)
            if default_instance_type is None:
                # TODO: Add hints to all return values in this method to help
                #  users understand why the resources are not launchable.
                return resources_utils.FeasibleResources([], [], None)
            else:
                return resources_utils.FeasibleResources(
                    _make([default_instance_type]), [], None)

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
            clouds='nebius')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to
        Nebius's compute service."""
        logging.debug('Nebius cloud check credentials')
        token_cred_msg = (
            f'{_INDENT_PREFIX}Credentials can be set up by running: \n'
            f'{_INDENT_PREFIX}  $ nebius iam get-access-token > {nebius.NEBIUS_IAM_TOKEN_PATH} \n'  # pylint: disable=line-too-long
            f'{_INDENT_PREFIX} or generate  ~/.nebius/credentials.json')

        tenant_msg = (f'{_INDENT_PREFIX}Copy your tenat ID from the web console and save it to file \n'  # pylint: disable=line-too-long
                      f'{_INDENT_PREFIX}  $ echo $NEBIUS_TENANT_ID_PATH > {nebius.NEBIUS_TENANT_ID_PATH} \n'  # pylint: disable=line-too-long
                      f'{_INDENT_PREFIX} Or if you have 1 tenant you can run:\n'  # pylint: disable=line-too-long
                      f'{_INDENT_PREFIX}  $ nebius --format json iam whoami|jq -r \'.user_profile.tenants[0].tenant_id\' > {nebius.NEBIUS_TENANT_ID_PATH} \n')  # pylint: disable=line-too-long
        if not nebius.is_token_or_cred_file_exist():
            return False, f'{token_cred_msg}'
        sdk = nebius.sdk()
        tenant_id = nebius.get_tenant_id()
        if tenant_id is None:
            return False, f'{tenant_msg}'
        try:
            service = nebius.iam().ProjectServiceClient(sdk)
            service.list(
                nebius.iam().ListProjectsRequest(parent_id=tenant_id)).wait()
        except nebius.request_error() as e:
            return False, (
                f'{e.status} \n'  # First line is indented by 4 spaces
                f'{token_cred_msg}'
                f'{tenant_msg}')
        return True, None

    @classmethod
    def _check_storage_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to Nebius Object Storage.

        Returns:
            A tuple of a boolean value and a hint message where the bool
            is True when credentials needed for Nebius Object Storage is set.
            It is False when either of those are not set, which would hint
            with a string on unset credential.
        """
        hints = None
        if not nebius_profile_in_aws_cred_and_config():
            hints = (f'[{nebius.NEBIUS_PROFILE_NAME}] profile '
                     'is not set in ~/.aws/credentials.')
        if hints:
            hints += ' Run the following commands:'
            if not nebius_profile_in_aws_cred_and_config():
                hints += (
                    f'\n{_INDENT_PREFIX}  $ pip install boto3'
                    f'\n{_INDENT_PREFIX}  $ aws configure --profile nebius')
            hints += (
                f'\n{_INDENT_PREFIX}For more info: '
                'https://docs.skypilot.co/en/latest/getting-started/installation.html#nebius'  # pylint: disable=line-too-long
            )
        return (False, hints) if hints else (True, hints)

    def get_credential_file_mounts(self) -> Dict[str, str]:
        credential_file_mounts = {
            f'~/.nebius/{filename}': f'~/.nebius/{filename}'
            for filename in _CREDENTIAL_FILES
        }
        credential_file_mounts['~/.aws/credentials'] = '~/.aws/credentials'
        credential_file_mounts['~/.aws/config'] = '~/.aws/config'
        return credential_file_mounts

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'nebius')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='nebius')
