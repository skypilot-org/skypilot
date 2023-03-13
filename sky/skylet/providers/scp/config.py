import copy


class ZoneConfig:
    def __init__(self, scp_client, zone_name):
        self.scp_client = scp_client
        self.zone_id = self._get_region_id(zone_name)
        self.product_ids = self._set_product_list()
        self.product_group_ids = self._set_product_group()

    def _set_product_list(self):
        product_dict = {}
        product_contents = self.scp_client.list_products(self.zone_id)
        # product_contents = [item for item in product_contents if item['modifiedBy']=='ADMIN_PORTAL']

        for item in product_contents:
            key = item['productType'] + ":" + item['productName']
            val = item['productId']
            # assert key not in product_dict, key
            # if key in product_dict: print(key)
            product_dict[key] = val
        return product_dict

    def _set_product_group(self):
        group_contents = self.scp_client.list_product_groups(self.zone_id)
        group_dict = {}

        for item in group_contents:
            key = item['targetProductGroup'] + ":" + item['targetProduct']
            val = item['productGroupId']
            # assert key not in group_dict, key
            # if key in group_dict: print(key)
            group_dict[key] = val
        return group_dict

    def get_product_group(self, name):
        return self.product_group_ids[name]


    def bootstrap_instance_config(self, node_config):

        instance_config = {"imageId": node_config["imageId"]}
        instance_config['serviceZoneId'] = self.zone_id
        instance_config['serverTypeId'] = self.product_ids['SCALE:' + node_config['InstanceType']]
        instance_config['contractId'] = self.product_ids['CONTRACT_DISCOUNT:None']
        instance_config['productGroupId'] = self.get_product_group('COMPUTE:Virtual Server')


        miscellaneous ={
            'deletionProtectionEnabled': False,
            'dnsEnabled': False,
            'osAdmin':{
                'osUserId': 'root',
                'osUserPassword': 'example123$'
            },
            'blockStorage':{
                'blockStorageName': 'sky-storage',
                'diskSize': 100,
                'encryptEnabled': False,
                'productId': 'PRODUCT-sRlJ34iBr9hOxN9J5PrQxo'
            },
            "nic": {
                "natEnabled": True
            },

        }
        instance_config.update(miscellaneous)

        return instance_config

    def _get_region_id(self, region_name):
        zone_contents = self.scp_client.list_zones()
        zone_dict = {item['serviceZoneName']: item['serviceZoneId'] for item in zone_contents}
        return zone_dict[region_name]

    def get_vcp_subnets(self):
        vpc_contents = self.scp_client.list_vpcs(self.zone_id)
        vpc_list = [item['vpcId'] for item in vpc_contents if item['vpcState'] == 'ACTIVE']

        igw_contents = self.scp_client.list_igw()
        vps_with_igw = [item['vpcId'] for item in igw_contents if item['internetGatewayState']=='ATTACHED']

        vpc_list = [vpc for vpc in vpc_list if vpc in vps_with_igw ]

        subnet_contents = self.scp_client.list_subnets()

        vpc_subnets = {}
        for vpc in vpc_list:
            subnet_list = [item['subnetId'] for item in subnet_contents
                           if item['subnetState'] == 'ACTIVE' and item["vpcId"] == vpc]
            if len(subnet_list) > 0: vpc_subnets[vpc] = subnet_list

        return vpc_subnets
