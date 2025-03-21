""" SimplePod Cloud. """

import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

import requests

from sky import clouds
from sky.clouds import service_catalog
from sky.provision.simplepod import simplepod_utils
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import status_lib

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

_CREDENTIAL_FILES = [
    'simplepod_keys',
]


@registry.CLOUD_REGISTRY.register
class SimplePod(clouds.Cloud):
    """ SimplePod GPU Cloud """

    _REPR = 'SimplePod'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.MULTI_NODE:
            ('Multi-node not supported yet, as the interconnection among nodes '
             'are non-trivial on SimplePod.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Customizing disk tier is not supported yet on SimplePod.'),
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING:
            ('Mounting object stores is not supported on SimplePod. To read data '
             'from object stores on SimplePod, use `mode: COPY` to copy the data '
             'to local disk.'),
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _regions: List[clouds.Region] = []

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT
    OPEN_PORTS_VERSION = clouds.OpenPortsVersion.LAUNCH_ONLY

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
        assert zone is None, 'SimplePod does not support zones.'
        del accelerators, zone  # unused
        regions = service_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'simplepod')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        return service_catalog.get_hourly_cost(instance_type,
                                               use_spot=use_spot,
                                               region=region,
                                               zone=zone,
                                               clouds='simplepod')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        return 0.0  # SimplePod includes accelerators in the hourly cost

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[resources_utils.DiskTier] = None
    ) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='simplepod')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='simplepod')

    @classmethod
    def _check_credentials(cls) -> Tuple[bool, Optional[str]]:
        try:
            import simplepod  # pylint: disable=import-outside-toplevel
            valid, error = simplepod.check_credentials()

            if not valid:
                return False, (
                    f'{error} \n'
                    '    Credentials can be set up by running: \n'
                    f'        $ pip install simplepod \n'
                    f'        $ simplepod config\n'
                    '    For more information, see https://docs.skypilot.co/getting-started/installation.html#simplepod'
                )
            return True, None

        except ImportError:
            return False, ('Failed to import simplepod. '
                           'To install, run: pip install skypilot[simplepod]')

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.simplepod/{filename}': f'~/.simplepod/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='simplepod')

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to SimplePod's compute service."""
        try:
            simplepod_utils.SimplePodClient().list_instances()
        except (AssertionError, KeyError, simplepod_utils.SimplePodError):
            return False, ('Failed to access SimplePod Cloud with credentials. '
                         'To configure credentials, go to:\n    '
                         '  https://simplepod.ai/ \n    '
                         'to generate API key and add the line\n    '
                         '  api_key = [YOUR API KEY]\n    '
                         'to ~/.simplepod_cloud/simplepod_keys')
        except requests.exceptions.ConnectionError:
            return False, ('Failed to verify SimplePod Cloud credentials. '
                         'Check your network connection '
                         'and try again.')
        return True, None

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        status_map = {
            'booting': status_lib.ClusterStatus.INIT,
            'active': status_lib.ClusterStatus.UP,
            'unhealthy': status_lib.ClusterStatus.INIT,
            'terminating': None,
            'terminated': None,
        }
        status_list = []
        vms = simplepod_utils.SimplePodClient().list_instances()
        possible_names = [f'{name}-head', f'{name}-worker']
        for node in vms:
            if node.get('name') in possible_names:
                node_status = status_map[node['status']]
                if node_status is not None:
                    status_list.append(node_status)
        return status_list
