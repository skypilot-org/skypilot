#
# (C) Copyright IBM Corp. 2021
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
""" module allocating nodes to Ray's cluster.
 used by the Ray framework as an external connector to IBM provider. """

import concurrent.futures as cf
import inspect
import json
import re
import socket
import threading
import time
from pathlib import Path
from pprint import pprint
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ray.autoscaler._private.cli_logger import cli_logger
from ray.autoscaler._private.util import hash_launch_conf, hash_runtime_conf
from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler.tags import (
    NODE_KIND_HEAD,
    NODE_KIND_WORKER,
    TAG_RAY_CLUSTER_NAME,
    TAG_RAY_FILE_MOUNTS_CONTENTS,
    TAG_RAY_LAUNCH_CONFIG,
    TAG_RAY_NODE_KIND,
    TAG_RAY_NODE_NAME,
    TAG_RAY_NODE_STATUS,
    TAG_RAY_RUNTIME_CONFIG,
    TAG_RAY_USER_NODE_TYPE,
)

from sky.adaptors import ibm
from sky.skylet.providers.ibm.utils import RAY_RECYCLABLE, get_logger
from sky.skylet.providers.ibm.vpc_provider import IBMVPCProvider

logger = get_logger("node_provider_")

INSTANCE_NAME_UUID_LEN = 8
INSTANCE_NAME_MAX_LEN = 64
PENDING_TIMEOUT = 3600  # period before a node that's yet to run is deleted.
VOLUME_TIER_NAME_DEFAULT = "general-purpose"
# identifies resources created by this package.
# these resources are deleted alongside the node.
VPC_TAGS = ".sky-vpc-tags"


def log_in_out(func):
    """Tracing decorator for debugging purposes"""

    def decorated_func(*args, **kwargs):
        name = func.__name__
        logger.debug(
            f"\n\nEnter {name} from {inspect.stack()[0][3]} "
            f"{inspect.stack()[1][3]} {inspect.stack()[2][3]} with args: "
            f"entered with args:\n{pprint(args)} and kwargs {pprint(kwargs)}"
        )
        try:
            result = func(*args, **kwargs)
            logger.debug(
                f"Leave {name} from {inspect.stack()[1][3]} with result "
                f"Func Result:{pprint(result)}\n\n"
            )
        except Exception:
            cli_logger.error(f"Error in {name}")
            raise
        return result

    return decorated_func


class IBMVPCNodeProvider(NodeProvider):
    """Node Provider for IBM VPC

    Currently, instance tagging is implemented using internal cache

    To communicate with head node from outside cluster private network,
    `use_hybrid_ips` set to True. Then, floating (external) ip allocated to
    cluster head node, while worker nodes are provisioned with private ips only.
    """

    # IBM-TODO alter tagging mechanism to support cloud tags instead of
    #  maintaining VM tags locally (file).
    def _load_tags(self):
        """
        if local tags cache (file) exists (cluster is restarting),
            cache is loaded, deleted nodes are filtered away and result
            is dumped to local cache.
        otherwise, remote head node will initialize the in memory
          and local storage tags cache with the head's cluster tags.
        """

        # local tags cache exists from former runs
        if self.tags_file.is_file():
            all_tags = json.loads(self.tags_file.read_text())
            tags = all_tags.get(self.cluster_name, {})

            # filters instances that were deleted since
            # the last time the head node was up
            for instance_id, instance_tags in tags.items():
                try:
                    self.ibm_vpc_client.get_instance(instance_id)
                    self.nodes_tags[instance_id] = instance_tags
                except ibm.ibm_cloud_sdk_core.ApiException as e:
                    cli_logger.warning(instance_id)
                    if e.message == "Instance not found":
                        logger.error(
                            f"cached instance {instance_id} not found, \
                                will be removed from cache"
                        )
            # dump in-memory cache to local cache (file).
            self.set_node_tags(None, None)

        else:
            name = socket.gethostname()  # returns the instance's (VSI) name
            logger.debug(f"Check if {name} is HEAD")
            # check if current VM is the remote head node
            if self._get_node_type(name) == NODE_KIND_HEAD:
                logger.debug(f"{name} is HEAD")
                # pylint: disable=line-too-long
                node = self.ibm_vpc_client.list_instances(name=name).get_result()[
                    "instances"
                ]
                if node:
                    logger.debug(f"{name} is node in vpc")

                    # remote head node will calculate 2 hash values below
                    ray_bootstrap_config = Path.home() / "ray_bootstrap_config.yaml"
                    config = json.loads(ray_bootstrap_config.read_text())
                    (runtime_hash, mounts_contents_hash) = hash_runtime_conf(
                        config["file_mounts"], None, config
                    )

                    # launch_hash is used to verify the head node belongs
                    # to the cluster. e.g. checked when running --restart-only
                    head_node_config = {}
                    head_node_type = config.get("head_node_type")
                    if head_node_type:
                        head_config = config["available_node_types"][head_node_type]
                        head_node_config.update(head_config["node_config"])
                    launch_hash = hash_launch_conf(head_node_config, config["auth"])

                    head_tags = {
                        TAG_RAY_NODE_KIND: NODE_KIND_HEAD,
                        TAG_RAY_NODE_NAME: name,
                        TAG_RAY_NODE_STATUS: "up-to-date",
                        TAG_RAY_CLUSTER_NAME: self.cluster_name,
                        TAG_RAY_USER_NODE_TYPE: config["head_node_type"],
                        TAG_RAY_RUNTIME_CONFIG: runtime_hash,
                        TAG_RAY_LAUNCH_CONFIG: launch_hash,
                        TAG_RAY_FILE_MOUNTS_CONTENTS: mounts_contents_hash,
                    }

                    logger.debug(f"Setting HEAD node tags {head_tags}")
                    self.set_node_tags(node[0]["id"], head_tags)

    def __init__(self, provider_config, cluster_name):
        """
        Args:
            provider_config (dict): containing the provider segment of
                the cluster's config file.initialized as an instance
                variable of the parent class, hence can accessed via self.
            cluster_name(str): value of cluster_name within the
                 cluster's config file.

        """
        NodeProvider.__init__(self, provider_config, cluster_name)

        self.lock = threading.RLock()
        self.tags_file = Path.home() / VPC_TAGS
        self.iam_api_key = provider_config["iam_api_key"]
        self.resource_group_id = provider_config["resource_group_id"]
        self.iam_endpoint = provider_config.get("iam_endpoint")
        self.region = provider_config["region"]
        self.zone = self.provider_config["availability_zone"]
        self.ibm_vpc_client = ibm.client(region=self.region)

        # vpc_tags may contain the following fields:
        # vpc_id, subnet_id, security_group_id
        self.vpc_tags = None
        self.vpc_provider = IBMVPCProvider(
            self.resource_group_id, self.region, self.cluster_name
        )
        # cache of the cluster's nodes in the format: {node_id:node_tags}
        self.nodes_tags = {}
        # Cache of starting/running/pending(below PENDING_TIMEOUT)nodes.
        # in the format{node_id:node_data}.
        self.cached_nodes = {}
        # cache of the nodes created, but not yet tagged and running.
        # in the format {node_id:time_of_creation}.
        self.pending_nodes = {}
        # ids of nodes scheduled for deletion.
        self.deleted_nodes = []

        self._load_tags()

        # if cache_stopped_nodes == true, nodes will be stopped
        #   instead of deleted to better accommodate future rise in demand
        self.cache_stopped_nodes = provider_config.get("cache_stopped_nodes", True)

    def _get_node_type(self, name):
        """returns the type of node with the specified name.

        Args:
            name (str): node's name
        Returns:
            node type (str): NODE_KIND_HEAD or NODE_KIND_WORKER.
            None: return none if node name doesn't belong to cluster's
                name scheme.
        """
        if f"{self.cluster_name}-{NODE_KIND_WORKER}" in name:
            return NODE_KIND_WORKER
        elif f"{self.cluster_name}-{NODE_KIND_HEAD}" in name:
            return NODE_KIND_HEAD

    def _get_nodes_by_tags(self, filters):
        """
        returns list of nodes' instance data who's tags are matching the specified filters.
        updates self.nodes_tags if no filters were specified or
          the only filter is the node's type.

        This function is used by non_terminate_nodes to update self.cached_nodes
          and self.nodes_tags thus abstains from using the cache. moreover,
          using the cache from this function will create a loop on cache miss.
        Args:
            filters(dict):  specified conditions to filter nodes by.
        Returns:
            list of node data for each node that matches filters.
        """

        nodes = []
        found_new_node = False
        # filters specified are a subset of {cluster_name, node_type}
        if {TAG_RAY_NODE_KIND, TAG_RAY_CLUSTER_NAME}.issuperset(set(filters.keys())):
            if self.vpc_tags:
                # if possible, search within the vpc to narrow the search scope
                result = self.ibm_vpc_client.list_instances(
                    vpc_id=self.vpc_tags["vpc_id"]
                ).get_result()
            else:
                result = self.ibm_vpc_client.list_instances().get_result()
            instances = result["instances"]
            # using pagination to acquire instances from all result pages:
            # https://cloud.ibm.com/apidocs/vpc/latest?code=python#api-pagination
            while result.get("next"):
                start = result["next"]["href"].split("start=")[1]
                result = self.ibm_vpc_client.list_instances(start=start).get_result()
                instances.extend(result["instances"])

            for instance in instances:
                kind = self._get_node_type(instance["name"])
                if kind and instance["id"] not in self.deleted_nodes:
                    if not filters or kind == filters[TAG_RAY_NODE_KIND]:
                        nodes.append(instance)
                        with self.lock:
                            node_tags = self.nodes_tags.setdefault(instance["id"], {})
                            if not node_tags:
                                found_new_node = True
                            node_tags.update(
                                {
                                    TAG_RAY_CLUSTER_NAME: self.cluster_name,
                                    TAG_RAY_NODE_KIND: kind,
                                }
                            )
            # update local tags cache
            if found_new_node:
                self.set_node_tags(None, None)

        # if more complex set of filters is specified, there's no use to search
        # the VPC for them, as this implementation stores tags locally instead
        # of tagging the instances. instead we'll search the cache nodes_tags
        else:  # match filters specified
            with self.lock:

                for node_id, node_tags in self.nodes_tags.items():
                    # skipping nodes that don't match all specified filters,
                    # i.e. not all items in filters match those in self.nodes_tags
                    if not all(item in node_tags.items() for item in filters.items()):
                        logger.debug(
                            f"specified filters: {filters} don't match node "
                            f"tags: {node_tags}"
                        )
                        continue

                    # append to result list nodes that do match the filters:
                    try:
                        # not using cache. see function docs for details.
                        nodes.append(self.ibm_vpc_client.get_instance(node_id).result)
                    except ibm.ibm_cloud_sdk_core.ApiException as e:
                        cli_logger.warning(node_id)
                        if "Instance not found" in e.message:
                            logger.error(f"failed to find vsi {node_id}, skipping")
                            continue
                        logger.error(f"failed to find instance {node_id}, raising")
                        raise e

        return nodes

    def non_terminated_nodes(self, tag_filters) -> List[str]:
        """
        returns list of ids of non terminated nodes,
        matching the specified tags. updates the nodes cache.
        NOTE: this function is called periodically by ray,
            a fact utilized to refresh caches (self.cached_nodes and self.node_tags).
        Args:
            tag_filters(dict): fields by which nodes will be filtered.
        """

        # collecting valid nodes that are either starting, running or pending.
        res_nodes = []

        found_nodes = self._get_nodes_by_tags(tag_filters)

        for node in found_nodes:

            # check if node scheduled for delete
            with self.lock:
                if node["id"] in self.deleted_nodes:
                    logger.info(f"{node['id']} scheduled for delete")
                    continue

            # delete failed nodes and skip nodes in other invalid states
            valid_statuses = ["pending", "starting", "running"]
            if node["status"] not in valid_statuses:
                with self.lock:
                    if node["status"] == "failed":
                        # since non_terminated_nodes is called periodically,
                        # prevent access until node is deleted
                        self._delete_node(node["id"], node)
                        logger.error(
                            """Encountered a failed node.
                            Region might be crowded,
                            Consider retrying later."""
                        )
                    else:
                        logger.debug(
                            f"{node['id']} status {node['status']}"
                            f" not in {valid_statuses}, skipping"
                        )
                        continue

            # remove instance hanging in pending state
            with self.lock:
                if node["id"] in self.pending_nodes:
                    if node["status"] != "running":
                        pending_time = time.time() - self.pending_nodes[node["id"]]
                        logger.debug(f"{node['id']} is pending for {pending_time}")
                        if pending_time > PENDING_TIMEOUT:
                            logger.error(
                                f"pending timeout {PENDING_TIMEOUT} reached, "
                                f"deleting instance {node['id']}"
                            )
                            self._delete_node(node["id"], node)
                            # avoid adding the node to cached_nodes
                            continue
                    else:
                        self.pending_nodes.pop(node["id"], None)

            # verify a floating ip is attached to the node
            nic_id = node["network_interfaces"][0]["id"]
            # pylint: disable=line-too-long
            res = self.ibm_vpc_client.list_instance_network_interface_floating_ips(
                node["id"], nic_id
            ).get_result()
            floating_ips = res["floating_ips"]
            if not floating_ips:
                # not adding a node that's yet/failed to
                # to get a floating ip provisioned
                continue
            else:
                node["floating_ips"] = floating_ips

            res_nodes.append(node)

        # update cached_nodes
        for node in res_nodes:
            self.cached_nodes[node["id"]] = node

        return [node["id"] for node in res_nodes]

    def is_running(self, node_id) -> bool:
        """returns whether a node is in status running"""
        with self.lock:
            node = self._get_cached_node(node_id)
            logger.debug(
                f"""node: {node_id} is_running?
                {node["status"] == "running"}"""
            )
            return node["status"] == "running"

    def is_terminated(self, node_id) -> bool:
        """returns True if a node is either not recorded or
        not in any valid status."""
        with self.lock:
            try:
                node = self._get_cached_node(node_id)
                logger.debug(
                    f"""node: {node_id} is_terminated?
                    {node["status"] not in
                    ["running", "starting", "pending"]}"""
                )
                return node["status"] not in ["running", "starting", "pending"]
            # pylint: disable=W0703
            except Exception:
                return True

    def node_tags(self, node_id) -> Dict[str, str]:
        """returns tags of specified node id"""

        with self.lock:
            return self.nodes_tags.get(node_id, {})

    def external_ip(self, node_id) -> Optional[str]:
        """returns node's public ip

        Args:
            node_id (str): id of a node within ibm VPC

        Returns:
            str: public ip of specified node
        """

        with self.lock:
            node = self._get_cached_node(node_id)
            fip = node.get("floating_ips")
            if fip:
                return fip[0]["address"]
        return None

    def internal_ip(self, node_id) -> str:
        """returns the worker's node private ip address"""
        node = self._get_cached_node(node_id)

        # if a bug ocurred, or node data was fetched before primary_ip
        # was assigned, refetch node data from cloud.
        try:
            primary_ip = node["network_interfaces"][0].get("primary_ip")["address"]
            if primary_ip is None:
                node = self._get_node(node_id)
        # pylint: disable=W0703
        except Exception:
            node = self._get_node(node_id)

        return node["network_interfaces"][0].get("primary_ip")["address"]

    def set_node_tags(self, node_id, tags) -> None:
        """
        updates local (file) tags cache.
        also updates in memory cache (self.nodes_tags) if node_id and tags
        are specified.

        Args:
            node_id(str): node unique id.
            tags(dict): specified conditions by which nodes will be filtered.
        """

        with self.lock:
            # update in-memory cache
            if node_id and tags:
                node_tags = self.nodes_tags.setdefault(node_id, {})
                node_tags.update(tags)

            # dump in-memory cache to file.
            all_tags = {}
            if self.tags_file.is_file():
                all_tags = json.loads(self.tags_file.read_text())
            all_tags[self.cluster_name] = self.nodes_tags
            self.tags_file.write_text(json.dumps(all_tags))

    def _get_instance_data(self, name):
        """Returns instance (node) information matching the specified name"""

        instances_data = self.ibm_vpc_client.list_instances(name=name).get_result()
        if instances_data["instances"]:
            return instances_data["instances"][0]
        return None

    def _create_instance(self, name, base_config):
        """
        Creates a new VM instance with the specified name,
        based on the provided base_config configuration dictionary
        Args:
            name(str): name of the instance.
            base_config(dict): specific node relevant data.
            node type segment of the cluster's config file,
             e.g. ray_head_default.
        """

        logger.info(f"Creating new VM instance {name}")

        security_group_identity_model = {"id": self.vpc_tags["security_group_id"]}
        subnet_identity_model = {"id": self.vpc_tags["subnet_id"]}
        primary_network_interface = {
            "name": "eth0",
            "subnet": subnet_identity_model,
            "security_groups": [security_group_identity_model],
        }

        boot_volume_profile = {
            "capacity": base_config.get("boot_volume_capacity", 100),
            "name": f"boot-volume-{uuid4().hex[:4]}",
            "profile": {
                "name": base_config.get("volume_tier_name", VOLUME_TIER_NAME_DEFAULT)
            },
        }

        boot_volume_attachment = {
            "delete_volume_on_instance_delete": True,
            "volume": boot_volume_profile,
        }

        key_identity_model = {"id": base_config["key_id"]}
        profile_name = base_config.get("instance_profile_name")
        if not profile_name:
            raise Exception(
                """Failed to detect 'instance_profile_name'
                key in cluster config file"""
            )

        instance_prototype = {}
        instance_prototype["name"] = name
        instance_prototype["keys"] = [key_identity_model]
        instance_prototype["profile"] = {"name": profile_name}
        instance_prototype["resource_group"] = {"id": self.resource_group_id}
        instance_prototype["vpc"] = {"id": self.vpc_tags["vpc_id"]}
        instance_prototype["image"] = {"id": base_config["image_id"]}

        instance_prototype["zone"] = {"name": self.zone}
        instance_prototype["boot_volume_attachment"] = boot_volume_attachment
        # pylint: disable=line-too-long
        instance_prototype["primary_network_interface"] = primary_network_interface

        try:
            with self.lock:
                resp = self.ibm_vpc_client.create_instance(instance_prototype)
        except ibm.ibm_cloud_sdk_core.ApiException as e:
            if e.code == 400 and "already exists" in e.message:
                return self._get_instance_data(name)
            elif e.code == 400 and "over quota" in e.message:
                cli_logger.error(f"Create VM instance {name} failed due to quota limit")
            else:
                cli_logger.error(
                    f"""Create VM instance for {name}
                    failed with status code {str(e.code)}
                    .\nFailed instance prototype:\n"""
                )
                pprint(instance_prototype)
            raise e

        logger.info(f"VM instance {name} created successfully")
        return resp.result

    def _create_floating_ip(self, base_config):
        """returns unbound floating IP address.
        Creates a new ip if none were found in the config file.
        Args:
            base_config(dict): specific node relevant data.
            node type segment of the cluster's config file,
            e.g. ray_head_default.
        """

        if base_config.get("head_ip"):
            # pylint: disable=line-too-long
            for ip in self.ibm_vpc_client.list_floating_ips().get_result()[
                "floating_ips"
            ]:
                if ip["address"] == base_config["head_ip"]:
                    return ip

        floating_ip_name = f"{RAY_RECYCLABLE}-{uuid4().hex[:4]}"
        # create a new floating ip
        logger.debug(f"Creating floating IP {floating_ip_name}")
        floating_ip_prototype = {}
        floating_ip_prototype["name"] = floating_ip_name
        floating_ip_prototype["zone"] = {"name": self.zone}
        floating_ip_prototype["resource_group"] = {"id": self.resource_group_id}
        response = self.ibm_vpc_client.create_floating_ip(floating_ip_prototype)
        floating_ip_data = response.result

        return floating_ip_data

    def _attach_floating_ip(self, instance, fip_data):
        """
        attach a floating ip to the network interface of an instance
        Args:
            instance(dict): extensive data of a node.
            fip_data(dict): floating ip data.
        """

        fip = fip_data["address"]
        fip_id = fip_data["id"]

        logger.debug(
            f"""Attaching floating IP {fip} to
            VM instance {instance["id"]}"""
        )

        # check if floating ip is not attached yet
        inst_p_nic = instance["primary_network_interface"]

        if inst_p_nic["primary_ip"] and inst_p_nic["id"] == fip_id:
            # floating ip already attached. do nothing
            logger.debug(f"Floating IP {fip} already attached to eth0")
        else:
            # attach floating ip
            self.ibm_vpc_client.add_instance_network_interface_floating_ip(
                instance["id"], instance["network_interfaces"][0]["id"], fip_id
            )

    def _stopped_nodes(self, tags):
        """
        returns either stopped head or stopped worker nodes,
            belonging to this cluster

        requires: TAG_RAY_NODE_KIND key in tags.
        Args:
            tags(dict): set of conditions nodes will be filtered by.
        Returns:
            list of stopped nodes, where each item contains node data.
        """

        # pylint: disable=C0206 W0622
        # the following subset 'filter' is a common denominator for all
        # nodes belonging to the cluster.
        filter = {
            TAG_RAY_CLUSTER_NAME: self.cluster_name,
            TAG_RAY_NODE_KIND: tags[TAG_RAY_NODE_KIND],
        }
        nodes = []
        # filter stopped nodes
        with self.lock:
            for node_id in self.nodes_tags:
                try:
                    node_tags = self.nodes_tags[node_id]
                    if all(item in node_tags.items() for item in filter.items()):
                        node = self.ibm_vpc_client.get_instance(node_id).result
                        state = node["status"]
                        if state in ["stopped", "stopping"]:
                            nodes.append(node)
                except Exception as e:
                    cli_logger.warning(
                        f"node: {node_id} from cache: 'nodes_tags' not found in VPC"
                    )
                    if (
                        isinstance(e, ibm.ibm_cloud_sdk_core.ApiException)
                        and "Instance not found" in e.message
                    ):
                        continue
                    raise e
            return nodes

    def _create_node(self, base_config, tags):
        """
        returns dict {instance_id:instance_data} of newly created node.
        updates tags cache.
        creates a node in the following format:
            ray-{cluster_name}-{node_type}-{uuid}

        Args:
            base_config(dict): specific node relevant data.
                node type segment of the cluster's config file,
                e.g. ray_head_default.
            tags(dict): set of conditions nodes will be filtered by.
        """

        name_tag = tags[TAG_RAY_NODE_NAME]
        if len(name_tag) > INSTANCE_NAME_MAX_LEN - INSTANCE_NAME_UUID_LEN - 1:
            logger.error(
                f"""node name: {name_tag} is longer than
                {INSTANCE_NAME_MAX_LEN - INSTANCE_NAME_UUID_LEN - 1}
                characters"""
            )
            raise Exception("Invalid node name: length check failed")

        # IBM VPC VSI pattern requirement
        pattern = re.compile("^([a-z]|[a-z][-a-z0-9]*[a-z0-9])$")
        res = pattern.match(name_tag)

        if not res:
            logger.error(
                f"""node name {name_tag} doesn't match the naming pattern
                 `[a-z]|[a-z][-a-z0-9]*[a-z0-9]` for IBM VPC instance """
            )
            raise Exception(
                "Invalid node name: pattern check for IBM VPC instance failed"
            )

        # append instance name with uuid
        name = "{name_tag}-{uuid}".format(
            name_tag=name_tag, uuid=uuid4().hex[:INSTANCE_NAME_UUID_LEN]
        )

        # create instance in vpc
        instance = self._create_instance(name, base_config)

        # record creation time. used to discover hanging nodes.
        with self.lock:
            self.pending_nodes[instance["id"]] = time.time()

        tags[TAG_RAY_CLUSTER_NAME] = self.cluster_name
        tags[TAG_RAY_NODE_NAME] = name
        self.set_node_tags(instance["id"], tags)

        # create and attach a floating ip to the node
        fip_data = self._create_floating_ip(base_config)
        self._attach_floating_ip(instance, fip_data)

        return {instance["id"]: instance}

    def create_node(self, base_config, tags, count) -> Dict[str, Any]:
        """
        returns dict of {instance_id:instance_data} of nodes.
        creates 'count' number of nodes.
        if enabled in base_config,
        tries to re-run stopped nodes before creation of new instances.

        Args:
            base_config(dict): specific node relevant data.
                node type segment of the cluster's config file,
                e.g. ray_head_default.
                a template shared by all nodes
                when creating multiple nodes (count>1).
            tags(dict): set of conditions nodes will be filtered by.
            count(int): number of nodes to create.

        """

        # Create a VPC when creating a head node
        # or fetch (for any node type) when restarting Ray
        if not self.vpc_tags:
            self.vpc_tags = self.vpc_provider.create_or_fetch_vpc(
                self.region, self.zone
            )

        stopped_nodes_dict = {}
        futures = []

        # Try to reuse previously stopped nodes with compatible configs
        if self.cache_stopped_nodes:
            stopped_nodes = self._stopped_nodes(tags)
            stopped_nodes_ids = [n["id"] for n in stopped_nodes]
            stopped_nodes_dict = {n["id"]: n for n in stopped_nodes}

            if stopped_nodes:
                cli_logger.print(
                    f"Reusing nodes {stopped_nodes_ids}. "
                    "To disable reuse, set `cache_stopped_nodes: False` "
                    "under `provider` in the cluster configuration."
                )

            for node in stopped_nodes:
                logger.info(f"Starting instance {node['id']}")
                self.ibm_vpc_client.create_instance_action(node["id"], "start")
            # added delay due to a race condition.
            # discussed at the final comment of this function.
            time.sleep(1)

            # update tags of stopped nodes
            for node_id in stopped_nodes_ids:
                self.set_node_tags(node_id, tags)

            count -= len(stopped_nodes_ids)

        created_nodes_dict = {}

        # create multiple instances concurrently
        if count:
            with cf.ThreadPoolExecutor(count) as ex:
                for _ in range(count):
                    futures.append(ex.submit(self._create_node, base_config, tags))

            for future in cf.as_completed(futures):
                created_node = future.result()
                created_nodes_dict.update(created_node)

        all_created_nodes = stopped_nodes_dict
        all_created_nodes.update(created_nodes_dict)

        # this sleep is required due to race condition with non_terminated_nodes
        # called in separate thread by autoscaler. not a lost as anyway the vsi
        # operating system takes time to start. can be removed after
        # https://github.com/ray-project/ray/issues/28150 resolved
        time.sleep(5)

        return all_created_nodes

    def _delete_node(self, node_id, node_data=None):
        """deletes a node and removes it from the caches.
        if it's a (not failed) head node, deletes the vpc.

        if node_data is specified uses it instead of fetching it
        by using the node_id.

        Args:
            node_id (str): id of the node.
            node_data (dict): node data received by the api: get_instance()

        Raises:
            ApiException: raises VPC api exception if deletion failed
              for any reason other then "resource not found"
        """

        logger.info("Deleting VM instance {}".format(node_id))
        # avoid fetching data if specified. Skipping this optimization
        # could cause a loop if node is 'failed' since _get_cached_node()
        # would eventually invoke non_terminated_nodes()
        # which in turn will invoke this function.
        node = node_data if node_data else self._get_cached_node(node_id)

        # get vpc_id to delete if deleting head node which isn't a failed node
        # pylint: disable=line-too-long
        vpc_id_to_delete = None
        if (
            self._get_node_type(node["name"]) == NODE_KIND_HEAD
            and node["status"] != "failed"
        ):
            if self.vpc_tags:
                # if it was initialized it's sure to contain `vpc_id`
                vpc_id_to_delete = self.vpc_tags["vpc_id"]
            else:
                vpc_id_to_delete = self.ibm_vpc_client.get_instance(
                    node_id
                ).get_result()["vpc"]["id"]

            # if the request is executed by the remote head like in `sky autostop --down`
            # , in contrast to `sky down`, use cloud functions to delete the VPC
            # with its associated resources.
            hostname = socket.gethostname()  # returns the instance's (VSI) name
            if self._get_node_type(hostname) == NODE_KIND_HEAD:
                self.vpc_provider.remote_cluster_removal(vpc_id_to_delete, self.region)

        try:
            self.ibm_vpc_client.delete_instance(node_id)

            with self.lock:
                # update node deletion in all caches
                self.nodes_tags.pop(node_id, None)
                self.pending_nodes.pop(node_id, None)
                self.deleted_nodes.append(node_id)
                self.cached_nodes.pop(node_id, None)

                # calling set_node_tags with None
                # will dump self.nodes_tags cache to file
                self.set_node_tags(None, None)

            # delete all ips created by this module
            floating_ips = node.get("floating_ips", [])
            for ip in floating_ips:
                if ip["name"].startswith(RAY_RECYCLABLE):
                    self.ibm_vpc_client.delete_floating_ip(ip["id"])

        # catch exceptions from instance/ip deletion
        except ibm.ibm_cloud_sdk_core.ApiException as e:
            # allow 'resource not found' exceptions to pass through
            # suggesting the resource was already removed
            if e.code == 404:
                pass
            else:
                raise e

        # Terminate VPC if node deleted is a head node
        if vpc_id_to_delete:
            if self.poll_instance_deleted(node_id):
                self.vpc_provider.delete_vpc(vpc_id_to_delete, self.region)

    def terminate_nodes(self, node_ids):

        if not node_ids:
            return

        futures = []
        with cf.ThreadPoolExecutor(len(node_ids)) as ex:
            for node_id in node_ids:
                logger.debug(f"NodeProvider Terminating node {node_id}")
                futures.append(ex.submit(self.terminate_node, node_id))

        for future in cf.as_completed(futures):
            future.result()

    @log_in_out
    def terminate_node(self, node_id):
        """Deletes the VM instance and the associated volume.
        if cache_stopped_nodes is set to true in the cluster config file,
        nodes are stopped instead."""

        try:
            if self.cache_stopped_nodes:
                cli_logger.print(
                    f"Stopping instance {node_id}. To terminate instead, "
                    "set `cache_stopped_nodes: False` "
                    "under `provider` in the cluster configuration"
                )

                self.ibm_vpc_client.create_instance_action(node_id, "stop")
            else:
                cli_logger.print(f"Terminating instance {node_id}")
                self._delete_node(node_id)

        except ibm.ibm_cloud_sdk_core.ApiException as e:
            if e.code == 404:
                pass
            else:
                raise e

    def _get_node(self, node_id):
        """Refresh and get info for this node, updating the cache."""
        err_msg = f"failed to get instance with id {node_id}"
        # updates self.cached_nodes and nodes_tags
        self.non_terminated_nodes({})

        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]
        else:
            logger.error(err_msg)
            raise Exception(err_msg)

    def _get_cached_node(self, node_id):
        """Return node info from cache if possible, otherwise fetches it."""
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]

        return self._get_node(node_id)

    def poll_instance_deleted(self, instance_id):
        tries = 20  # waits up to 200sec with 10 sec interval
        sleep_interval = 10
        while tries:
            try:
                self.ibm_vpc_client.get_instance(instance_id).get_result()
            except ibm.ibm_cloud_sdk_core.ApiException:
                logger.debug(f"Deleted VM instance with id: {instance_id}")
                return True

            tries -= 1
            time.sleep(sleep_interval)
        logger.Error("\nFailed to delete instance within expected time frame\n")
        return False

    @staticmethod
    def bootstrap_config(cluster_config) -> Dict[str, Any]:
        return cluster_config
