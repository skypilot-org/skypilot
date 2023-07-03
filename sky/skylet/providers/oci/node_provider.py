"""OCI Node Provider.

Node provider is called by the Ray Autoscaler to provision new compute
resources (head / worker nodes).

To show debug messages, export SKYPILOT_DEBUG=1 

History:
 - Hysun He (hysun.he@oracle.com) @ Apr, 2023: Initial implementation
 
"""

import logging
import time
import threading
import copy

from datetime import datetime
from sky.skylet.providers.oci.config import oci_conf
from sky.skylet.providers.oci import utils
from sky.skylet.providers.oci.query_helper import oci_query_helper
from sky.adaptors import oci as oci_adaptor

from ray.autoscaler.node_provider import NodeProvider
from ray.autoscaler.tags import (
    TAG_RAY_CLUSTER_NAME,
    TAG_RAY_NODE_KIND,
    TAG_RAY_USER_NODE_TYPE,
    TAG_RAY_LAUNCH_CONFIG,
)

logger = logging.getLogger(__name__)


def synchronized(f):

    def wrapper(self, *args, **kwargs):
        self.lock.acquire()
        try:
            return f(self, *args, **kwargs)
        finally:
            self.lock.release()

    return wrapper


class OCINodeProvider(NodeProvider):
    """Node Provider for OracleCloud (OCI)."""

    def __init__(self, provider_config, cluster_name):
        NodeProvider.__init__(self, provider_config, cluster_name)
        self.lock = threading.RLock()
        self.cached_nodes = {}
        self.cache_stopped_nodes = provider_config.get("cache_stopped_nodes",
                                                       True)
        self.region = provider_config["region"]

        # Do a read-ahead cache loading to improve performance.
        self._get_filtered_nodes({})

    @synchronized
    def _get_filtered_nodes(self, tag_filters, force=False):
        # Make sure the cluster_name is always an criterion
        tag_filters = {**tag_filters, TAG_RAY_CLUSTER_NAME: self.cluster_name}

        return_nodes = {}
        if not force:
            # Query cache first to reduce API call.
            cache_hit = False
            for k, node in self.cached_nodes.items():
                tags = node["tags"]
                unmatched_tags = [
                    k for k, v in tag_filters.items()
                    if k not in tags or v != tags[k]
                ]
                if len(unmatched_tags) == 0:
                    return_nodes[k] = node
                    cache_hit |= True

            if cache_hit:
                return return_nodes

        insts = oci_query_helper.query_instances_by_tags(
            tag_filters, self.region)
        for inst in insts:
            inst_id = inst.identifier
            if inst_id in self.cached_nodes:
                del self.cached_nodes[inst_id]

            item = self.get_inst_obj({
                "inst_id": inst_id,
                "ad": inst.availability_domain,
                "compartment": inst.compartment_id,
                "lifecycle_state": inst.lifecycle_state,
                "oci_tags": inst.freeform_tags,
            })
            return_nodes[inst_id] = item
            self.cached_nodes[inst_id] = item

        return return_nodes

    @utils.debug_enabled(logger=logger)
    def non_terminated_nodes(self, tag_filters):
        """Return a list of node ids filtered by the specified tags dict.

        This list must not include terminated nodes. For performance reasons,
        providers are allowed to cache the result of a call to
        non_terminated_nodes() to serve single-node queries
        (e.g. is_running(node_id)). This means that non_terminated_nodes()
        must be called again to refresh results.
        """
        VALIDITY_TAGS = [
            TAG_RAY_CLUSTER_NAME,
            TAG_RAY_NODE_KIND,
            TAG_RAY_USER_NODE_TYPE,
            TAG_RAY_LAUNCH_CONFIG,
        ]
        filters = {
            tag: tag_filters[tag] for tag in VALIDITY_TAGS if tag in tag_filters
        }

        nodes = self._get_filtered_nodes(tag_filters=filters)
        return [k for k, v in nodes.items() if v["status"] == "RUNNING"]

    @utils.debug_enabled(logger=logger)
    def is_running(self, node_id):
        """Return whether the specified node is running."""
        node = self._get_cached_node(node_id=node_id)
        check_result = node is None or node["status"] == "RUNNING"

        return check_result

    @utils.debug_enabled(logger=logger)
    def is_terminated(self, node_id):
        """Return whether the specified node is terminated."""
        node = self._get_cached_node(node_id=node_id)
        check_result = ((node is None) or (node["status"] == "TERMINATED") or
                        (node["status"] == "TERMINATING"))

        return check_result

    @utils.debug_enabled(logger=logger)
    def node_tags(self, node_id):
        return self.cached_nodes[node_id]["tags"]

    @utils.debug_enabled(logger=logger)
    def external_ip(self, node_id):
        """Returns the external ip of the given node."""
        return self._get_cached_node(node_id=node_id)["external_ip"]

    @utils.debug_enabled(logger=logger)
    def internal_ip(self, node_id):
        """Returns the internal ip (Ray ip) of the given node."""
        return self._get_cached_node(node_id=node_id)["internal_ip"]

    @synchronized
    @utils.debug_enabled(logger=logger)
    def create_node(self, node_config, tags, count):
        """Creates a number of nodes within the namespace."""
        start_time = round(time.time() * 1000)
        starting_insts = []
        # Check first if it neccessary to create new nodes / start stopped nodes
        VALIDITY_TAGS = [
            TAG_RAY_CLUSTER_NAME,
            TAG_RAY_NODE_KIND,
            TAG_RAY_USER_NODE_TYPE,
        ]
        filters = {tag: tags[tag] for tag in VALIDITY_TAGS if tag in tags}

        # Starting stopped nodes if cache_stopped_nodes=True
        if self.cache_stopped_nodes:
            logger.debug("Checking existing stopped nodes.")

            filters_with_launch_config = copy.copy(filters)
            if TAG_RAY_LAUNCH_CONFIG in tags:
                filters_with_launch_config[TAG_RAY_LAUNCH_CONFIG] = tags[
                    TAG_RAY_LAUNCH_CONFIG]

            nodes_matching_launch_config = self.stopped_nodes(
                filters_with_launch_config)
            logger.debug(f"Found stopped nodes (with same launch config): "
                         f"{len(nodes_matching_launch_config)}")

            reuse_nodes = []
            if len(nodes_matching_launch_config) >= count:
                reuse_nodes = nodes_matching_launch_config[:count]
            else:
                nodes_all = self.stopped_nodes(filters)
                logger.debug(f"Found stopped nodes (regardless launch config): "
                             f"{len(nodes_all)}")
                nodes_matching_launch_config_ids = [
                    n["id"] for n in nodes_matching_launch_config
                ]
                nodes_non_matching_launch_config = [
                    n for n in nodes_all
                    if n["id"] not in nodes_matching_launch_config_ids
                ]
                reuse_nodes = (nodes_matching_launch_config +
                               nodes_non_matching_launch_config)
                reuse_nodes = reuse_nodes[:count]

            logger.info(
                f"Reusing nodes {len(reuse_nodes)}: {list(reuse_nodes)}. "
                "To disable reuse, set `cache_stopped_nodes: False` "
                "under `provider` in the cluster configuration.",)

            for reuse_node in reuse_nodes:
                if reuse_node["status"] == "STOPPING":
                    get_instance_response = oci_adaptor.get_core_client(
                        self.region, oci_conf.get_profile()).get_instance(
                            instance_id=reuse_node["id"])
                    oci_adaptor.get_oci().wait_until(
                        oci_adaptor.get_core_client(self.region,
                                                    oci_conf.get_profile()),
                        get_instance_response,
                        "lifecycle_state",
                        "STOPPED",
                    )

            start_time1 = round(time.time() * 1000)
            for matched_node in reuse_nodes:
                matched_node_id = matched_node["id"]
                instance_action_response = oci_adaptor.get_core_client(
                    self.region, oci_conf.get_profile()).instance_action(
                        instance_id=matched_node_id, action="START")

                starting_inst = instance_action_response.data
                starting_insts.append({
                    "inst_id": starting_inst.id,
                    "ad": starting_inst.availability_domain,
                    "compartment": starting_inst.compartment_id,
                    "lifecycle_state": starting_inst.lifecycle_state,
                    "oci_tags": starting_inst.freeform_tags,
                })
            count -= len(reuse_nodes)

            launch_stopped_time = round(time.time() * 1000) - start_time1
            logger.debug(
                "Time elapsed(Launch stopped): {0} milli-seconds.".format(
                    launch_stopped_time))
        # end if self.cache_stopped_nodes:...

        # Let's create additional new nodes (if neccessary)
        if count > 0:
            compartment = oci_query_helper.find_compartment(self.region)
            vcn = oci_query_helper.find_create_vcn_subnet(self.region)
            if vcn is None:
                raise RuntimeError("VcnSubnetNotFound Error!")

            ocpu_count = 0
            vcpu_str = node_config["VCPUs"]
            instance_type_str = node_config["InstanceType"]

            if vcpu_str is not None and vcpu_str != "None":
                if instance_type_str.startswith(f"{oci_conf.VM_PREFIX}.A"):
                    # For ARM cpu, 1*ocpu = 1*vcpu
                    ocpu_count = round(float(vcpu_str))
                else:
                    # For Intel / AMD cpu, 1*ocpu = 2*vcpu
                    ocpu_count = round(float(vcpu_str) / 2)
            ocpu_count = 1 if (ocpu_count > 0 and
                               ocpu_count < 1) else ocpu_count

            machine_shape_config = None
            if ocpu_count > 0:
                mem = node_config["MemoryInGbs"]
                if mem is not None and mem != "None":
                    machine_shape_config = (oci_adaptor.get_oci().core.models.
                                            LaunchInstanceShapeConfigDetails(
                                                ocpus=ocpu_count,
                                                memory_in_gbs=mem))
                else:
                    machine_shape_config = (oci_adaptor.get_oci().core.models.
                                            LaunchInstanceShapeConfigDetails(
                                                ocpus=ocpu_count))

            preempitible_config = (oci_adaptor.get_oci(
            ).core.models.PreemptibleInstanceConfigDetails(
                preemption_action=oci_adaptor.get_oci().core.models.
                TerminatePreemptionAction(type="TERMINATE",
                                          preserve_boot_volume=False))
                                   if node_config["Preemptible"] else None)

            logger.debug(f"Shape: {instance_type_str}, ocpu: {ocpu_count}")
            logger.debug(f"Shape config is {machine_shape_config}")
            logger.debug(f"Spot config is {preempitible_config}")

            vm_tags = {
                **tags,
                TAG_RAY_CLUSTER_NAME: self.cluster_name,
                "sky_spot_flag": str(node_config["Preemptible"]).lower(),
            }
            # Use UTC time so that header & worker nodes use same rule
            batch_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            node_type = tags[TAG_RAY_NODE_KIND]

            oci_query_helper.subscribe_image(
                compartment_id=compartment,
                listing_id=node_config["AppCatalogListingId"],
                resource_version=node_config["ResourceVersion"],
                region=self.region,
            )

            start_time1 = round(time.time() * 1000)
            for seq in range(1, count + 1):
                launch_instance_response = oci_adaptor.get_core_client(
                    self.region, oci_conf.get_profile()
                ).launch_instance(launch_instance_details=oci_adaptor.get_oci(
                ).core.models.LaunchInstanceDetails(
                    availability_domain=node_config["AvailabilityDomain"],
                    compartment_id=compartment,
                    shape=instance_type_str,
                    display_name=
                    f"{self.cluster_name}_{node_type}_{batch_id}_{seq}",
                    freeform_tags=vm_tags,
                    metadata={
                        "ssh_authorized_keys": node_config["AuthorizedKey"]
                    },
                    source_details=oci_adaptor.get_oci(
                    ).core.models.InstanceSourceViaImageDetails(
                        source_type="image",
                        image_id=node_config["ImageId"],
                        boot_volume_size_in_gbs=node_config["BootVolumeSize"],
                        boot_volume_vpus_per_gb=int(
                            node_config["BootVolumePerf"]),
                    ),
                    create_vnic_details=oci_adaptor.get_oci(
                    ).core.models.CreateVnicDetails(
                        assign_public_ip=True,
                        subnet_id=vcn,
                    ),
                    shape_config=machine_shape_config,
                    preemptible_instance_config=preempitible_config,
                ))

                new_inst = launch_instance_response.data
                starting_insts.append({
                    "inst_id": new_inst.id,
                    "ad": new_inst.availability_domain,
                    "compartment": new_inst.compartment_id,
                    "lifecycle_state": new_inst.lifecycle_state,
                    "oci_tags": new_inst.freeform_tags,
                })
            # end for loop

            launch_new_time = round(time.time() * 1000) - start_time1
            logger.debug("Time elapsed(Launch): {0} milli-seconds.".format(
                launch_new_time))
        # end if count > 0:...

        for ninst in starting_insts:
            # Waiting for the instance to be RUNNING state
            get_instance_response = oci_adaptor.get_core_client(
                self.region, oci_conf.get_profile()).get_instance(
                    instance_id=ninst["inst_id"])
            oci_adaptor.get_oci().wait_until(
                oci_adaptor.get_core_client(self.region,
                                            oci_conf.get_profile()),
                get_instance_response,
                "lifecycle_state",
                "RUNNING",
            )
            ninst["lifecycle_state"] = "RUNNING"
            self.cached_nodes[ninst["inst_id"]] = self.get_inst_obj(ninst)

        total_time = round(time.time() * 1000) - start_time
        logger.debug(
            "Total time elapsed: {0} milli-seconds.".format(total_time))

    def get_inst_obj(self, inst_info):
        list_vnic_attachments_response = oci_adaptor.get_core_client(
            self.region, oci_conf.get_profile()).list_vnic_attachments(
                availability_domain=inst_info["ad"],
                compartment_id=inst_info["compartment"],
                instance_id=inst_info["inst_id"],
            )

        vnic = list_vnic_attachments_response.data[0]
        get_vnic_response = (oci_adaptor.get_net_client(
            self.region,
            oci_conf.get_profile()).get_vnic(vnic_id=vnic.vnic_id).data)

        internal_ip = get_vnic_response.private_ip
        external_ip = get_vnic_response.public_ip
        if external_ip is None:
            external_ip = internal_ip

        return {
            "id": inst_info["inst_id"],
            "external_ip": external_ip,
            "internal_ip": internal_ip,
            "tags": inst_info["oci_tags"],
            "status": inst_info["lifecycle_state"],
        }

    @synchronized
    @utils.debug_enabled(logger=logger)
    def set_node_tags(self, node_id, tags):
        existing_tags = self._get_cached_node(node_id)["tags"]
        combined_tags = dict(existing_tags, **tags)

        self.cached_nodes[node_id]["tags"] = combined_tags
        retry_count = 0
        while retry_count < oci_conf.MAX_RETRY_COUNT:
            try:
                oci_adaptor.get_core_client(
                    self.region, oci_conf.get_profile()).update_instance(
                        instance_id=node_id,
                        update_instance_details=oci_adaptor.get_oci().core.
                        models.UpdateInstanceDetails(
                            freeform_tags=combined_tags),
                    )
                logger.info(f"Tags are well set for node {node_id}")
                break
            except Exception as e:
                retry_count = retry_count + 1
                wait_seconds = oci_conf.RETRY_INTERVAL_BASE_SECONDS * retry_count
                logger.warn(
                    f"Not ready yet, wait {wait_seconds} seconds & retry!")
                logger.warn(f"Exception message is {str(e)}")
                time.sleep(wait_seconds)

    @synchronized
    def terminate_node(self, node_id):
        """Terminates the specified node."""
        logger.info(f"terminate_node {node_id}...")
        node = self._get_cached_node(node_id)
        if node is None:
            logger.info(f"The node is not existed: {node_id}..")
            return  # Node not exists yet.

        logger.debug(f"sky_spot_flag: {node['tags']['sky_spot_flag']}")
        preemptibleFlag = (True if node and
                           (str(node["tags"]["sky_spot_flag"]) == "true") else
                           False)

        if self.cache_stopped_nodes and not preemptibleFlag:
            logger.info(f"Stopping instance {node_id}"
                        "(to fully terminate instead, "
                        "set `cache_stopped_nodes: False` "
                        "under `provider` in the cluster configuration)")
            instance_action_response = oci_adaptor.get_core_client(
                self.region,
                oci_conf.get_profile()).instance_action(instance_id=node_id,
                                                        action="STOP")
            logger.info(
                f"Stopped the instance {instance_action_response.data.id}")
            if node_id in self.cached_nodes:
                self.cached_nodes[node_id]["status"] = "STOPPED"
            state_word = "Stopped"
        else:
            terminate_instance_response = oci_adaptor.get_core_client(
                self.region, oci_conf.get_profile()).terminate_instance(node_id)
            logger.debug(terminate_instance_response.data)
            if node_id in self.cached_nodes:
                del self.cached_nodes[node_id]
            state_word = "Terminated"

        logger.info(
            f"{state_word} {node_id} w/ sky_spot_flag: {preemptibleFlag}.")

    def _get_node(self, node_id):
        self._get_filtered_nodes({},
                                 force=True)  # All except for those terminated.
        return self.cached_nodes.get(node_id, None)

    def _get_cached_node(self, node_id):
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]
        return self._get_node(node_id=node_id)

    def stopped_nodes(self, tag_filters):
        """Return a list of stopped nodes filtered by the specified tags dict."""
        nodes = self._get_filtered_nodes(tag_filters=tag_filters, force=True)
        return [
            v for _, v in nodes.items()
            if v["status"] in ("STOPPED", "STOPPING")
        ]

    def running_nodes(self, tag_filters):
        """Return a list of running node ids filtered by the specified tags dict."""
        nodes = self._get_filtered_nodes(tag_filters=tag_filters)
        return [k for k, v in nodes.items() if v["status"] == "RUNNING"]
