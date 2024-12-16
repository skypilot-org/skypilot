# NEBIUSTODO The cloud class that handles the metadata of the clouds

""" Nebius Cloud. """
import logging
import os
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from nebius.api.nebius.iam.v1 import ProjectServiceClient, GetProjectRequest

from sky import clouds
from sky.clouds import service_catalog
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

_CREDENTIAL_FILES = [
    # credential files for Nebius,
    # TODO: Change this to the actual credential files
    'NEBIUS_IAM_TOKEN.txt',
]


@clouds.CLOUD_REGISTRY.register
class Nebius(clouds.Cloud):
    """Nebius GPU Cloud"""
    _REPR = 'Nebius'
    _CLOUD_UNSUPPORTED_FEATURES = {
        # clouds.CloudImplementationFeatures.STOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Spot is not supported, as Nebius API does not implement spot .'),
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node not supported yet, as the interconnection among nodes '
             'are non-trivial on Nebius.'),
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _regions: List[clouds.Region] = ['fin']

    # Using the latest SkyPilot provisioner API to provision and check status.
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _unsupported_features_for_resources(
            cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        """The features not supported based on the resources provided.

        This method is used by check_features_are_supported() to check if the
        cloud implementation supports all the requested features.

        Returns:
            A dict of {feature: reason} for the features not supported by the
            cloud implementation.
        """
        del resources  # unused
        return cls._CLOUD_UNSUPPORTED_FEATURES

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        logging.debug('Nebius cloud max cluster name length: %s', cls._REPR)
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        logging.debug('Nebius cloud regions with offering: %s', cls._REPR)
        assert zone is None, 'Nebius does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []
        else:
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
        logging.debug('Nebius cloud get vcpus mem: %s', cls._REPR)
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
        logging.debug('Nebius cloud zone provision loop: %s', cls._REPR)
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
        logging.debug('Nebius cloud instance type to hourly cost: %s', )
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
        logging.debug('Nebius cloud accelerators to hourly cost: %s', 1)
        del accelerators, use_spot, region, zone  # unused
        ########
        # TODO #
        ########
        # This function assumes accelerators are included as part of instance
        # type. If not, you will need to change this. (However, you can do
        # this later; `return 0.0` is a good placeholder.)
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        logging.debug('Nebius cloud get egress cost: %s', 1)
        ########
        # TODO #
        ########
        # Change if your cloud has egress cost. (You can do this later;
        # `return 0.0` is a good placeholder.)
        return 0.0

    def __repr__(self):
        return 'Nebius'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        logging.debug('Nebius cloud is same: %s', self)
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Nebius)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        logging.debug('Nebius cloud default instance type: %s', cls._REPR)
        """Returns the default instance type for Nebius."""
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='nebius')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, int]]:
        logging.debug('Nebius cloud accelerators from instance type: %s', )
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='nebius')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        logging.debug('Nebius cloud get zone shell cmd: %s', )
        return None

    def make_deploy_resources_variables(
            self,
            resources: 'resources_lib.Resources',
            cluster_name: '',
            region: 'clouds.Region',
            zones: Optional[List['clouds.Zone']],
            num_nodes: int,
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        logging.debug('Nebius cloud make deploy resources variables: %s', )
        del zones, dryrun, cluster_name

        return {
            'instance_type': resources.instance_type
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
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """ Verify that the user has valid credentials for Nebius. """
        logging.debug('Nebius cloud check credentials')
        ########
        # TODO #
        ########
        # Verify locally stored credentials are correct.
        # The following is an example, where we assume `nebius` is the
        # Python SDK for Nebius.

        try:
            from nebius.sdk import SDK

            from nebius.base.error import SDKError
            from nebius.aio.service_error import RequestError

            try:
                DEFAULT_NEBIUS_TOKEN_PATH = os.path.expanduser('~/.nebius/NEBIUS_IAM_TOKEN.txt')
                with open(DEFAULT_NEBIUS_TOKEN_PATH, 'r') as file:
                    NEBIUS_IAM_TOKEN, NB_PROJECT_ID= file.read().strip().split('\n')
                sdk = SDK(credentials=NEBIUS_IAM_TOKEN)

            except SDKError as e:
                return False, (
                    f'EMPTY TOKEN \n'  # First line is indented by 4 spaces
                    '    Credentials can be set up by running: \n'
                    f'        $ pip install nebius \n'
                    f'        $ nebius iam get-access-token > ~/.nebius/NEBIUS_IAM_TOKEN.txt \n'
                    f'   Copy your project ID from the web console Project settings and save it to file \n'
                    f'        $ NB_PROJECT_ID >> ~/.nebius/NEBIUS_IAM_TOKEN.txt \n'
                    '    For more information, see https://docs..io/docs/skypilot'  # pylint: disable=line-too-long
                )
            try:
                service = ProjectServiceClient(sdk)
                get = service.get(GetProjectRequest(id=NB_PROJECT_ID)).wait()
                logging.info(f'Project name: {get.metadata.name}')
            except RequestError as e:
                return False, (
                    f'{e.status} \n'  # First line is indented by 4 spaces
                    '    Credentials can be set up by running: \n'
                    f'        $ pip install nebius \n'
                    f'        $ nebius iam get-access-token > ~/.nebius/NEBIUS_IAM_TOKEN.txt  \n'
                    '    For more information, see https://docs..io/docs/skypilot'  # pylint: disable=line-too-long
                )

            return True, None

        except ImportError:
            return False, (
                'Failed to import nebius.'
                'To install, run: "pip install nebius" or "pip install sky[nebius]"'  # pylint: disable=line-too-long
            )

    def get_credential_file_mounts(self) -> Dict[str, str]:
        logging.debug('Nebius cloud get credential file mounts')
        ########
        # TODO #
        ########
        # Return dictionary of credential file paths. This may look
        # something like:
        return {
            f'~/.nebius/{filename}': f'~/.nebius/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        logging.debug('Nebius cloud get current user identity')
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        logging.debug('Nebius cloud instance type exists: %s', instance_type)
        return service_catalog.instance_type_exists(instance_type, 'nebius')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        logging.debug('Nebius cloud validate region zone: %s', zone)
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='nebius')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        logging.debug('Nebius cloud accelerator in region or zone: %s', zone)
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'nebius')
