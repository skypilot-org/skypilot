import copy


class ZoneConfig:
    def __init__(self, scp_client, zone_name):
        self.zone_name = zone_name
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
        instance_config['serverType'] = node_config['InstanceType']
        instance_config['contractId'] = "None"
        instance_config['initialScript'] = self._get_vm_init_script(node_config['auth']['ssh_public_key'])

        miscellaneous ={
            'deletionProtectionEnabled': False,
            'dnsEnabled': False,
            'osAdmin':{
                'osUserId': 'root',
                'osUserPassword': 'example123$'
            },
            'blockStorage':{
                'blockStorageName': 'skystorage',
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

    def _get_vm_init_script(self, ssh_public_key_path):

        init_script_content = self._get_default_config_cmd() + self._get_ssh_key_gen_cmd(ssh_public_key_path)
        return {
            "encodingType" : "plain",
            "initialScriptShell": "bash",
            "initialScriptType": "text",
            "initialScriptContent": init_script_content
        }

    def _get_ssh_key_gen_cmd(self, ssh_public_key_path):
        cmd_st = "mkdir -p ~/.ssh/; touch ~/.ssh/authorized_keys;"
        cmd_ed = "chmod 644 ~/.ssh/authorized_keys; chmod 700 ~/.ssh/"
        try:
            with open(ssh_public_key_path, 'r') as f:
                key = f.read()
        # Load configuration file values
        except FileNotFoundError:
            print('Public SSH key does not exist.')

        cmd = "echo '{}' &>>~/.ssh/authorized_keys;".format(key)

        return cmd_st + cmd + cmd_ed
    def _get_default_config_cmd(self):
        cmd_list = ["echo 'nameserver 8.8.8.8' &>>/etc/resolv.conf",
                    "echo export LANG=ko_KR.utf8 &>>~/.bashrc",
                    "echo export LC_ALL=ko_KR.utf8 &>>~/.bashrc",
                    "sed -i '/alias cp=/d' ~/.bashrc",
                    "source ~/.bashrc",
                    "yum -y install rsync"]

        res = ""
        for cmd in cmd_list:
            res += cmd + "; "

        return res