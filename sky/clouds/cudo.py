"""Cudo Compute"""
import subprocess
import typing
from typing import Dict, Iterator, List, Optional, Tuple, Union

from sky import catalog
from sky import clouds
from sky.adaptors import common
from sky.utils import common_utils
from sky.utils import registry
from sky.utils import resources_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib
    from sky.utils import volume as volume_lib

_CREDENTIAL_FILES = [
    # credential files for Cudo,
    'cudo.yml'
]


def _run_output(cmd):
    proc = subprocess.run(cmd,
                          shell=True,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('ascii')


@registry.CLOUD_REGISTRY.register
class Cudo(clouds.Cloud):
    """Cudo Compute"""
    _REPR = 'Cudo'

    _INDENT_PREFIX = '    '
    _DEPENDENCY_HINT = (
        'Cudo tools are not installed. Run the following commands:\n'
        f'{_INDENT_PREFIX}  $ pip install cudo-compute')

    _CREDENTIAL_HINT = (
        'Install cudoctl and run the following commands:\n'
        f'{_INDENT_PREFIX}  $ cudoctl init\n'
        f'{_INDENT_PREFIX}For more info: '
        # pylint: disable=line-too-long
        'https://docs.skypilot.co/en/latest/getting-started/installation.html')

    _PROJECT_HINT = (
        'Create a project and then set it as the default project,:\n'
        f'{_INDENT_PREFIX} $ cudoctl projects create my-project-name\n'
        f'{_INDENT_PREFIX} $ cudoctl init\n'
        f'{_INDENT_PREFIX}For more info: '
        # pylint: disable=line-too-long
        'https://docs.skypilot.co/en/latest/getting-started/installation.html')

    _CLOUD_UNSUPPORTED_FEATURES = {
        clouds.CloudImplementationFeatures.STOP: 'Stopping not supported.',
        clouds.CloudImplementationFeatures.SPOT_INSTANCE:
            ('Spot is not supported, as Cudo API does not implement spot.'),
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
            ('Custom disk tier is currently not supported on Cudo Compute'),
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER:
            ('Custom network tier is currently not supported on Cudo Compute'),
        clouds.CloudImplementationFeatures.IMAGE_ID:
            ('Image ID is currently not supported on Cudo. '),
        clouds.CloudImplementationFeatures.DOCKER_IMAGE:
            ('Docker image is currently not supported on Cudo. You can try '
             'running docker command inside the `run` section in task.yaml.'),
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS: (
            'Cudo Compute cannot host a controller as it does not '
            'autostopping, which will leave the controller to run indefinitely.'
        ),
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS:
            ('High availability controllers are not supported on Cudo.'),
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK:
            ('Customized multiple network interfaces are not supported on Cudo.'
            ),
        clouds.CloudImplementationFeatures.LOCAL_DISK:
            (f'Local disk is not supported on {_REPR}')
    }
    _MAX_CLUSTER_NAME_LEN_LIMIT = 60

    PROVISIONER_VERSION = clouds.ProvisionerVersion.SKYPILOT
    STATUS_VERSION = clouds.StatusVersion.SKYPILOT

    _regions: List[clouds.Region] = []

    @classmethod
    def _unsupported_features_for_resources(
        cls,
        resources: 'resources_lib.Resources',
        region: Optional[str] = None,
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
        instance_type,
        accelerators: Optional[Dict[str, int]],
        use_spot: bool,
        region: Optional[str],
        zone: Optional[str],
        resources: Optional['resources_lib.Resources'] = None,
    ) -> List[clouds.Region]:
        assert zone is None, 'Cudo does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []

        regions = catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'cudo')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:

        return catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                        clouds='cudo')

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: Optional[str] = None,
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
        return catalog.get_hourly_cost(instance_type,
                                       use_spot=use_spot,
                                       region=region,
                                       zone=zone,
                                       clouds='cudo')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        return 0.0

    def get_egress_cost(self, num_gigabytes: float) -> float:
        # Change if your cloud has egress cost. (You can do this later;
        # `return 0.0` is a good placeholder.)
        return 0.0

    @classmethod
    def get_default_instance_type(cls,
                                  cpus: Optional[str] = None,
                                  memory: Optional[str] = None,
                                  disk_tier: Optional[
                                      resources_utils.DiskTier] = None,
                                  local_disk: Optional[str] = None,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> Optional[str]:
        return catalog.get_default_instance_type(cpus=cpus,
                                                 memory=memory,
                                                 disk_tier=disk_tier,
                                                 local_disk=local_disk,
                                                 region=region,
                                                 zone=zone,
                                                 clouds='cudo')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, Union[int, float]]]:
        return catalog.get_accelerators_from_instance_type(instance_type,
                                                           clouds='cudo')

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
        volume_mounts: Optional[List['volume_lib.VolumeMount']] = None,
    ) -> Dict[str, Optional[str]]:
        del zones, cluster_name  # unused
        resources = resources.assert_launchable()
        acc_dict = self.get_accelerators_from_instance_type(
            resources.instance_type)
        custom_resources = resources_utils.make_ray_custom_resources_str(
            acc_dict)

        return {
            'instance_type': resources.instance_type,
            'custom_resources': custom_resources,
            'region': region.name,
        }

    def _get_feasible_launchable_resources(
        self, resources: 'resources_lib.Resources'
    ) -> 'resources_utils.FeasibleResources':
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
                    cloud=Cudo(),
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
            default_instance_type = Cudo.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier,
                local_disk=resources.local_disk,
                region=resources.region,
                zone=resources.zone)
            if default_instance_type is None:
                return resources_utils.FeasibleResources([], [], None)
            else:
                return resources_utils.FeasibleResources(
                    _make([default_instance_type]), [], None)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list,
         fuzzy_candidate_list) = catalog.get_instance_type_for_accelerator(
             acc,
             acc_count,
             use_spot=resources.use_spot,
             cpus=resources.cpus,
             memory=resources.memory,
             local_disk=resources.local_disk,
             region=resources.region,
             zone=resources.zone,
             clouds='cudo')
        if instance_list is None:
            return resources_utils.FeasibleResources([], fuzzy_candidate_list,
                                                     None)
        return resources_utils.FeasibleResources(_make(instance_list),
                                                 fuzzy_candidate_list, None)

    @classmethod
    def _check_compute_credentials(
            cls) -> Tuple[bool, Optional[Union[str, Dict[str, str]]]]:
        """Checks if the user has access credentials to
        Cudo's compute service."""
        if not common.can_import_modules(['cudo_compute']):
            return False, (f'{cls._DEPENDENCY_HINT}\n'
                           f'{cls._INDENT_PREFIX}')

        try:
            _run_output('cudoctl --version')
        except (ImportError, subprocess.CalledProcessError) as e:
            return False, (
                f'{cls._CREDENTIAL_HINT}\n'
                f'{cls._INDENT_PREFIX}'
                f'{common_utils.format_exception(e, use_bracket=True)}')
        # pylint: disable=import-outside-toplevel,unused-import
        from cudo_compute import cudo_api
        from cudo_compute.rest import ApiException
        try:
            _, error = cudo_api.make_client()
        except FileNotFoundError as e:
            return False, (
                'Cudo credentials are not set. '
                f'{cls._CREDENTIAL_HINT}\n'
                f'{cls._INDENT_PREFIX}'
                f'{common_utils.format_exception(e, use_bracket=True)}')

        if error is not None:
            return False, (
                f'Application credentials are not set. '
                f'{common_utils.format_exception(error, use_bracket=True)}')

        project_id, error = cudo_api.get_project_id()
        if error is not None:
            return False, (
                f'Default project is not set. '
                f'{cls._PROJECT_HINT}\n'
                f'{cls._INDENT_PREFIX}'
                f'{common_utils.format_exception(error, use_bracket=True)}')
        try:
            api = cudo_api.virtual_machines()
            api.list_vms(project_id)
            return True, None
        except ApiException as e:
            return False, (
                f'Error calling API '
                f'{common_utils.format_exception(e, use_bracket=True)}')

    def get_credential_file_mounts(self) -> Dict[str, str]:
        return {
            f'~/.config/cudo/{filename}': f'~/.config/cudo/{filename}'
            for filename in _CREDENTIAL_FILES
        }

    @classmethod
    def get_user_identities(cls) -> Optional[List[List[str]]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return catalog.instance_type_exists(instance_type, 'cudo')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return catalog.validate_region_zone(region, zone, clouds='cudo')
