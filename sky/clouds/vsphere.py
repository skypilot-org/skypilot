"""Vsphere cloud implementation."""
import subprocess
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import clouds
from sky.adaptors import common as adaptors_common
from sky.clouds import service_catalog
from sky.provision.vsphere import vsphere_utils
from sky.provision.vsphere.vsphere_utils import get_vsphere_credentials
from sky.provision.vsphere.vsphere_utils import initialize_vsphere_data
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    import requests

    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib
else:
    requests = adaptors_common.LazyImport('requests')

_CLOUD_VSPHERE = 'vsphere'
_CREDENTIAL_FILES = [
    # credential files for vSphere,
    'credential.yaml',
]


@registry.CLOUD_REGISTRY.register
class Vsphere(clouds.Cloud):
    """Vsphere cloud"""

    _INDENT_PREFIX = '    '
    _REPR = 'vSphere'

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.MULTI_NODE: 'Multi-node is not '
                                                       'supported by the '
                                                       'vSphere implementation '
                                                       'yet.',
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
            (f'Migrating disk is currently not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            (f'Specifying image id is currently not supported on {_REPR}.'),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            (f'Docker image is currently not supported on {_REPR}. '
             'You can try running docker command inside the '
             '`run` section in task.yaml.'),
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            (f'Spot instances are not supported in {_REPR}.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            (f'Custom disk tiers are not supported in {_REPR}.'),
        clouds.CloudImplementationFeatures.OPEN_PORTS:
            (f'Opening ports is currently not supported on {_REPR}.'),
    }

    _MAX_CLUSTER_NAME_LEN_LIMIT = 80  # The name can't exceeds 80 characters
    # for vsphere.

    _regions: List[clouds.Region] = []

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        features = cls._CLOUD_UNSUPPORTED_FEATURES
        return features

    @classmethod
    def max_cluster_name_length(cls) -> Optional[int]:
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
        del accelerators, zone  # unused
        regions = service_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, _CLOUD_VSPHERE)

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,  # pylint: disable=unused-argument
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[List[clouds.Zone]]:
        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        # Vsphere provisioner takes 1 zone per request.
        for r in regions:
            assert r.zones is not None, r
            for zone in r.zones:
                yield [zone]

    def instance_type_to_hourly_cost(
        self,
        instance_type: str,
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        return 0.0

    def accelerators_to_hourly_cost(
        self,
        accelerators: Dict[str, int],
        use_spot: bool,
        region: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> float:
        # Always return 0 for vSphere cases.
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        # vSphere doesn't support this part.
        return 0.0

    def __repr__(self):
        return 'vSphere'

    @classmethod
    def get_default_instance_type(
        cls,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[resources_utils.DiskTier] = None,
    ) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds=_CLOUD_VSPHERE)

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds=_CLOUD_VSPHERE)

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(
            instance_type, clouds=_CLOUD_VSPHERE)

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
        dryrun: bool = False,
    ) -> Dict[str, Optional[str]]:
        # TODO get image id here.
        del cluster_name, dryrun  # unused
        assert zones is not None, (region, zones)
        zone_names = [zone.name for zone in zones]
        r = resources
        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
            'zones': ','.join(zone_names),
        }

    def _get_feasible_launchable_resources(
            self, resources: 'resources_lib.Resources'):
        if resources.use_spot:
            # TODO: Add hints to all return values in this method to help
            #  users understand why the resources are not launchable.
            return resources_utils.FeasibleResources([], [], None)
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return resources_utils.FeasibleResources([resources], [], None)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=Vsphere(),
                    instance_type=instance_type,
                    accelerators=None,
                    cpus=None,
                    memory=None,
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type
            default_instance_type = Vsphere.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
            )
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            else:
                return resources_utils.FeasibleResources(
                    _make([default_instance_type]), [], None)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (
            instance_list,
            fuzzy_candidate_list,
        ) = service_catalog.get_instance_type_for_accelerator(
            acc,
            acc_count,
            use_spot=resources.use_spot,
            cpus=resources.cpus,
            memory=resources.memory,
            region=resources.region,
            zone=resources.zone,
            clouds=_CLOUD_VSPHERE,
        )
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to
        vSphere's compute service."""

        try:
            # pylint: disable=import-outside-toplevel,unused-import
            # Check pyVmomi installation.
            import pyVmomi
        except (ImportError, subprocess.CalledProcessError) as e:
            return False, (
                'vSphere dependencies are not installed. '
                'Run the following commands:'
                f'\n{cls._INDENT_PREFIX}  $ pip install skypilot[vSphere]'
                f'\n{cls._INDENT_PREFIX}Credentials may also need to be set. '
                'For more details. See https://docs.skypilot.co/en/latest/getting-started/installation.html#vmware-vsphere'  # pylint: disable=line-too-long
                f'{common_utils.format_exception(e, use_bracket=True)}')

        required_keys = ['name', 'username', 'password', 'clusters']
        skip_key = 'skip_verification'
        try:
            for vcenter in get_vsphere_credentials():
                for req_key in required_keys:
                    if req_key not in vcenter:
                        return False, (
                            f'{req_key} is a required key in vCenter section '
                            f'and must be present in the YAML file.')

                if skip_key not in vcenter:
                    vcenter[skip_key] = False
                vs_client = vsphere_utils.VsphereClient(
                    vcenter['name'],
                    vcenter['username'],
                    vcenter['password'],
                    vcenter['clusters'],
                    vcenter[skip_key],
                )
                vs_client.check_credential()
                initialize_vsphere_data()
        except requests.exceptions.ConnectionError:
            return False, ('Failed to verify Vsphere credentials. '
                           'Check your network connection '
                           'and try again.')
        except Exception as err:  # pylint: disable=broad-except
            error_message = str(err)
            return False, (error_message)  # TODO: Add url of guide.
        return True, None

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.vsphere/{filename}': f'~/.vsphere/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type,
                                                    _CLOUD_VSPHERE)

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region,
                                                    zone,
                                                    clouds=_CLOUD_VSPHERE)
