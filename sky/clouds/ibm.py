"""IBM Web Services."""
import os
import yaml
import json
from sky import clouds
from sky.clouds import service_catalog
from sky.adaptors import ibm
import typing
from typing import Dict, Iterator, List, Optional, Tuple
from sky.adaptors.ibm import CREDENTIAL_FILE

from sky.clouds.cloud import Region, Zone
if typing.TYPE_CHECKING:
    # renaming to avoid shadowing variables
    from sky import resources as resources_lib


@clouds.CLOUD_REGISTRY.register
class IBM(clouds.Cloud):
    """IBM Web Services."""

    _REPR = 'IBM'
    _regions: List[clouds.Region] = []

    @classmethod
    def regions(cls):
        if not cls._regions:
            ## regions and zones
            cls._regions = [
                clouds.Region('us-south').set_zones([
                    clouds.Zone('us-south-1'),
                    clouds.Zone('us-south-2'),
                    clouds.Zone('us-south-3')
                ]),
                clouds.Region('us-east').set_zones([
                    clouds.Zone('us-east-1'),
                    clouds.Zone('us-east-2'),
                    clouds.Zone('us-east-3'),
                ]),
                clouds.Region('eu-de').set_zones([
                    clouds.Zone('eu-de-1'),
                    clouds.Zone('eu-de-2'),
                    clouds.Zone('eu-de-3'),
                ]),
                clouds.Region('eu-gb').set_zones([
                    clouds.Zone('eu-gb-1'),
                    clouds.Zone('eu-gb-2'),
                    clouds.Zone('eu-gb-3'),
                ]),
                clouds.Region('au-syd').set_zones([
                    clouds.Zone('au-syd-1'),
                    clouds.Zone('au-syd-2'),
                    clouds.Zone('au-syd-3'),
                ]),
                clouds.Region('br-sao').set_zones([
                    clouds.Zone('br-sao-1'),
                    clouds.Zone('br-sao-2'),
                    clouds.Zone('br-sao-3'),
                ]),
                clouds.Region('ca-tor').set_zones([
                    clouds.Zone('ca-tor-1'),
                    clouds.Zone('ca-tor-2'),
                    clouds.Zone('ca-tor-3'),
                ]),
                clouds.Region('jp-osa').set_zones([
                    clouds.Zone('jp-osa-1'),
                    clouds.Zone('jp-osa-2'),
                    clouds.Zone('jp-osa-3'),
                ]),
                clouds.Region('jp-tok').set_zones([
                    clouds.Zone('jp-tok-1'),
                    clouds.Zone('jp-tok-2'),
                    clouds.Zone('jp-tok-3'),
                ]),
            ]
        return cls._regions

    @classmethod
    def region_zones_provision_loop(
        cls,
        *,
        instance_type: Optional[str] = None,
        accelerators: Optional[Dict[str, int]] = None,
        use_spot: Optional[bool] = False,
    ) -> Iterator[Tuple[Region, List[Zone]]]:
        """Loops over (region, zones) to retry for provisioning.

        returning a single zone list with its region,
        since ibm cloud currently doesn't
        support retries for list of zones.

        Args:
            instance_type: The instance type to provision.
            accelerators: The accelerators to provision.
            use_spot: Whether to use spot instances.
        """
        del accelerators  # unused

        if instance_type is None:
            # fallback to manually specified region/zones
            regions = cls.regions()
        else:
            regions = service_catalog.get_region_zones_for_instance_type(
                instance_type, use_spot, 'ibm')

        for region in regions:
            for zone in region.zones:
                yield region, [zone]

    @classmethod
    def get_zone_shell_cmd(cls) -> Optional[str]:
        return None

    def instance_type_to_hourly_cost(self, instance_type: str, use_spot: bool):

        # Currently doesn't support spot instances, hence use_spot set to False.
        return service_catalog.get_hourly_cost(instance_type,
                                               region=None,
                                               use_spot=False,
                                               clouds='ibm')

    def accelerators_to_hourly_cost(self, accelerators, use_spot):
        """Returns the hourly on-demand price for accelerators."""
        # IBM includes accelerators as part of the instance type.
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
        region: Optional['clouds.Region'],
        zones: Optional[List['clouds.Zone']],
    ) -> Dict[str, str]:
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
            profile_resources_str = instance_profile.split('-')[1]
            # gpu count based on profile
            gpu_num = int(
                profile_resources_str.split('x')[2].split('v')[0]) if (len(
                    profile_resources_str.split('x')) == 3) else 0
            # cpu count based on profile
            cpu_num = int(profile_resources_str.split('x')[0])
            # memory GBs on profile
            memory_num = int(profile_resources_str.split('x')[1])
            return {'CPU': cpu_num, 'memory': memory_num, 'GPU': gpu_num}

        if region is None:
            assert zones is None, (
                'Set either both or neither for: region, zones.')
            region = self._get_default_region()
            zones = region.zones
        else:
            assert zones is not None, (
                'Set either both or neither for: region, zones.')
        region_name = region.name
        zones = [zone.name for zone in zones]

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

        if r.image_id is not None:
            image_id = r.image_id
        else:
            image_id = self.get_default_image(region_name)

        return {
            'instance_type': r.instance_type,
            'instance_resources': instance_resources,
            'worker_instance_type': worker_instance_type,
            'worker_instance_resources': worker_instance_resources,
            'custom_resources': custom_resources,
            'use_spot': r.use_spot,
            'region': region_name,
            'zones': ','.join(zones),
            'image_id': image_id,
            'iam_api_key': get_cred_file_field('iam_api_key'),
            'resource_group_id': get_cred_file_field('resource_group_id'),
            'disk_size': get_cred_file_field('disk_capacity', 100)
        }

    @classmethod
    def get_vcpus_from_instance_type(cls,
                                     instance_type: str) -> Optional[float]:
        """Returns the number of virtual CPUs that the instance type offers."""
        return service_catalog.get_vcpus_from_instance_type(instance_type,
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
    def get_default_instance_type(cls) -> str:
        # 8 vCpus, 32 GB RAM.
        return get_cred_file_field('instance_profile_name', 'bx2d-8x32')

    def get_feasible_launchable_resources(self, resources):
        """Returns a list of feasible and launchable resources.

        Feasible resources refer to an offering respecting the resource
        requirements.  Currently, this function implements "filtering" the
        cloud's offerings only w.r.t. accelerators constraints.

        Launchable resources require a cloud and an instance type be assigned.
        """
        fuzzy_candidate_list = []
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
                )
                resource_list.append(r)
            return resource_list

        # Currently, handle a filter on accelerators only.
        accelerators = resources.accelerators
        if accelerators is None:
            # No requirements to filter, so just return a default VM type.
            return (_make([IBM.get_default_instance_type()]),
                    fuzzy_candidate_list)

        assert len(accelerators) == 1, resources
        acc, acc_count = list(accelerators.items())[0]
        (instance_list, fuzzy_candidate_list
        ) = service_catalog.get_instance_type_for_accelerator(acc,
                                                              acc_count,
                                                              clouds='ibm')
        if instance_list is None:
            return ([], fuzzy_candidate_list)
        return (_make(instance_list), fuzzy_candidate_list)

    @classmethod
    def _get_default_region(cls) -> clouds.Region:
        return get_cred_file_field('region', 'us_south')

    @classmethod
    def get_default_image(cls, region):
        """
        Returns default image.
        if user ran the ibm script to creating a gpu image,
        its id is loaded from local cache,
        otherwise provides a clean image of ubuntu 22-04.
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

        if get_cred_file_field('image_id'):
            return get_cred_file_field('image_id')

        client = ibm.client(region=region)
        # returns default image: "ibm-ubuntu-22-04" with amd architecture
        return next((img for img in _get_image_objects() if
         img['name'].startswith('ibm-ubuntu-22-04') \
            and img['operating_system']['architecture'].startswith(
                'amd')))['id']

    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""

        help_str = (f""" Run the following command:
            a configuration script that asks for
            api key and stores it in {CREDENTIAL_FILE}""")
        if not os.path.isfile(os.path.expanduser(CREDENTIAL_FILE)):
            return (False, f"""
            {os.path.expanduser(CREDENTIAL_FILE)} does not exist.""" + help_str)

        base_config = _read_credential_file()
        if base_config and 'iam_api_key' in base_config:
            try:
                ibm.client()
                return True, None
            # pylint: disable=W0703
            except Exception as e:
                return False, str(e)
        else:
            return False, f"""Missing iam_api_key in
             {os.path.expanduser(CREDENTIAL_FILE)}"""

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


def _read_credential_file():
    with open(os.path.expanduser(CREDENTIAL_FILE), 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_cred_file_field(field, default_val=None) -> str:
    """returns a the value of a field from the user's
     credentials file if exists, else default_val"""
    return _read_credential_file().get(field, default_val)
