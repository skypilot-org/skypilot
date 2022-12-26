import json
import uuid
from sky.adaptors import ibm
from ibm_cloud_sdk_core import ApiException
from sky.skylet.providers.ibm.utils import get_logger

VPC_CACHE_SEGMENT = 'vpc'
logger = get_logger()

class IBMVPCProvider():
    def __init__(self, resource_group_id ,region, zone, cache_path, cluster_name):
        self.vpc_client = ibm.client(region=region)
        self.resource_group_id = resource_group_id
        self.region = region
        self.zone = zone
        self.cluster_name = cluster_name
        self.cache_path = cache_path

    
    def create_or_fetch_vpc_tags(self):
        reuse_vpc = False
        vpc_fields = {'vpc_id','subnet_id','security_group_id','region','zone'}
        cluster_vpc_tags = {}

        if self.tags_file.is_file():
            all_tags = json.loads(self.cache_path.read_text()) # tags of all clusters
            
            if self.cluster_name in all_tags and VPC_CACHE_SEGMENT in all_tags[self.cluster_name]: 
                    cluster_vpc_tags = all_tags[self.cluster_name][VPC_CACHE_SEGMENT] 
                    if vpc_fields.issubset(set(cluster_vpc_tags.keys())) and cluster_vpc_tags.get('region')==self.region:
                        reuse_vpc = True                          
            else:
                all_tags.update({self.cluster_name:{VPC_CACHE_SEGMENT:{}}})

        if reuse_vpc:
            """
            if zone is different delete subnets and create a new subnet in that zone. 
            verify vpc components are valid, unless we just created them (if zone was different).
            if valid, return vpc_fields values, otherwise the module will continue to create a new vpc. 
            """

            # all_tags[self.cluster_name][VPC_CACHE_SEGMENT]=cluster_vpc_tags
            # return vpc_tags
            pass
        else:
            self.delete_vpc(cluster_vpc_tags.get('vpc_id'),cluster_vpc_tags.get('region'))
        vpc_tags = self.create_vpc()
        all_tags[self.cluster_name][VPC_CACHE_SEGMENT]=vpc_tags  
        return vpc_tags

    def create_vpc(self):
        vpc_data = self.vpc_client.create_vpc(address_prefix_management='auto', classic_access=False,
                                                name="test3", resource_group={'id':self.resource_group_id}).get_result()
        subnet_data = self.create_subnet(vpc_data['id'], self.zone)
        self.create_public_gateway(vpc_data['id'], self.zone, subnet_data)
        sg_id = self.create_sg_rules(vpc_data)
        
        return {"vpc_id":vpc_data['id'], "subnet_id":subnet_data['id'],"security_group_id":sg_id, "region":self.region, "zone":self.zone} 

    def create_subnet(self, vpc_id, zone_name):
        ipv4_cidr_block = None
        subnet_name = f"sky-subnet-{self.cluster_name}-{str(uuid.uuid4())[:5]}"
        res = self.vpc_client.list_vpc_address_prefixes(vpc_id).result
        address_prefixes = res['address_prefixes']

        # searching for the CIDR block (internal ip range) matching the specified zone of a VPC (whose region has already been set) 
        for address_prefix in address_prefixes:
            if address_prefix['zone']['name'] == zone_name:
                ipv4_cidr_block = address_prefix['cidr']
                break

        subnet_prototype = {}
        subnet_prototype['zone'] = {'name': zone_name}
        subnet_prototype['ip_version'] = 'ipv4'
        subnet_prototype['name'] = subnet_name
        subnet_prototype['resource_group'] = {'id':self.resource_group_id}
        subnet_prototype['vpc'] = {'id': vpc_id}
        subnet_prototype['ipv4_cidr_block'] = ipv4_cidr_block

        subnet_data = self.vpc_client.create_subnet(
            subnet_prototype).result
        return subnet_data
        

    def create_public_gateway(self, vpc_id, zone_name, subnet_data):
        
        gateway_prototype = {}
        gateway_prototype['vpc'] = {'id': vpc_id}
        gateway_prototype['zone'] = {'name': zone_name}
        gateway_prototype['name'] = f"{subnet_data['name']}-gw"
        gateway_prototype['resource_group'] = {'id':self.resource_group_id}
        gateway_data = self.vpc_client.create_public_gateway(
            **gateway_prototype).get_result()
        gateway_id = gateway_data['id']

        print(
            f"\033[92mVPC public gateway {gateway_prototype['name']} been created\033[0m")
        self.vpc_client.set_subnet_public_gateway(subnet_data['id'], {'id': gateway_id})
        return gateway_id            

    def create_sg_rules(self,vpc_data):
    
        def _build_security_group_rule_prototype_model(missing_rule, sg_id=None):
            direction, protocol, port = missing_rule.split('_')
            remote = {"cidr_block": "0.0.0.0/0"}

            try: # port number was specified
                port = int(port)
                port_min = port
                port_max = port
            except:
                port_min = 1
                port_max = 65535

                # only valid if security group already exists
                if port == 'sg':
                    if not sg_id:
                        return None
                    remote = {'id': sg_id}

            return {
                'direction': direction,
                'ip_version': 'ipv4',
                'protocol': protocol,
                'remote': remote,
                'port_min': port_min,
                'port_max': port_max
            }
        sg_id = vpc_data['default_security_group']['id']
        sg_name = f'{self.cluster_name}-{vpc_data["name"]}-sg'

        REQUIRED_RULES = {'outbound_tcp_all': 'selected security group is missing rule permitting outbound TCP access\n', 'outbound_udp_all': 'selected security group is missing rule permitting outbound UDP access\n', 'inbound_tcp_sg': 'selected security group is missing rule permitting inbound tcp traffic inside selected security group\n',
                        'inbound_tcp_22': 'selected security group is missing rule permitting inbound traffic to tcp port 22 required for ssh\n'}
        INSECURE_RULES = {'inbound_tcp_6379': 'selected security group is missing rule permitting inbound traffic to tcp port 6379 required for Redis\n', 'inbound_tcp_8265': 'selected security group is missing rule permitting inbound traffic to tcp port 8265 required to access Ray Dashboard\n'}
        REQUIRED_RULES.update(INSECURE_RULES)

        # update sg name
        self.vpc_client.update_security_group(
            sg_id, security_group_patch={'name': sg_name})

        # add rule to open private tcp traffic between VSIs within the security group
        sg_rule_prototype = _build_security_group_rule_prototype_model(
            'inbound_tcp_sg', sg_id=sg_id)
        self.vpc_client.create_security_group_rule(
            sg_id, sg_rule_prototype).get_result()

        # add all other required rules configured by the specific backend
        for rule in REQUIRED_RULES.keys():
            sg_rule_prototype = _build_security_group_rule_prototype_model(
                rule)
            if sg_rule_prototype:
                self.vpc_client.create_security_group_rule(
                    sg_id, sg_rule_prototype).get_result()

        return sg_id   


    def get_vpc_data(self, vpc_id, old_region):
        """returns vpc data if exists, else None"""
        if not vpc_id: return None
        tmp_vpc_client = ibm.client(region=old_region)
        try:
            vpc_data = tmp_vpc_client.get_vpc(vpc_id).result
            return vpc_data
        except ApiException as e:
            if e.code == 404:
                logger.debug("VPC doesn't exist.")
                return None
            else: raise 

    def delete_vpc(self, vpc_id, old_region):
        logger.debug(f"Deleting vpc:{vpc_id}")
        vpc_data = self.get_vpc_data(vpc_id, old_region)
        if not vpc_data:
            return None
        self.delete_subnets(vpc_id, old_region)
        tmp_vpc_client = ibm.client(region=old_region)
        # at this point vpc was already verified to be existing, thus no relevant exception to catch when deleting.  
        tmp_vpc_client.delete_vpc(vpc_id)

    def delete_subnets(self, vpc_id, old_region):
        tmp_vpc_client = ibm.client(region=old_region)
        vpc_data = self.get_vpc_data(vpc_id, old_region)

        subnets_attached_to_routing_table = tmp_vpc_client.list_subnets(routing_table_id = vpc_data['default_routing_table']['id']).get_result()['subnets']
        subnets_ids = [subnet['id'] for subnet in subnets_attached_to_routing_table]
        for id in subnets_ids:
            tmp_vpc_client.delete_subnet(id)