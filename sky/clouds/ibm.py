import os
import yaml
import json
from sky import clouds
from sky.clouds import service_catalog
import typing
from typing import Dict, List, Optional, Tuple
if typing.TYPE_CHECKING:
    # renaming to avoid shadowing variables
    from sky import resources as resources_lib

_CREDENTIAL_FILES = {'path':os.path.expanduser('~/.ibm/'),'names':['credentials']}

@clouds.CLOUD_REGISTRY.register
class IBM(clouds.Cloud):
    """IBM Web Services."""

    _REPR = 'IBM'
    _regions: List[clouds.Region] = []
    
    @classmethod
    def regions(cls):
        if not cls._regions:
            # regions and zones 
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
        price_thresholds=[{'threshold':150,'price_per_gb':0.05},{'threshold':50,'price_per_gb':0.07},{'threshold':0,'price_per_gb':0.09}]
        for price_threshold in price_thresholds:
            cost+=(num_gigabytes - price_threshold['threshold']) * price_threshold['price_per_gb']
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

        if r.image_id is not None:
            image_id = r.image_id
        else:
            image_id = self.get_default_image()

        return {
            'instance_type': r.instance_type,
            'custom_resources': custom_resources,
            'use_spot': r.use_spot,
            'region': region_name,
            'zones': ','.join(zones),
            'image_id': image_id,
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
        return 'BL2.8x32'  

    def _get_default_region():
        return 'us_south'    

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
        return 'us_south'            

    @classmethod
    def get_default_image(cls):  # TODO IBM-TODO complete once the process of creating a gpu supported image has been streamlined.
        """ 
        Returns default image. if user ran the ibm script to creating a gpu image,
        its id is loaded from local cache, otherwise provides a clean image of ubuntu 20.04.  
        """
        base_config = None
        with open(cls.credentials_path) as f:
                    base_config = yaml.safe_load(f)
        if 'image_id' in base_config and base_config['image_id']:
            return base_config['image_id']
        else:
            # currently returning a clean image id of the image named: "ibm-ubuntu-20-04-4-minimal-amd64-2"
            return 'r006-3e7bdf31-21f8-4726-b1ea-ee53d3c589dc' 

            # May switch to dynamic implementation that requires authentication:
            """
            image_objects = self.ibm_vpc_client.list_images().get_result()['images']
            image_obj = next((obj for obj in image_objects if 'ibm-ubuntu-20-04-' in obj['name']), None)
            return image_obj['id']
            """
        
    def check_credentials(self) -> Tuple[bool, Optional[str]]:
        """Checks if the user has access credentials to this cloud."""
        help_str = (
            ' Run the following command:'
            f'a configuration script that asks for api key and stores it in {self.credentials_path}'
        )
        if not os.path.isfile(os.path.expanduser(self.credentials_path)):
            return (False, f'{self.credentials_path} does not exist.' + help_str)
        base_config = None
        with open(self.credentials_path) as f:
                    base_config = yaml.safe_load(f)
        if base_config and 'iam_api_key' in base_config:
            # IBM-TODO check the viability of initializing a client to verify api key. 
            return True, None
        else:
            return False, f"Missing iam_api_key filed in {self.credentials_path}"

    def get_credential_file_mounts(self) -> Dict[str, str]:
        """Returns the files necessary to access this cloud.

        Returns a dictionary that will be added to a task's file mounts.
        """
        base_path = _CREDENTIAL_FILES['path']
        return {
            f'{base_path}{filename}': f'{base_path}{filename}' for filename in _CREDENTIAL_FILES['names']
        }

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

    
        
