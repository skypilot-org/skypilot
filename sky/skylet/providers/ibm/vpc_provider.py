import uuid
import copy
import time
from sky.adaptors import ibm
from ibm_cloud_sdk_core import ApiException
from sky.skylet.providers.ibm.utils import get_logger
from pprint import pprint

logger = get_logger("vpc_provider_")
REQUIRED_RULES = {'outbound_tcp_all': 'selected security group is missing rule permitting outbound TCP access\n', 'outbound_udp_all': 'selected security group is missing rule permitting outbound UDP access\n', 'inbound_tcp_sg': 'selected security group is missing rule permitting inbound tcp traffic inside selected security group\n',
                'inbound_tcp_22': 'selected security group is missing rule permitting inbound traffic to tcp port 22 required for ssh\n'}
INSECURE_RULES = {'inbound_tcp_6379': 'selected security group is missing rule permitting inbound traffic to tcp port 6379 required for Redis\n', 'inbound_tcp_8265': 'selected security group is missing rule permitting inbound traffic to tcp port 8265 required to access Ray Dashboard\n'}
REQUIRED_RULES.update(INSECURE_RULES)

class IBMVPCProvider():

    def __init__(self, resource_group_id, region, cluster_name):
        self.vpc_client = ibm.client(region=region)
        self.search_client = ibm.search_client()
        self.tagging_client = ibm.tagging_client()
        self.resource_group_id = resource_group_id
        self.cluster_name = cluster_name
        ## region and zone might change between failovers
        self.region = None   
        self.zone = None
    
    def create_or_fetch_vpc(self,region,zone):
        """
        returns a cluster with tag matching the cluster name if exists, else creates one
        an entry point (out of 2) to this module. 
        """

        # refresh client region scope if region changed. 
        if self.region and self.region!=region:
            self.vpc_client = ibm.client(region=region)
        self.region = region
        self.zone = zone
        reused_vpc_data = None
        vpcs_filtered_by_tags_and_region = self.search_client.search(query=f"type:vpc AND tags:{self.cluster_name} AND region:{self.region}",
                    fields=["tags","region","type"], limit = 1000).get_result()['items']  
        for vpc in vpcs_filtered_by_tags_and_region:
            vpc_id = vpc['crn'].rsplit(':',1)[-1]
            vpc_data = self.get_vpc_data(vpc_id,self.region)
            if vpc_data['status'] == 'available':
                reused_vpc_data = vpc_data
                break
        if reused_vpc_data:
                #verify subnet and gateway
            subnets = self.get_vpc_subnets(reused_vpc_data, self.region) # cached vpc is in the same region as self.region, otherwise it wouldn't get reused
            subnet_in_zone = next((subnet for subnet in subnets if subnet['zone']['name'] == self.zone), None)
            if subnet_in_zone:
                subnet_id = subnet_in_zone['id']
                public_gateway = subnet_in_zone.get('public_gateway')
                if not public_gateway:
                    public_gateway = self.create_public_gateway(vpc_id=reused_vpc_data['id'], zone=self.zone, subnet_data=subnet_in_zone)
            else: # create new subnet and gateway 
                subnet_data = self.create_subnet(reused_vpc_data['id'], self.region)
                subnet_id = subnet_data['id']
                public_gateway = self.create_public_gateway(vpc_id=reused_vpc_data['id'] ,zone=self.zone, subnet_data=subnet_data)

            # add missing security group rules if missing 
            security_group = reused_vpc_data.get('default_security_group')
            if security_group:
                sg_id = security_group['id']
                self.add_missing_sg_rules(sg_id)

                # managed to reuse found VPC
                logger.info(f"Reusing VPC {reused_vpc_data['id']} named: {reused_vpc_data['name']}")
                return {"vpc_id":reused_vpc_data['id'], "subnet_id":subnet_id, "security_group_id":sg_id} 
            else:
                self.delete_vpc(reused_vpc_data['id'], self.region)
        vpc_tags = self.create_vpc()
        return vpc_tags        

    def create_vpc(self):
        vpc_data = self.vpc_client.create_vpc(address_prefix_management='auto', classic_access=False,
                                                name=f"sky-vpc-{self.cluster_name}-{str(uuid.uuid4())[:5]}", resource_group={'id':self.resource_group_id}).get_result()
        subnet_data = self.create_subnet(vpc_data['id'], self.zone)
        self.create_public_gateway(vpc_data['id'], self.zone, subnet_data)
        sg_id = self.create_sg_rules(vpc_data)
        # tag vpc with the cluster's name
        resource_model = {'resource_id': vpc_data['crn']}
        tag_results = self.tagging_client.attach_tag(resources=[resource_model],tag_names=[self.cluster_name],tag_type='user').get_result()
        
        return {"vpc_id":vpc_data['id'], "subnet_id":subnet_data['id'],"security_group_id":sg_id} 

    def create_subnet(self, vpc_id, zone_name):
        ipv4_cidr_block = None
        subnet_name = f"sky-subnet-{self.cluster_name}-{str(uuid.uuid4())[:5]}"
        res = self.vpc_client.list_vpc_address_prefixes(vpc_id).get_result()

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
            subnet_prototype).get_result()
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

        self.vpc_client.set_subnet_public_gateway(subnet_data['id'], {'id': gateway_id})
        return gateway_id            

    def create_sg_rules(self,vpc_data):
    
        sg_id = vpc_data['default_security_group']['id']
        sg_name = f'{self.cluster_name}-{vpc_data["name"]}-sg'

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
    
    def get_vpc_subnets(self, vpc_data, region, field = ""):
        if not vpc_data:
            return None
        tmp_vpc_client = ibm.client(region=region)
        subnets_attached_to_routing_table = tmp_vpc_client.list_subnets(routing_table_id = vpc_data['default_routing_table']['id']).get_result()['subnets']
        if field:
            return [subnet[field] for subnet in subnets_attached_to_routing_table]
        else:
            return subnets_attached_to_routing_table

    def delete_vpc(self, vpc_id, old_region):
        """
        deletes a vpc with the specified id and region.
        an entry point to this module (alongside create_or_fetch_vpc) 
        """
        logger.debug(f"Deleting vpc:{vpc_id}")
        tmp_vpc_client = ibm.client(region=old_region)
        vpc_data = self.get_vpc_data(vpc_id, old_region)
        if not vpc_data:
            return None
        self.delete_subnets(tmp_vpc_client, vpc_data, old_region)
        self.delete_gateways(tmp_vpc_client,vpc_id)
        # at this point vpc was already verified to be existing, thus no relevant exception to catch when deleting.  
        tmp_vpc_client.delete_vpc(vpc_id)

    def delete_subnets(self, vpc_client, vpc_data, region):
        def _poll_subnet_deleted(subnet_id):
            tries = 10
            sleep_interval = 2
            while tries:
                try:
                    subnet_data = self.vpc_client.get_subnet(subnet_id).result
                except Exception:
                    logger.debug('Deleted subnet id: {}'.format(subnet_id))
                    return True
                tries -= 1
                time.sleep(sleep_interval)
            logger.debug(f"\Failed to delete instance within expected time frame of {tries*sleep_interval/60} minutes.\n")
            return False
        for subnet_id in self.get_vpc_subnets(vpc_data, region, field="id"):
            vpc_client.delete_subnet(subnet_id).get_result() # get_result() used for synchronization
            _poll_subnet_deleted(subnet_id)

    def delete_gateways(self, vpc_client, vpc_id):
        gateways = vpc_client.list_public_gateways(resource_group_id=self.resource_group_id).get_result()['public_gateways']
        gateways_ids_of_vpc = [gateway['id'] for gateway in gateways if gateway['vpc']['id']== vpc_id]
        for gateway_id in gateways_ids_of_vpc:
            vpc_client.delete_public_gateway(gateway_id).get_result() # get_result() used for synchronization

    def add_missing_sg_rules(self, sec_group_id):
        missing_rules = self.get_unsatisfied_security_group_rules(sec_group_id)

        if missing_rules:
            for val in missing_rules.values():
                logger.debug(f'missing {val}')
            for missing_rule in missing_rules.keys():
                sg_rule_prototype = _build_security_group_rule_prototype_model(missing_rule, sec_group_id)
                self.vpc_client.create_security_group_rule(sec_group_id, sg_rule_prototype).get_result()
                
    def get_unsatisfied_security_group_rules(self,sg_id):
        """
        returns unsatisfied security group rules.
        """

        unsatisfied_rules = copy.deepcopy(REQUIRED_RULES)
        sg = self.vpc_client.get_security_group(sg_id).get_result()

        for rule in sg['rules']:

            # check outbound rules that are not associated with a specific IP address range 
            if rule['direction'] == 'outbound' and rule['remote'] == {'cidr_block': '0.0.0.0/0'}:
                if rule['protocol'] == 'all':
                    # outbound is fine!
                    unsatisfied_rules.pop('outbound_tcp_all', None)
                    unsatisfied_rules.pop('outbound_udp_all', None)
                elif rule['protocol'] == 'tcp':
                    unsatisfied_rules.pop('outbound_tcp_all', None)
                elif rule['protocol'] == 'udp':
                    unsatisfied_rules.pop('outbound_udp_all', None)
            
            # Check inbound rules 
            elif rule['direction'] == 'inbound':
                # check rules that are not associated with a specific IP address range
                if rule['remote'] == {'cidr_block': '0.0.0.0/0'}:
                    # we interested only in all or tcp protocols
                    if rule['protocol'] == 'all':
                        # there a rule permitting all traffic
                        unsatisfied_rules.pop('inbound_tcp_sg', None)
                        unsatisfied_rules.pop('inbound_tcp_22', None)
                        unsatisfied_rules.pop('inbound_tcp_6379', None)
                        unsatisfied_rules.pop('inbound_tcp_8265', None)

                    elif rule['protocol'] == 'tcp':
                        if rule['port_min'] == 1 and rule['port_max'] == 65535:
                            # all ports are open
                            unsatisfied_rules.pop('inbound_tcp_sg', None)
                            unsatisfied_rules.pop('inbound_tcp_22', None)
                            unsatisfied_rules.pop('inbound_tcp_6379', None)
                            unsatisfied_rules.pop('inbound_tcp_8265', None)
                        else:
                            port_min = rule['port_min']
                            port_max = rule['port_max']
                            if port_min <= 22 and port_max >= 22:
                                unsatisfied_rules.pop('inbound_tcp_22', None)
                            elif port_min <= 6379 and port_max >= 6379:
                                unsatisfied_rules.pop('inbound_tcp_6379', None)
                            elif port_min <= 8265 and port_max >= 8265:
                                unsatisfied_rules.pop('inbound_tcp_8265', None)

                # rule regards private traffic within the VSIs associated with the security group  
                elif rule['remote'].get('id') == sg['id']:
                    # validate that inbound traffic inside group available
                    if rule['protocol'] == 'all' or rule['protocol'] == 'tcp':
                        unsatisfied_rules.pop('inbound_tcp_sg', None)

        return unsatisfied_rules  


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