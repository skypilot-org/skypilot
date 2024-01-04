"""Cudo Compute"""
import json
import subprocess
import typing
from typing import Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import status_lib
from sky.clouds import service_catalog
from sky.utils import common_utils

if typing.TYPE_CHECKING:
    # Renaming to avoid shadowing variables.
    from sky import resources as resources_lib

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


@clouds.CLOUD_REGISTRY.register
class Cudo(clouds.Cloud):
    """Cudo Compute"""
    _REPR = 'cudo'

    _INDENT_PREFIX = '    '
    _DEPENDENCY_HINT = (
        'Cudo tools are not installed. Run the following commands:\n'
        f'{_INDENT_PREFIX}  $ pip install cudo-compute')

    _CREDENTIAL_HINT = (
        'Install cudoctl and run the following commands:\n'
        f'{_INDENT_PREFIX}  $ cudoctl init\n'
        f'{_INDENT_PREFIX}For more info: '
        # pylint: disable=line-too-long
        'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html'
    )

    _MAX_CLUSTER_NAME_LEN_LIMIT = 60

    _regions: List[clouds.Region] = []

    @classmethod
    def _unsupported_features_for_resources(
        cls, resources: 'resources_lib.Resources'
    ) -> Dict[clouds.CloudImplementationFeatures, str]:
        features = {
            clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
                (f'Migrating disk is currently not supported on {cls._REPR}.'),
            clouds.CloudImplementationFeatures.DOCKER_IMAGE:
                (f'Docker image is currently not supported on {cls._REPR}. '
                 'You can try running docker command inside the '
                 '`run` section in task.yaml.'),
            clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
                (f'Custom disk tier is currently not supported on {cls._REPR}.'
                ),
            clouds.CloudImplementationFeatures.OPEN_PORTS:
                (f'Opening ports is currently not supported on {cls._REPR}.'),
        }
        if resources.use_spot:
            features[clouds.CloudImplementationFeatures.STOP] = (
                'Stopping spot instances is currently not supported on'
                f' {cls._REPR}.')
        return features

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions(cls) -> List[clouds.Region]:
        if not cls._regions:
            # Add the region from catalog entry
            cls._regions = [
                clouds.Region(...),
            ]
        return cls._regions

    @classmethod
    def regions_with_offering(cls, instance_type: Optional[str],
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        assert zone is None, 'Cudo does not support zones.'
        del accelerators, zone  # unused
        if use_spot:
            return []
        if instance_type is None:
            # Fall back to default regions
            regions = cls.regions()
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'cudo')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        return regions

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:

        return service_catalog.get_vcpus_mem_from_instance_type(instance_type,
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
        return service_catalog.get_hourly_cost(instance_type,
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

    def __repr__(self):
        return 'cudo'

    def is_same_cloud(self, other: clouds.Cloud) -> bool:
        # Returns true if the two clouds are the same cloud type.
        return isinstance(other, Cudo)

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         clouds='cudo')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='cudo')

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def make_deploy_resources_variables(
            self, resources: 'resources_lib.Resources',
            cluster_name_on_cloud: str, region: clouds.Region,
            zones: Optional[List['clouds.Zone']]) -> Dict[str, Optional[str]]:
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

    def _get_feasible_launchable_resources(
            self, resources: 'resources_lib.Resources'):
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
            clouds='cudo')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:

        try:
            # pylint: disable=import-outside-toplevel,unused-import
            from cudo_compute import cudo_api
        except (ImportError, subprocess.CalledProcessError) as e:
            return False, (
                f'{cls._DEPENDENCY_HINT}\n'
                f'{common_utils.format_exception(e, use_bracket=True)}')

        try:
            _run_output('cudoctl --version')
        except (ImportError, subprocess.CalledProcessError) as e:
            return False, (
                f'{cls._CREDENTIAL_HINT}\n'
                f'{common_utils.format_exception(e, use_bracket=True)}')
        # pylint: disable=import-outside-toplevel,unused-import
        from cudo_compute import cudo_api
        from cudo_compute.rest import ApiException
        _, error = cudo_api.client()

        if error is not None:
            return False, (
                f'Application credentials are not set. '
                f'{common_utils.format_exception(error, use_bracket=True)}')

        project_id, error = cudo_api.get_project_id()
        if error is not None:
            return False, (
                f'Error getting project '
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
    def get_current_user_identity(cls) -> Optional[List[str]]:
        # NOTE: used for very advanced SkyPilot functionality
        # Can implement later if desired
        return None

    def instance_type_exists(self, instance_type: str) -> bool:
        return service_catalog.instance_type_exists(instance_type, 'cudo')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        return service_catalog.validate_region_zone(region, zone, clouds='cudo')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'cudo')

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List[status_lib.ClusterStatus]:
        del tag_filters, region, zone, kwargs  # Unused.

        # pylint: disable=import-outside-toplevel,unused-import
        import sky.skylet.providers.cudo.cudo_wrapper as cudo_wrapper
        status_map = {
            'init': status_lib.ClusterStatus.INIT,
            'pend': status_lib.ClusterStatus.INIT,
            'prol': status_lib.ClusterStatus.INIT,
            'boot': status_lib.ClusterStatus.INIT,
            'runn': status_lib.ClusterStatus.UP,
            'stop': status_lib.ClusterStatus.STOPPED,
            'susp': status_lib.ClusterStatus.STOPPED,
            'done': status_lib.ClusterStatus.STOPPED,
            'poff': status_lib.ClusterStatus.STOPPED,
        }
        status_list = []
        vms = cudo_wrapper.list_instances()
        for node in vms:
            if vms[node]['name'] == name:
                node_status = status_map[node['status']]
                if node_status is not None:
                    status_list.append(node_status)
        return status_list
