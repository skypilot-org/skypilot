""" Paperspace Cloud. """

import json
import typing
from typing import Dict, Iterator, List, Optional, Tuple

import requests

from sky import clouds
from sky.clouds import service_catalog
from sky.provision.paperspace import utils
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

_CREDENTIAL_FILES = [
    # credential files for Paperspace,
    'config.json',
]


@clouds.CLOUD_REGISTRY.register
class Paperspace(clouds.Cloud):
    """Paperspace GPU Cloud"""

    _REPR = 'Paperspace'
    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            'Migrating '
            f'disk is not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            'Spot instances are'
            f' not supported in {_REPR}.',
        clouds.CloudImplementationFeatures.IMAGE_ID:
            'Specifying image ID '
            f'is not supported for {_REPR}.',
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            'Custom disk tiers'
            f' is not supported in {_REPR}.',
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 120
    _regions: List[clouds.Region] = []

    # Using the latest SkyPilot provisioner API to provision and check status.
    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

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
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(
        cls,
        instance_type: str,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
    ) -> List[clouds.Region]:
        assert zone is None, 'Paperspace does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'paperspace')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(
            instance_type, clouds='paperspace')

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

    def instance_type_to_hourly_cost(
        self,
        instance_type: str,
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        return service_catalog.get_hourly_cost(
            instance_type,
            use_spot=use_spot,
            region=region,
            zone=zone,
            clouds='paperspace',
        )

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        """Returns the hourly cost of the accelerators, in dollars/hour."""
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        return 0.0

    def __repr__(self):
        return self._REPR

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Paperspace)

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
    ) -> Optional[str]:
        """Returns the default instance type for Paperspace."""
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='paperspace')

    @classmethod
    def get_accelerators_from_instance_type(
            cls, instance_type: str) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='paperspace')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self,
            resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str,
            region: 'clouds.Region',
            zones: Optional[List['clouds.Zone']],
            dryrun: bool = False) -> Dict[str, Optional[str]]:
        del zones, dryrun

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
                    cloud=Paperspace(),
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
            default_instance_type = Paperspace.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier)
            if default_instance_type is None:
                return ([], [])
            else:
                return (_make([default_instance_type]), [])

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list) = (
            service_catalog.get_instance_type_for_accelerator(
                acc,
                acc_count,
                use_spot=resources.use_spot,
                cpus=resources.cpus,
                memory=resources.memory,
                region=resources.region,
                zone=resources.zone,
                clouds='paperspace',
            ))
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Verify that the user has valid credentials for Paperspace."""
        try:
            # attempt to make a CURL request for listing instances
            utils.PaperspaceCloudClient().list_instances()
        except (AssertionError, KeyError, utils.PaperspaceCloudError) as e:
            # pylint: disable=line-too-long
            return False, (
                'Failed to access Paperspace Cloud with credentials.\n    '
                'To configure credentials, follow the instructions at: '
                'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#paperspace\n    '
                'Generate API key and create a json at `~/.paperspace/config.json` with \n     '
                '    {"apiKey": "[YOUR API KEY]"}\n    '
                f'Reason: {str(e)}')
        except requests.exceptions.ConnectionError:
            return False, ('Failed to verify Paperspace Cloud credentials. '
                           'Check your network connection '
                           'and try again.')
        except Exception as e:  # pylint: disable=broad-except
            return False, str(e)

        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.paperspace/{filename}': f'~/.paperspace/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    @classmethod
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'paperspace')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds='paperspace')
