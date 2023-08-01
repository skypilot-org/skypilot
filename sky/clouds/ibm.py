"""IBM Web Services."""
import os
import yaml
import json
import typing
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sky import clouds
from sky import sky_logging
from sky import status_lib
from sky.adaptors import ibm
from sky.adaptors.ibm import CREDENTIAL_FILE
from sky.clouds import service_catalog
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    # renaming to avoid shadowing variables
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)


@clouds.CLOUD_REGISTRY.register
class IBM(clouds.Cloud):
    """IBM Web Services."""

    _REPR = 'IBM'
    # IBM's VPC instance name length is limited to 63 chars.
    # since Ray names the instances using the following
    # format: ray-{cluster_name}-{node_type}-{8-char-uuid}
    # it leaves 63-3-5-8=47 characters for the cluster name.
    _MAX_CLUSTER_NAME_LEN_LIMIT = 47
    _regions: List[clouds.Region] = []

    @classmethod
    def _cloud_unsupported_features(
            cls) -> Dict[clouds.CloudImplementationFeatures, str]:
        return {
            clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER:
                (f'Migrating disk is not supported in {cls._REPR}.'),
            clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER:
                (f'Custom disk tier is not supported in {cls._REPR}.'),
        }

    @classmethod
    def _max_cluster_name_length(cls) -> Optional[int]:
        return cls._MAX_CLUSTER_NAME_LEN_LIMIT

    @classmethod
    def regions_with_offering(cls, instance_type: str,
                              accelerators: Optional[Dict[str, int]],
                              use_spot: bool, region: Optional[str],
                              zone: Optional[str]) -> List[clouds.Region]:
        del accelerators  # unused
        if use_spot:
            return []
        regions = service_catalog.get_region_zones_for_instance_type(
            instance_type, use_spot, 'ibm')

        if region is not None:
            regions = [r for r in regions if r.name == region]
        if zone is not None:
            for r in regions:
                assert r.zones is not None, r
                r.set_zones([z for z in r.zones if z.name == zone])
            regions = [r for r in regions if r.zones]
        return regions

    @classmethod
    def zones_provision_loop(
        cls,
        *,
        region: str,
        num_nodes: int,
        instance_type: str,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: bool = False,
    ) -> Iterator[Optional[List[clouds.Zone]]]:
        """Loops over (region, zones) to retry for provisioning.

        returning a single zone list with its region,
        since ibm cloud currently doesn't
        support retries for list of zones.

        Args:
            instance_type: The instance type to provision.
            accelerators: The accelerators to provision.
            use_spot: Whether to use spot instances.
        """
        # del accelerators  # unused
        del num_nodes  # unused
        assert use_spot is False, (
            'current IBM implementation doesn\'t support spot instances')

        regions = cls.regions_with_offering(instance_type,
                                            accelerators,
                                            use_spot,
                                            region=region,
                                            zone=None)
        # IBM provisioner currently takes 1 zone per request.
        for r in regions:
            assert r.zones is not None, r
            for zone in r.zones:
                yield [zone]

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def instance_type_to_hourly_cost(self,
                                     instance_type: str,
                                     use_spot: bool,
                                     region: Optional[str] = None,
                                     zone: Optional[str] = None) -> float:
        # Currently doesn't support spot instances, hence use_spot set to False.
        del use_spot
        return service_catalog.get_hourly_cost(instance_type,
                                               use_spot=False,
                                               region=region,
                                               zone=zone,
                                               clouds='ibm')

    def accelerators_to_hourly_cost(self,
                                    accelerators: Dict[str, int],
                                    use_spot: bool,
                                    region: Optional[str] = None,
                                    zone: Optional[str] = None) -> float:
        del accelerators, use_spot, region, zone  # unused
        # Currently Isn't implemented in the same manner by aws and azure.
        return 0

    def get_egress_cost(self, num_gigabytes):
        """Returns the egress cost. Currently true for us-south, i.e. Dallas.
        based on https://cloud.ibm.com/objectstorage/create#pricing. """
        cost = 0
        price_thresholds = [{
            'threshold': 150,
            'price_per_gb': 0.05
        }, {
            'threshold': 50,
            'price_per_gb': 0.07
        }, {
            'threshold': 0,
            'price_per_gb': 0.09
        }]
        for price_threshold in price_thresholds:
            # pylint: disable=line-too-long
            cost += (num_gigabytes - price_threshold['threshold']
                    ) * price_threshold['price_per_gb']
            num_gigabytes -= (num_gigabytes - price_threshold['threshold'])
        return cost

    def is_same_cloud(self, other):
        return isinstance(other, IBM)

    def make_deploy_resources_variables(
        self,
        resources: 'resources_lib.Resources',
        region: 'clouds.Region',
        zones: Optional[List['clouds.Zone']],
    ) -> Dict[str, Optional[str]]:
        """Converts planned sky.Resources to cloud-specific resource variables.

        These variables are used to fill the node type section (instance type,
        any accelerators, etc.) in the cloud's deployment YAML template.

        Cloud-agnostic sections (e.g., commands to run) need not be returned by
        this function.

        Returns:
          A dictionary of cloud-specific node type variables.
        """

        def _get_profile_resources(instance_profile):
            """returns a dict representing the
             cpu, memory and gpu of specified instance profile"""

            cpu_num, memory_num = self.get_vcpus_mem_from_instance_type(
                instance_profile)
            gpu_num = self.get_accelerators_from_instance_type(instance_profile)
            gpu_num = list(gpu_num.values())[0] if gpu_num else 0
            return {'CPU': cpu_num, 'memory': memory_num, 'GPU': gpu_num}

        region_name = region.name
        # zones is guaranteed to be initialized for
        # clouds implementing 'zones_provision_loop()'
        zone_names = [zone.name for zone in zones]  # type: ignore[union-attr]

        r = resources
        assert not r.use_spot, \
            'IBM does not currently support spot instances in this framework'

        acc_dict = self.get_accelerators_from_instance_type(r.instance_type)
        if acc_dict is not None:
            custom_resources = json.dumps(acc_dict, separators=(',', ':'))
        else:
            custom_resources = None

        instance_resources = _get_profile_resources(r.instance_type)

        worker_instance_type = get_cred_file_field('worker_instance_type',
                                                   r.instance_type)
        worker_instance_resources = _get_profile_resources(worker_instance_type)
        # r.image_id: {clouds.Region:image_id} - property of Resources class
        image_id = r.image_id[
            region.name] if r.image_id else self.get_default_image(region_name)

        return {
            'instance_type': r.instance_type,
            'instance_resources': instance_resources,
            'worker_instance_type': worker_instance_type,
            'worker_instance_resources': worker_instance_resources,
            'custom_resources': custom_resources,
            'use_spot': r.use_spot,
            'region': region_name,
            'zones': ','.join(zone_names),
            'image_id': image_id,
            'iam_api_key': get_cred_file_field('iam_api_key'),
            'resource_group_id': get_cred_file_field('resource_group_id'),
            'disk_capacity': get_cred_file_field('disk_capacity', 100)
        }

    @classmethod
    def get_vcpus_mem_from_instance_type(
        cls,
        instance_type: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        return service_catalog.get_vcpus_mem_from_instance_type(instance_type,
                                                                clouds='ibm')

    @classmethod
    def get_accelerators_from_instance_type(
        cls,
        instance_type: str,
    ) -> Optional[Dict[str, int]]:
        """Returns {acc: acc_count} held by 'instance_type', if any."""
        return service_catalog.get_accelerators_from_instance_type(
            instance_type, clouds='ibm')

    @classmethod
    def get_default_instance_type(
            cls,
            cpus: Optional[str] = None,
            memory: Optional[str] = None,
            disk_tier: Optional[str] = None) -> Optional[str]:
        return service_catalog.get_default_instance_type(cpus=cpus,
                                                         memory=memory,
                                                         disk_tier=disk_tier,
                                                         clouds='ibm')

    def _get_feasible_launchable_resources(
            self, resources: 'resources_lib.Resources'):
        """Returns a list of feasible and launchable resources.

        Feasible resources refer to an offering respecting the resource
        requirements.  Currently, this function implements "filtering" the
        cloud's offerings only w.r.t. accelerators constraints.

        Launchable resources require a cloud and an instance type be assigned.
        """
        fuzzy_candidate_list: Optional[List[str]] = []
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            resources = resources.copy(accelerators=None)
            return ([resources], fuzzy_candidate_list)

        def _make(instance_list):
            resource_list = []
            for instance_type in instance_list:
                r = resources.copy(
                    cloud=IBM(),
                    instance_type=instance_type,
                    # Setting this to None as IBM doesn't separately bill /
                    # attach the accelerators.  Billed as part of the VM type.
                    accelerators=None,
                    cpus=None,
                    memory=None)
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # Return a default instance type with the given number of vCPUs.
            default_instance_type = IBM.get_default_instance_type(
                cpus=resources.cpus,
                memory=resources.memory,
                disk_tier=resources.disk_tier)
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
            cpus=resources.cpus,
            memory=resources.memory,
            region=resources.region,
            zone=resources.zone,
            clouds='ibm')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def get_default_image(cls, region) -> str:
        """
        Returns default image id, currently stock ubuntu 22-04.
        if user specified 'image_id' in ~/.ibm/credentials.yaml
            matching this 'region', returns it instead.
        """

        def _get_image_objects():
            # pylint: disable=E1136
            images = []
            res = client.list_images().get_result()
            images.extend(res['images'])

            while res.get('next'):
                link_to_next = res['next']['href'].split('start=')[1].split(
                    '&limit')[0]
                res = client.list_images(start=link_to_next).get_result()
                images.extend(res['images'])
            return images

        # if user specified an image id for the region, return it.
        # IBM-TODO take into account user input when --image-id
        # is implemented
        user_image = get_cred_file_field('image_id')
        if user_image and region in user_image:
            logger.debug(f'using user image: {user_image[region]} '
                         f'in region: {region}.')
            return user_image[region]

        client = ibm.client(region=region)
        # returns default image: "ibm-ubuntu-22-04" with amd architecture
        return next((img for img in _get_image_objects() if
         img['name'].startswith('ibm-ubuntu-22-04') \
            and img['operating_system']['architecture'].startswith(
                'amd')))['id']

    def get_image_size(self, image_id: str, region: Optional[str]) -> float:
        assert region is not None, (image_id, region)
        client = ibm.client(region=region)
        try:
            image_data = client.get_image(image_id).get_result()
        # pylint: disable=line-too-long
        except ibm.ibm_cloud_sdk_core.ApiException as e:  # type: ignore[union-attr]
            logger.error(e.message)
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Image {image_id!r} not found in '
                                 f'IBM region "{region}"') from None
        try:
            # image_size['file']['size'] is not relevant, since
            # the minimum size of a volume onto which this image
            # may be provisioned is stored in minimum_provisioned_size
            # pylint: disable=unsubscriptable-object
            image_size = image_data['minimum_provisioned_size']
        except KeyError:
            logger.error('Image missing metadata:"minimum_provisioned_size". '
                         'Image may be in status: Failed/Pending.')
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'IBM image {image_id!r} in '
                    f'region "{region}", is missing'
                    'metadata:"minimum_provisioned_size". '
                    'Image may be in status: Failed/Pending') from None
        return image_size

    @classmethod
    def check_credentials(cls) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        # IBM-TODO - create a configuration script.
        required_fields = ['iam_api_key', 'resource_group_id']
        help_str = ('    Store your API key and Resource Group id '
                    f'in {CREDENTIAL_FILE} in the following format:\n'
                    '      iam_api_key: <IAM_API_KEY>\n'
                    '      resource_group_id: <RESOURCE_GROUP_ID>')

        base_config = _read_credential_file()
        if not base_config:
            return (False, 'Missing credential file at '
                    f'{os.path.expanduser(CREDENTIAL_FILE)}.\n' + help_str)

        for field in required_fields:
            if field not in base_config:
                return (False, f'Missing field "{field}" in '
                        f'{os.path.expanduser(CREDENTIAL_FILE)}.\n' + help_str)

        # verifies ability of user to create a client,
        # e.g. bad API KEY.
        try:
            ibm.client()
            return True, None
        # pylint: disable=W0703
        except Exception as e:
            return (False, f'{str(e)}' + help_str)

    @classmethod
    def check_disk_tier_enabled(cls, instance_type: str,
                                disk_tier: str) -> None:
        del instance_type, disk_tier  # unused

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns a {remote:local} credential path mapping
         written to the cluster's file_mounts segment
         of its yaml file (e.g., ibm-ray.yml.j2)
        """
        return {CREDENTIAL_FILE: CREDENTIAL_FILE}

    def instance_type_exists(self, instance_type):
        """Returns whether the instance type exists for this cloud."""
        return service_catalog.instance_type_exists(instance_type, clouds='ibm')

    def validate_region_zone(self, region: Optional[str], zone: Optional[str]):
        """Validates the region and zone."""
        return service_catalog.validate_region_zone(region, zone, clouds='ibm')

    def accelerator_in_region_or_zone(self,
                                      accelerator: str,
                                      acc_count: int,
                                      region: Optional[str] = None,
                                      zone: Optional[str] = None) -> bool:
        """Returns whether the accelerator is valid in the region or zone."""
        return service_catalog.accelerator_in_region_or_zone(
            accelerator, acc_count, region, zone, 'ibm')

    @classmethod
    def query_status(cls, name: str, tag_filters: Dict[str, str],
                     region: Optional[str], zone: Optional[str],
                     **kwargs) -> List['status_lib.ClusterStatus']:
        del tag_filters, zone, kwargs  # unused

        status_map: Dict[str, Any] = {
            'pending': status_lib.ClusterStatus.INIT,
            'starting': status_lib.ClusterStatus.INIT,
            'restarting': status_lib.ClusterStatus.INIT,
            'running': status_lib.ClusterStatus.UP,
            'stopping': status_lib.ClusterStatus.STOPPED,
            'stopped': status_lib.ClusterStatus.STOPPED,
            'deleting': None,
            'failed': status_lib.ClusterStatus.INIT,
            'cluster_deleted': []
        }

        client = ibm.client(region=region)
        search_client = ibm.search_client()
        # pylint: disable=E1136
        vpcs_filtered_by_tags_and_region = search_client.search(
            query=f'type:vpc AND tags:{name} AND region:{region}',
            fields=['tags', 'region', 'type'],
            limit=1000).get_result()['items']
        if not vpcs_filtered_by_tags_and_region:
            # a vpc could have been removed unkownlingly to skypilot, such as
            # via `sky autostop --down`, or simply manually (e.g. via console).
            logger.warning('No vpc exists in '
                           f'{region} '
                           f'with tag: {name}')
            return status_map['cluster_deleted']
        vpc_id = vpcs_filtered_by_tags_and_region[0]['crn'].rsplit(':', 1)[-1]
        instances = client.list_instances(
            vpc_id=vpc_id).get_result()['instances']

        return [status_map[instance['status']] for instance in instances]


def _read_credential_file():
    try:
        with open(os.path.expanduser(CREDENTIAL_FILE), 'r',
                  encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return False


def get_cred_file_field(field, default_val=None) -> str:
    """returns a the value of a field from the user's
     credentials file if exists, else default_val"""
    base_config = _read_credential_file()
    if not base_config:
        raise FileNotFoundError('Missing '
                                f'credential file at {CREDENTIAL_FILE}')
    return base_config.get(field, default_val)
