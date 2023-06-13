"""
module allocating VPC - network namespace and configuration
for Ray's cluster. used by the node_provider module to group the
nodes under the same subnet, tagged by the same cluster name.
"""

from concurrent.futures import ThreadPoolExecutor
import uuid
import copy
import time
import requests
import json
import textwrap
from sky.adaptors import ibm
from sky.skylet.providers.ibm.utils import get_logger, RAY_RECYCLABLE

# pylint: disable=line-too-long
logger = get_logger("vpc_provider_")
REQUIRED_RULES = {
    "outbound_tcp_all": "selected security group is missing rule permitting outbound TCP access\n",
    "outbound_udp_all": "selected security group is missing rule permitting outbound UDP access\n",
    "inbound_tcp_sg": "selected security group is missing rule permitting inbound tcp traffic inside selected security group\n",
    "inbound_tcp_22": "selected security group is missing rule permitting inbound traffic to tcp port 22 required for ssh\n",
}
INSECURE_RULES = {
    "inbound_tcp_6379": "selected security group is missing rule permitting inbound traffic to tcp port 6379 required for Redis\n",
    "inbound_tcp_8265": "selected security group is missing rule permitting inbound traffic to tcp port 8265 required to access Ray Dashboard\n",
}
REQUIRED_RULES.update(INSECURE_RULES)


class IBMVPCProvider:
    """
    Manages a vpc containing the network configuration for the cluster's nodes.
    """

    def __init__(self, resource_group_id, region, cluster_name):
        self.vpc_client = ibm.client(region=region)
        self.search_client = ibm.search_client()
        self.tagging_client = ibm.tagging_client()
        self.resource_group_id = resource_group_id
        self.cluster_name = cluster_name
        ## region and zone might change between failovers
        self.region = None
        self.zone = None

    def create_or_fetch_vpc(self, region, zone):
        """
        returns a cluster with tag matching the cluster name if exists, else creates one.
        an entry point (out of 2) to this module.
        """

        # refresh client region scope if region changed.
        if self.region and self.region != region:
            self.vpc_client = ibm.client(region=region)
        self.region = region
        self.zone = zone
        reused_vpc_data = None
        # pylint: disable=line-too-long
        vpcs_filtered_by_tags_and_region = self.search_client.search(
            query=f"type:vpc AND tags:{self.cluster_name} AND region:{self.region}",
            fields=["tags", "region", "type"],
            limit=1000,
        ).get_result()["items"]
        for vpc in vpcs_filtered_by_tags_and_region:
            vpc_id = vpc["crn"].rsplit(":", 1)[-1]
            vpc_data = self.get_vpc_data(vpc_id, self.region)
            if vpc_data["status"] == "available":
                reused_vpc_data = vpc_data
                break
        # found vpc tagged with cluster name in the required region
        if reused_vpc_data:
            # using self.region since tagged vpc is in the same region
            subnets = self.get_vpc_subnets(reused_vpc_data, self.region)
            subnet_in_zone = next(
                (subnet for subnet in subnets if subnet["zone"]["name"] == self.zone),
                None,
            )
            # found a subnet in the required zone
            if subnet_in_zone:
                subnet_id = subnet_in_zone["id"]
                public_gateway = subnet_in_zone.get("public_gateway")
                if not public_gateway:
                    public_gateway = self.create_public_gateway(
                        reused_vpc_data["id"], self.zone, subnet_in_zone
                    )
            # tagged vpc found doesn't have a subnet in the required zone
            else:
                subnet_data = self.create_subnet(reused_vpc_data["id"], self.zone)
                subnet_id = subnet_data["id"]
                public_gateway = self.create_public_gateway(
                    reused_vpc_data["id"], self.zone, subnet_data
                )

            # add missing security group rules if needed
            security_group = reused_vpc_data.get("default_security_group")
            if security_group:
                sg_id = security_group["id"]
                self.add_missing_sg_rules(sg_id)

                # managed to reuse found VPC
                logger.info(
                    f"Reusing VPC {reused_vpc_data['id']} named: {reused_vpc_data['name']}"
                )
                return {
                    "vpc_id": reused_vpc_data["id"],
                    "subnet_id": subnet_id,
                    "security_group_id": sg_id,
                }

        # delete a tagged vpc that doesn't meet requirements
        if reused_vpc_data:
            self.delete_vpc(reused_vpc_data["id"], self.region)
        # create a new vpc
        vpc_tags = self.create_vpc()
        return vpc_tags

    def create_vpc(self):
        """creates a vpc, tags it and return key values pertaining it.

        uuid is added to vpc name to avoid naming collision.
        vpc is tagged using the exact cluster name, as appears on
            the cluster's config file.

        Returns:
            dict: containing the keys: vpc_id, subnet_id
                    and security_group_id
        """
        vpc_data = self.vpc_client.create_vpc(
            address_prefix_management="auto",
            classic_access=False,
            name=f"sky-vpc-{self.cluster_name}-{str(uuid.uuid4())[:5]}",
            resource_group={"id": self.resource_group_id},
        ).get_result()
        subnet_data = self.create_subnet(vpc_data["id"], self.zone)
        self.create_public_gateway(vpc_data["id"], self.zone, subnet_data)
        sg_id = self.create_sg_rules(vpc_data)

        # tag vpc with the cluster's name
        resource_model = {"resource_id": vpc_data["crn"]}
        self.tagging_client.attach_tag(
            resources=[resource_model], tag_names=[self.cluster_name], tag_type="user"
        ).get_result()

        return {
            "vpc_id": vpc_data["id"],
            "subnet_id": subnet_data["id"],
            "security_group_id": sg_id,
        }

    def create_subnet(self, vpc_id, zone_name):
        ipv4_cidr_block = None
        subnet_name = f"sky-subnet-{self.cluster_name}-{str(uuid.uuid4())[:5]}"
        res = self.vpc_client.list_vpc_address_prefixes(vpc_id).get_result()

        # searching for the CIDR block (internal ip range) matching the
        # specified zone of a VPC (whose region has already been set)
        address_prefixes = res["address_prefixes"]
        ipv4_cidr_block = next(
            (
                address_prefix["cidr"]
                for address_prefix in address_prefixes
                if address_prefix["zone"]["name"] == zone_name
            ),
            None,
        )
        if not ipv4_cidr_block:
            raise Exception(
                "Failed to locate a cidr block "
                f"Matching the zone name: {zone_name} to create "
                "a subnet"
            )

        subnet_prototype = {}
        subnet_prototype["zone"] = {"name": zone_name}
        subnet_prototype["ip_version"] = "ipv4"
        subnet_prototype["name"] = subnet_name
        subnet_prototype["resource_group"] = {"id": self.resource_group_id}
        subnet_prototype["vpc"] = {"id": vpc_id}
        subnet_prototype["ipv4_cidr_block"] = ipv4_cidr_block

        subnet_data = self.vpc_client.create_subnet(subnet_prototype).get_result()
        return subnet_data

    def create_public_gateway(self, vpc_id, zone_name, subnet_data):

        gateway_prototype = {}
        gateway_prototype["vpc"] = {"id": vpc_id}
        gateway_prototype["zone"] = {"name": zone_name}
        gateway_prototype["name"] = f"{subnet_data['name']}-gw"
        gateway_prototype["resource_group"] = {"id": self.resource_group_id}
        gateway_data = self.vpc_client.create_public_gateway(
            **gateway_prototype
        ).get_result()
        gateway_id = gateway_data["id"]

        self.vpc_client.set_subnet_public_gateway(subnet_data["id"], {"id": gateway_id})
        return gateway_id

    def create_sg_rules(self, vpc_data):

        sg_id = vpc_data["default_security_group"]["id"]
        sg_name = f"{self.cluster_name}-{str(uuid.uuid4())[:5]}-sg"

        # update sg name
        self.vpc_client.update_security_group(
            sg_id, security_group_patch={"name": sg_name}
        )

        # open private tcp traffic between VSIs within the security group
        sg_rule_prototype = _build_security_group_rule_prototype_model(
            "inbound_tcp_sg", sg_id=sg_id
        )
        self.vpc_client.create_security_group_rule(
            sg_id, sg_rule_prototype
        ).get_result()

        # add all other required rules configured by the specific backend
        for rule in REQUIRED_RULES.keys():
            sg_rule_prototype = _build_security_group_rule_prototype_model(rule)
            if sg_rule_prototype:
                self.vpc_client.create_security_group_rule(
                    sg_id, sg_rule_prototype
                ).get_result()

        return sg_id

    def get_vpc_data(self, vpc_id, region):
        """returns vpc data if exists, else None"""
        if not vpc_id:
            return None
        tmp_vpc_client = ibm.client(region=region)
        try:
            vpc_data = tmp_vpc_client.get_vpc(vpc_id).result
            return vpc_data
        except ibm.ibm_cloud_sdk_core.ApiException as e:
            if e.code == 404:
                logger.debug("VPC doesn't exist.")
                return None
            else:
                raise

    def get_vpc_subnets(self, vpc_data, region, field=""):
        """return data on subnets belonging to specified vpc within
        the specified region.

        if 'field' is specified narrowing data returned to
        data['field'] (for each subnet)

        Args:
            vpc_data (str): vpc data as received from vpc client.
            region (str): ibm vpc region.
            field (str, optional): field within the subnet data response from vpc
              client's list_subnets()/get_subnet() response. Defaults to ''.

        Returns:
            str: data on all subnets belonging to the specified vpc.
        """
        if not vpc_data:
            return None
        # pylint: disable=line-too-long
        tmp_vpc_client = ibm.client(region=region)
        subnets_attached_to_routing_table = tmp_vpc_client.list_subnets(
            routing_table_id=vpc_data["default_routing_table"]["id"]
        ).get_result()["subnets"]
        if field:
            return [subnet[field] for subnet in subnets_attached_to_routing_table]
        else:
            return subnets_attached_to_routing_table

    def delete_vpc(self, vpc_id, region):
        """
        deletes a vpc with the specified id and region.
        an entry point to this module (alongside create_or_fetch_vpc)
        """
        logger.debug(f"Deleting vpc: {vpc_id}")
        tmp_vpc_client = ibm.client(region=region)
        vpc_data = self.get_vpc_data(vpc_id, region)
        if not vpc_data:
            logger.warn(f"vpc:{vpc_id} is set for deletion, but wasn't found")
            return None
        self.delete_vms(tmp_vpc_client, vpc_id)
        self.delete_subnets(tmp_vpc_client, vpc_data, region)
        self.delete_gateways(tmp_vpc_client, vpc_id)
        # at this point vpc was already verified to be existing
        # thus no relevant exception to catch when deleting.
        tmp_vpc_client.delete_vpc(vpc_id)

    def delete_vms(self, vpc_client, vpc_id):
        def _poll_vpc_contains_vms(vpc_id):
            tries = 60
            sleep_interval = 3
            while tries:
                # list_instances() never raise an exception, check values instead
                res = vpc_client.list_instances(vpc_id=vpc_id).get_result()
                if not res["total_count"]:
                    return True
                else:
                    tries -= 1
                    time.sleep(sleep_interval)
            raise Exception(
                "Failed to delete VPC's instances within "
                "the expected time frame. Cannot "
                "continue to delete VPC."
            )

        def _del_instance(vm_data):
            # first delete ips created by node_provider
            nic_id = vm_data["network_interfaces"][0]["id"]
            res = vpc_client.list_instance_network_interface_floating_ips(
                vm_data["id"], nic_id
            ).get_result()
            floating_ips = res.get("floating_ips", [])
            for ip in floating_ips:
                if ip["name"].startswith(RAY_RECYCLABLE):
                    logger.debug(f"Deleting IP: {ip['id']}")
                    vpc_client.delete_floating_ip(ip["id"])
            logger.debug(f"Deleting VM: {vm_data['id']}")
            vpc_client.delete_instance(id=vm_data["id"])

        # pylint: disable=line-too-long E1136
        res = vpc_client.list_instances(vpc_id=vpc_id).get_result()
        num_instances = res["total_count"]

        # Delete VSIs if exist
        if num_instances:
            instances = res["instances"]
            with ThreadPoolExecutor(num_instances) as ex:
                for i in range(num_instances):
                    ex.submit(_del_instance, instances[i])
            # wait until all vms are deleted to proceed
            _poll_vpc_contains_vms(vpc_id)

    def delete_subnets(self, vpc_client, vpc_data, region):
        def _poll_subnet_deleted(subnet_id):
            tries = 10
            sleep_interval = 2
            while tries:
                try:
                    vpc_client.get_subnet(subnet_id).get_result()
                except ibm.ibm_cloud_sdk_core.ApiException:
                    logger.debug(f"Deleted subnet id: {subnet_id}")
                    return True
                tries -= 1
                time.sleep(sleep_interval)
            logger.error("Failed to delete instance within the alloted time\n")
            return False

        for subnet_id in self.get_vpc_subnets(vpc_data, region, field="id"):
            # get_result() used for synchronization
            logger.debug(f"Deleting subnet: {subnet_id}")
            vpc_client.delete_subnet(subnet_id).get_result()
            _poll_subnet_deleted(subnet_id)

    def delete_gateways(self, vpc_client, vpc_id):
        """deletes all gateways attached to the specified vpc"""
        # pylint: disable=line-too-long
        gateways = vpc_client.list_public_gateways(
            resource_group_id=self.resource_group_id
        ).get_result()["public_gateways"]
        gateways_ids_of_vpc = [
            gateway["id"] for gateway in gateways if gateway["vpc"]["id"] == vpc_id
        ]
        for gateway_id in gateways_ids_of_vpc:
            # get_result() used for synchronization
            logger.debug(f"Deleting gateway: {gateway_id}")
            vpc_client.delete_public_gateway(gateway_id).get_result()

    def add_missing_sg_rules(self, sec_group_id):
        missing_rules = self.get_unsatisfied_security_group_rules(sec_group_id)

        if missing_rules:
            for val in missing_rules.values():
                logger.debug(f"missing {val}")
            for missing_rule in missing_rules.keys():
                sg_rule_prototype = _build_security_group_rule_prototype_model(
                    missing_rule, sec_group_id
                )
                self.vpc_client.create_security_group_rule(
                    sec_group_id, sg_rule_prototype
                ).get_result()

    def get_unsatisfied_security_group_rules(self, sg_id):
        """
        returns unsatisfied security group rules.
        """

        unsatisfied_rules = copy.deepcopy(REQUIRED_RULES)
        sg = self.vpc_client.get_security_group(sg_id).get_result()

        for rule in sg["rules"]:
            # pylint: disable=line-too-long
            # check outbound rules that are not associated with a specific IP address range
            if rule["direction"] == "outbound" and rule["remote"] == {
                "cidr_block": "0.0.0.0/0"
            }:
                if rule["protocol"] == "all":
                    # outbound is fine!
                    unsatisfied_rules.pop("outbound_tcp_all", None)
                    unsatisfied_rules.pop("outbound_udp_all", None)
                elif rule["protocol"] == "tcp":
                    unsatisfied_rules.pop("outbound_tcp_all", None)
                elif rule["protocol"] == "udp":
                    unsatisfied_rules.pop("outbound_udp_all", None)

            # Check inbound rules
            elif rule["direction"] == "inbound":
                # check rules that are not associated with a specific IP address range
                if rule["remote"] == {"cidr_block": "0.0.0.0/0"}:
                    # we interested only in all or tcp protocols
                    if rule["protocol"] == "all":
                        # there a rule permitting all traffic
                        unsatisfied_rules.pop("inbound_tcp_sg", None)
                        unsatisfied_rules.pop("inbound_tcp_22", None)
                        unsatisfied_rules.pop("inbound_tcp_6379", None)
                        unsatisfied_rules.pop("inbound_tcp_8265", None)

                    elif rule["protocol"] == "tcp":
                        if rule["port_min"] == 1 and rule["port_max"] == 65535:
                            # all ports are open
                            unsatisfied_rules.pop("inbound_tcp_sg", None)
                            unsatisfied_rules.pop("inbound_tcp_22", None)
                            unsatisfied_rules.pop("inbound_tcp_6379", None)
                            unsatisfied_rules.pop("inbound_tcp_8265", None)
                        else:
                            port_min = rule["port_min"]
                            port_max = rule["port_max"]
                            if port_min <= 22 and port_max >= 22:
                                unsatisfied_rules.pop("inbound_tcp_22", None)
                            elif port_min <= 6379 and port_max >= 6379:
                                unsatisfied_rules.pop("inbound_tcp_6379", None)
                            elif port_min <= 8265 and port_max >= 8265:
                                unsatisfied_rules.pop("inbound_tcp_8265", None)

                # rule regards private traffic within the VSIs associated with the security group
                elif rule["remote"].get("id") == sg["id"]:
                    # validate that inbound traffic inside group available
                    if rule["protocol"] == "all" or rule["protocol"] == "tcp":
                        unsatisfied_rules.pop("inbound_tcp_sg", None)

        return unsatisfied_rules

    def remote_cluster_removal(self, vpc_id, region):
        """deletes the vpc and its associated resources
        using the FaaS service: ibm cloud functions.
        Used only when cluster is set to be deleted
        remotely, e.g. by `sky autostop --down`"""
        cc = ClusterCleaner(self.resource_group_id, vpc_id, region)
        cc.delete_cluster()


def _build_security_group_rule_prototype_model(missing_rule, sg_id=None):
    direction, protocol, port = missing_rule.split("_")
    remote = {"cidr_block": "0.0.0.0/0"}

    try:  # port number was specified
        port = int(port)
        port_min = port
        port_max = port
    # pylint: disable=W0703
    except Exception:
        port_min = 1
        port_max = 65535

        # only valid if security group already exists
        if port == "sg":
            if not sg_id:
                return None
            remote = {"id": sg_id}

    return {
        "direction": direction,
        "ip_version": "ipv4",
        "protocol": protocol,
        "remote": remote,
        "port_min": port_min,
        "port_max": port_max,
    }


class ClusterCleaner:
    """Responsible for deleting a cluster with all its associated
    resources. Used when the remote cluster head is requested to
    dismantle its cluster."""

    # default region for the cloud function namespace
    namespace_region = "us-east"
    # default name for the cloud function namespace (cf doesn't support tagging)
    namespace_name = "skypilot-namespace"
    # default name for the cloud function action.
    action_name = "skypilot-vpc-cleaner-action"
    # url to cloud function's namespaces in the chosen region: `namespace_region`
    cf_namespaces_url = (
        f"https://{namespace_region}.functions.cloud.ibm.com/api/v1/namespaces"
    )

    def __init__(self, resource_group_id, vpc_id, vpc_region) -> None:
        self.resource_group_id = resource_group_id
        self.vpc_id = vpc_id
        self.vpc_region = vpc_region

    function_code = textwrap.dedent(
        """
    import subprocess
    import time
    from concurrent.futures import ThreadPoolExecutor
    RAY_RECYCLABLE = "ray-recyclable"
    ibm_vpc_client = None
    # modules installed and imported entry point
    ibm_vpc = None
    ibm_cloud_sdk_core = None

    def get_vpc_data(vpc_id):

        if not vpc_id: return None
        try:
            vpc_data = ibm_vpc_client.get_vpc(vpc_id).result
            return vpc_data
        except ibm_cloud_sdk_core.ApiException as e:
            if e.code == 404:
                print(("VPC doesn't exist."))
                return None
            else: raise 

    def delete_subnets(vpc_data):
        def _poll_subnet_exists(subnet_id):
            tries = 30 # waits up to 5 min with 10 sec interval
            sleep_interval = 10
            while tries:
                try:
                    subnet_data = ibm_vpc_client.get_subnet(subnet_id).result
                except Exception:
                    print('Deleted subnet id: {}'.format(subnet_id))
                    return True
                tries -= 1
                time.sleep(sleep_interval)
            print(f"Failed to delete instance within expected time frame of {tries*sleep_interval/60} minutes.")
            return False

        subnets_attached_to_routing_table = ibm_vpc_client.list_subnets(routing_table_id = vpc_data['default_routing_table']['id']).get_result()['subnets']
        subnets_ids = [subnet['id'] for subnet in subnets_attached_to_routing_table]
        for id in subnets_ids:
            try:
                ibm_vpc_client.delete_subnet(id).get_result()
                _poll_subnet_exists(id)
            except ibm_cloud_sdk_core.ApiException as e:
                if e.code == 404:
                    print("subnet doesn't exist.")

    def delete_gateways(vpc_id):
        gateways = ibm_vpc_client.list_public_gateways(resource_group_id=RESOURCE_GROUP_ID).get_result()['public_gateways']
        gateways_ids_of_vpc = [gateway['id'] for gateway in gateways if gateway['vpc']['id']== vpc_id]
        for gateway_id in gateways_ids_of_vpc:
            deleting_resource = True
            while deleting_resource:
                try:
                    ibm_vpc_client.delete_public_gateway(gateway_id).get_result()
                    deleting_resource = False
                except ibm_cloud_sdk_core.ApiException as e:
                    if e.code == 404:
                        print("gateway doesn't exist.") 
                        deleting_resource = False
                    if e.code == 409:
                        print("gateway still in use.")
                        # will retry until cloud functions timeout. 
                        time.sleep(5) 

    def delete_vms(vpc_id):
        def _poll_vpc_contains_vms(vpc_id):
            tries = 60
            sleep_interval = 3
            while tries:
                # list_instances() never raise an exception, check values instead
                res = ibm_vpc_client.list_instances(vpc_id=vpc_id).get_result()
                if not res["total_count"]:
                    return True
                else:
                    tries -= 1
                    time.sleep(sleep_interval)
            raise Exception(
                "Failed to delete VPC's instances within "
                "the expected time frame. Cannot "
                "continue to delete VPC."
            )

        def _del_instance(vm_data):
            # first delete ips created by node_provider 
            nic_id = vm_data["network_interfaces"][0]["id"]
            res = ibm_vpc_client.list_instance_network_interface_floating_ips(
                vm_data["id"], nic_id
            ).get_result()
            floating_ips = res.get("floating_ips", [])
            for ip in floating_ips:
                if ip["name"].startswith(RAY_RECYCLABLE):
                    print(f"Deleting IP: {ip['id']}")
                    ibm_vpc_client.delete_floating_ip(ip["id"])
            print(f"Deleting VM: {vm_data['id']}")
            ibm_vpc_client.delete_instance(id=vm_data["id"])
            
        res = ibm_vpc_client.list_instances(vpc_id=vpc_id).get_result()
        num_instances = res["total_count"]

        # Delete VSIs if exist
        if num_instances:
            instances = res["instances"]
            with ThreadPoolExecutor(num_instances) as ex:
                for i in range(num_instances):
                    ex.submit(_del_instance, instances[i])
            # wait until all vms are deleted to proceed
            _poll_vpc_contains_vms(vpc_id)

    def delete_unbound_vpc(vpc_id):
        deleting_resource = True
        while deleting_resource:
            try:
                ibm_vpc_client.delete_vpc(vpc_id).get_result()
                deleting_resource = False
            except ibm_cloud_sdk_core.ApiException as e:
                if e.code == 404:
                    print("VPC doesn't exist.") 
                    deleting_resource = False
                if e.code == 409:
                    print("VPC still in use.")
                    # will retry until cloud functions timeout. 
                    time.sleep(5) 

    def delete_vpc(vpc_id):
        vpc_data = get_vpc_data(vpc_id)
        if not vpc_data:
            print((f"Failed to find a VPC with id={vpc_id}"))
            return
        print(f"Deleting vpc:{vpc_data['name']} with id:{vpc_id}")
        delete_vms(vpc_id)
        delete_subnets(vpc_data)
        delete_gateways(vpc_id)
        delete_unbound_vpc(vpc_id)
        print(f"VPC {vpc_data['name']} and its attached resources were deleted successfully")


    def main(dict):
        global ibm_vpc_client, RESOURCE_GROUP_ID, ibm_cloud_sdk_core, ibm_vpc
        def install_package(package):
            pip_location_stdout = subprocess.run(['which', 'pip'], capture_output=True, text=True)
            pip_location = pip_location_stdout.stdout.strip()
            subprocess.call([pip_location, 'install', package], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

        for package in ["ibm-vpc", "ibm-cloud-sdk-core"]:
            install_package(package)

        import ibm_vpc as _ibm_vpc
        import ibm_cloud_sdk_core as _ibm_cloud_sdk_core
        ibm_vpc = _ibm_vpc
        ibm_cloud_sdk_core = _ibm_cloud_sdk_core

        iam_api_key, RESOURCE_GROUP_ID, vpc_id, region = dict['iam_api_key'], dict['resource_group_id'], dict['vpc_id'], dict['region']

        authenticator = ibm_cloud_sdk_core.authenticators.IAMAuthenticator(iam_api_key, url=None)
        ibm_vpc_client = ibm_vpc.VpcV1('2022-06-30',authenticator=authenticator)

        if not region:
            raise Exception("VPC not found in any region")

        ibm_vpc_client.set_service_url(f'https://{region}.iaas.cloud.ibm.com/v1')

        delete_vpc(vpc_id=vpc_id)
        return {"Status": "Success"}
    """
    )

    def get_headers(self):
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + ibm.get_oauth_token(),
        }

    def create_or_fetch_namespace(self):
        """returns namespace id.
        creates a namespace with given name in specified region.
        if namespace exists returns its id instead."""

        def _create_new_namespace():
            logger.info(
                f"Creating a new namespace: {self.namespace_name} in {self.namespace_region}"
            )

            data = {
                "name": self.namespace_name,
                "resource_group_id": self.resource_group_id,
                "resource_plan_id": "functions-base-plan",
            }

            res = requests.post(
                self.cf_namespaces_url, headers=self.get_headers(), json=data
            ).json()
            if res.status_code != 200:
                logger.error(res.text)
            namespace_id = res["id"]
            logger.info(f"Created new namespace with id: {namespace_id}")
            return namespace_id

        def _get_cloud_function_namespaces_metadata(offset=0):
            """returns meta data on namespaces of ibm cloud functions within a specified region
            :param offset - offset from the beginning of the list of results attained from the GET request,
                            which may contain up to 200 namespaces per http response"""

            res = requests.get(
                f"{self.cf_namespaces_url}?limit=200&offset={offset}",
                headers=self.get_headers(),
            )
            return json.loads(res.text)

        def _get_cloud_function_namespaces():
            """returns relevant metadata on existing namespaces within a given region."""
            logger.info(
                f"Obtaining Cloud Function namespaces in {self.namespace_region}"
            )

            namespaces = []

            collecting_namespaces = True
            max_limit = 200
            offset = 0

            #  request for namespaces is limited to 200 at a time, thus the request is fulfilled in increments of 200s.
            while collecting_namespaces:
                namespace_metadata = _get_cloud_function_namespaces_metadata(offset)
                if namespace_metadata["total_count"] == max_limit:
                    offset += max_limit
                else:
                    collecting_namespaces = False

                for name_space in namespace_metadata["namespaces"]:
                    if "name" in name_space:  # API based namespace
                        namespaces.append(
                            {
                                "name": name_space["name"],
                                "type": "API_based",
                                "id": name_space["id"],
                                "region": name_space["location"],
                            }
                        )

                    else:  # cloud foundry based namespace
                        namespaces.append(
                            {
                                "name": name_space["id"],
                                "type": "CF_based",
                                "region": name_space["location"],
                            }
                        )

            return namespaces

        namespaces_in_region = _get_cloud_function_namespaces()
        target_namespace_id = None
        if namespaces_in_region:
            target_namespace_id = next(
                (
                    namespace["id"]
                    for namespace in namespaces_in_region
                    if namespace["name"] == self.namespace_name
                ),
                None,
            )
        if not target_namespace_id:
            target_namespace_id = _create_new_namespace()
        else:
            logger.info(f"Reusing namespace: {target_namespace_id}")
        return target_namespace_id

    def _get_cloud_functions_actions(self, namespace_id):
        """returns meta data on namespaces of ibm cloud functions within a specified region
        :param offset - offset from the beginning of the list of results attained from the GET request,
                        which may contain up to 200 namespaces per http response"""

        res = requests.get(
            f"{self.cf_namespaces_url}/{namespace_id}/actions?limit=200",
            headers=self.get_headers(),
        )
        return json.loads(res.text)

    def create_action(self, namespace_id):
        logger.info(f"creating action on namespace: {namespace_id}")
        # Define the function parameters
        function_params = {
            "exec": {"kind": "python:3.9", "code": self.function_code},
            "limits": {"timeout": 600000},
        }
        res = requests.put(
            f"{self.cf_namespaces_url}/{namespace_id}/actions/{self.action_name}?blocking=true&overwrite=true",
            headers=self.get_headers(),
            data=json.dumps(function_params),
        )
        if res.status_code != 200:
            logger.error(res.text)
        return json.loads(res.text)

    def delete_action(self, namespace_id):
        """return the deleted function's metadata if it existed."""
        logger.info(f"deleting action on namespace: {namespace_id}")
        res = requests.delete(
            f"{self.cf_namespaces_url}/{namespace_id}/actions/{self.action_name}?blocking=true",
            headers=self.get_headers(),
        )
        if res.status_code != 200:
            logger.warn(res.text)
        return json.loads(res.text)

    def invoke_action(self, namespace_id: str):
        logger.info(f"invoking action on namespace: {namespace_id}")
        payload = {
            "iam_api_key": ibm.get_api_key(),
            "resource_group_id": self.resource_group_id,
            "vpc_id": self.vpc_id,
            "region": self.vpc_region,
        }
        res = requests.post(
            f"{self.cf_namespaces_url}/{namespace_id}/actions/{self.action_name}?blocking=true",
            headers=self.get_headers(),
            data=json.dumps(payload),
        )
        if res.status_code != 200:
            logger.error(res.text)
        return json.loads(res.text)

    def delete_cluster(self):
        """Deletes VPC with id==self.vpc_id.
        1. creates a CloudFunctions namespace named ClusterCleaner.namespace_name if doesn't exists.
        2. using idempotent function that deletes an action named ClusterCleaner.action_name if exists.
        3. invokes the action to delete the VPC and all its resources."""
        cf_namespace_id = self.create_or_fetch_namespace()
        self.delete_action(cf_namespace_id)
        self.create_action(cf_namespace_id)
        self.invoke_action(cf_namespace_id)
