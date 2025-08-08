"""CoreWeave Cloud."""
import os
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sky import clouds
from sky.adaptors import coreweave
from sky.utils import annotations
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

COREWEAVE_CREDENTIALS_PATH = '~/.aws/credentials'
COREWEAVE_CONFIG_PATH = '~/.aws/config'


def coreweave_profile_in_aws_cred_and_config() -> bool:
    """Checks if CoreWeave Object Storage profile is set in aws credentials
    and profile."""

    credentials_path = os.path.expanduser(COREWEAVE_CREDENTIALS_PATH)
    coreweave_profile_exists_in_credentials = False
    if os.path.isfile(credentials_path):
        with open(credentials_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[{coreweave.COREWEAVE_PROFILE_NAME}]' in line:
                    coreweave_profile_exists_in_credentials = True

    config_path = os.path.expanduser(COREWEAVE_CONFIG_PATH)
    coreweave_profile_exists_in_config = False
    if os.path.isfile(config_path):
        with open(config_path, 'r', encoding='utf-8') as file:
            for line in file:
                if f'[profile {coreweave.COREWEAVE_PROFILE_NAME}]' in line:
                    coreweave_profile_exists_in_config = True

    return (coreweave_profile_exists_in_credentials and
            coreweave_profile_exists_in_config)


@registry.CLOUD_REGISTRY.register
class CoreWeave(clouds.Cloud):
    """CoreWeave Cloud"""
    _REPR = 'CoreWeave'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.AUTODOWN:
            ('Autodown not supported on CoreWeave.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Spot instances not supported on CoreWeave.'),
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            (f'Migrating disk is currently not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            (f'Custom disk tier is currently not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is currently not supported on CoreWeave.'),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on CoreWeave.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported on '
             f'{_REPR}.'),
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 50
    _regions: List[clouds.Region] = []

    # Using the latest SkyPilot provisioner API
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        return cls._CLOUD_UNSUPPORTED_FEATURES.copy()

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        # CoreWeave doesn't support compute instances, only storage
        return []

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        # CoreWeave doesn't support compute instances
        return None, None

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
        # CoreWeave doesn't support compute instances
        return iter([])

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        # CoreWeave doesn't support compute instances
        return 0.0

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        # CoreWeave doesn't support compute instances
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        # CoreWeave doesn't charge for egress (storage-only)
        return 0.0

    def __repr__(self):
        return self._REPR

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        return isinstance(other, CoreWeave)

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[
                                      resources_utils.DiskTier] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        # CoreWeave doesn't support compute instances
        return None

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        # CoreWeave doesn't support compute instances
        return None

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        # CoreWeave doesn't support compute instances
        return None

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        cluster_name: resources_utils.ClusterName,
        region: 'clouds.Region',
        zones: Optional[List['clouds.Zone']],
        num_nodes: int,
        dryrun: bool = False,
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Any]:
        # CoreWeave doesn't support compute instances
        return {}

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> resources_utils.FeasibleResources:
        return resources_utils.FeasibleResources([], [], None)

    @classmethod
    @annotations.lru_cache(scope='request')
    def _check_storage_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        # Check if CoreWeave storage credentials are available
        return coreweave_profile_in_aws_cred_and_config(), None

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        # CoreWeave is storage-only and doesn't support compute instances
        return False, f'{cls._REPR} is does not support compute instances.'

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns the credential file mounts for CoreWeave."""
        file_mounts = {}

        # Also add AWS config if CoreWeave profile exists in both files
        if coreweave_profile_in_aws_cred_and_config():
            credentials_path = COREWEAVE_CREDENTIALS_PATH
            if os.path.exists(os.path.expanduser(credentials_path)):
                file_mounts[credentials_path] = credentials_path

            config_path = COREWEAVE_CONFIG_PATH
            if os.path.exists(os.path.expanduser(config_path)):
                file_mounts[config_path] = config_path

        for path in coreweave.get_credential_file_paths():
            if os.path.exists(os.path.expanduser(path)):
                file_mounts[path] = path

        return file_mounts

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # Not needed for storage-only cloud
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return False

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        # Accept any region/zone for storage
        pass

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        return None
