import logging
import time
import copy
from functools import wraps
from threading import RLock
from typing import Dict, List

import googleapiclient

from sky.skylet.providers.gcp.config import (
    bootstrap_gcp,
    construct_clients_from_provider_config,
    get_node_type,
)

from ray.autoscaler.tags import (
    TAG_RAY_LAUNCH_CONFIG,
    TAG_RAY_NODE_KIND,
    TAG_RAY_USER_NODE_TYPE,
)
from ray.autoscaler._private.cli_logger import cf, cli_logger


# The logic has been abstracted away here to allow for different GCP resources
# (API endpoints), which can differ widely, making it impossible to use
# the same logic for everything.
from sky.skylet.providers.gcp.node import (  # noqa
    GCPCompute,
    GCPNode,
    GCPNodeType,
    GCPResource,
    GCPTPU,
    # Added by SkyPilot
    INSTANCE_NAME_MAX_LEN,
    INSTANCE_NAME_UUID_LEN,
    MAX_POLLS_STOP,
    POLL_INTERVAL,
)
from ray.autoscaler.node_provider import NodeProvider

logger = logging.getLogger(__name__)


def _retry(method, max_tries=5, backoff_s=1):
    """Retry decorator for methods of GCPNodeProvider.

    Upon catching BrokenPipeError, API clients are rebuilt and
    decorated methods are retried.

    Work-around for https://github.com/ray-project/ray/issues/16072.
    Based on https://github.com/kubeflow/pipelines/pull/5250/files.
    """

    @wraps(method)
    def method_with_retries(self, *args, **kwargs):
        try_count = 0
        while try_count < max_tries:
            try:
                return method(self, *args, **kwargs)
            except BrokenPipeError:
                logger.warning("Caught a BrokenPipeError. Retrying.")
                try_count += 1
                if try_count < max_tries:
                    self._construct_clients()
                    time.sleep(backoff_s)
                else:
                    raise

    return method_with_retries


class GCPNodeProvider(NodeProvider):
    def __init__(self, provider_config: dict, cluster_name: str):
        NodeProvider.__init__(self, provider_config, cluster_name)
        self.lock = RLock()
        self._construct_clients()

        # Cache of node objects from the last nodes() call. This avoids
        # excessive DescribeInstances requests.
        self.cached_nodes: Dict[str, GCPNode] = {}
        self.cache_stopped_nodes = provider_config.get("cache_stopped_nodes", True)

    def _construct_clients(self):
        _, _, compute, tpu = construct_clients_from_provider_config(
            self.provider_config
        )

        # Dict of different resources provided by GCP.
        # At this moment - Compute and TPUs
        self.resources: Dict[GCPNodeType, GCPResource] = {}

        # Compute is always required
        self.resources[GCPNodeType.COMPUTE] = GCPCompute(
            compute,
            self.provider_config["project_id"],
            self.provider_config["availability_zone"],
            self.cluster_name,
        )

        # if there are no TPU nodes defined in config, tpu will be None.
        if tpu is not None:
            self.resources[GCPNodeType.TPU] = GCPTPU(
                tpu,
                self.provider_config["project_id"],
                self.provider_config["availability_zone"],
                self.cluster_name,
            )

    def _get_resource_depending_on_node_name(self, node_name: str) -> GCPResource:
        """Return the resource responsible for the node, based on node_name.

        This expects the name to be in format '[NAME]-[UUID]-[TYPE]',
        where [TYPE] is either 'compute' or 'tpu' (see ``GCPNodeType``).
        """
        return self.resources[GCPNodeType.name_to_type(node_name)]

    @_retry
    def non_terminated_nodes(self, tag_filters: dict):
        with self.lock:
            instances = []

            for resource in self.resources.values():
                node_instances = resource.list_instances(tag_filters)
                instances += node_instances

            # Note: All the operations use "name" as the unique instance id
            self.cached_nodes = {i["name"]: i for i in instances}
            return [i["name"] for i in instances]

    def is_running(self, node_id: str):
        with self.lock:
            node = self._get_cached_node(node_id)
            return node.is_running()

    def is_terminated(self, node_id: str):
        with self.lock:
            node = self._get_cached_node(node_id)
            return node.is_terminated()

    def node_tags(self, node_id: str):
        with self.lock:
            node = self._get_cached_node(node_id)
            return node.get_labels()

    @_retry
    def set_node_tags(self, node_id: str, tags: dict):
        with self.lock:
            labels = tags
            node = self._get_node(node_id)

            resource = self._get_resource_depending_on_node_name(node_id)

            result = resource.set_labels(node=node, labels=labels)

            return result

    def external_ip(self, node_id: str):
        with self.lock:
            node = self._get_cached_node(node_id)

            ip = node.get_external_ip()
            if ip is None:
                node = self._get_node(node_id)
                ip = node.get_external_ip()

            return ip

    def internal_ip(self, node_id: str):
        with self.lock:
            node = self._get_cached_node(node_id)

            ip = node.get_internal_ip()
            if ip is None:
                node = self._get_node(node_id)
                ip = node.get_internal_ip()

            return ip

    @_retry
    def create_node(self, base_config: dict, tags: dict, count: int) -> Dict[str, dict]:
        """Creates instances.

        Returns dict mapping instance id to each create operation result for the created
        instances.
        """
        with self.lock:
            result_dict = {}
            labels = tags  # gcp uses "labels" instead of aws "tags"
            labels = dict(sorted(copy.deepcopy(labels).items()))

            node_type = get_node_type(base_config)
            resource = self.resources[node_type]

            # Try to reuse previously stopped nodes with compatible configs
            if self.cache_stopped_nodes:
                filters = {
                    TAG_RAY_NODE_KIND: labels[TAG_RAY_NODE_KIND],
                    # SkyPilot: removed TAG_RAY_LAUNCH_CONFIG to allow reusing nodes
                    # with different launch configs.
                    # Reference: https://github.com/skypilot-org/skypilot/pull/1671
                }
                # This tag may not always be present.
                if TAG_RAY_USER_NODE_TYPE in labels:
                    filters[TAG_RAY_USER_NODE_TYPE] = labels[TAG_RAY_USER_NODE_TYPE]
                filters_with_launch_config = copy.copy(filters)
                filters_with_launch_config[TAG_RAY_LAUNCH_CONFIG] = labels[
                    TAG_RAY_LAUNCH_CONFIG
                ]

                # SKY: "TERMINATED" for compute VM, "STOPPED" for TPU VM
                # "STOPPING" means the VM is being stopped, which needs
                # to be included to avoid creating a new VM.
                if isinstance(resource, GCPCompute):
                    STOPPED_STATUS = ["TERMINATED", "STOPPING"]
                else:
                    STOPPED_STATUS = ["STOPPED", "STOPPING"]

                # SkyPilot: We try to use the instances with the same matching launch_config first. If
                # there is not enough instances with matching launch_config, we then use all the
                # instances with the same matching launch_config plus some instances with wrong
                # launch_config.
                def get_order_key(node):
                    import datetime

                    timestamp = node.get("lastStartTimestamp")
                    if timestamp is not None:
                        return datetime.datetime.strptime(
                            timestamp, "%Y-%m-%dT%H:%M:%S.%f%z"
                        )
                    return node.id

                nodes_matching_launch_config = resource._list_instances(
                    filters_with_launch_config, STOPPED_STATUS
                )
                nodes_matching_launch_config.sort(
                    key=lambda n: get_order_key(n), reverse=True
                )
                if len(nodes_matching_launch_config) >= count:
                    reuse_nodes = nodes_matching_launch_config[:count]
                else:
                    nodes_all = resource._list_instances(filters, STOPPED_STATUS)
                    nodes_matching_launch_config_ids = set(
                        n.id for n in nodes_matching_launch_config
                    )
                    nodes_non_matching_launch_config = [
                        n
                        for n in nodes_all
                        if n.id not in nodes_matching_launch_config_ids
                    ]
                    # This is for backward compatibility, where the uesr already has leaked
                    # stopped nodes with the different launch config before update to #1671,
                    # and the total number of the leaked nodes is greater than the number of
                    # nodes to be created. With this, we will make sure we will reuse the
                    # most recently used nodes.
                    # This can be removed in the future when we are sure all the users
                    # have updated to #1671.
                    nodes_non_matching_launch_config.sort(
                        key=lambda n: get_order_key(n), reverse=True
                    )
                    reuse_nodes = (
                        nodes_matching_launch_config + nodes_non_matching_launch_config
                    )
                    # The total number of reusable nodes can be less than the number of nodes to be created.
                    # This `[:count]` is fine, as it will get all the reusable nodes, even if there are
                    # less nodes.
                    reuse_nodes = reuse_nodes[:count]

                reuse_node_ids = [n.id for n in reuse_nodes]
                if reuse_nodes:
                    # TODO(suquark): Some instances could still be stopping.
                    # We may wait until these instances stop.
                    cli_logger.print(
                        # TODO: handle plural vs singular?
                        f"Reusing nodes {cli_logger.render_list(reuse_node_ids)}. "
                        "To disable reuse, set `cache_stopped_nodes: False` "
                        "under `provider` in the cluster configuration."
                    )
                    for node_id in reuse_node_ids:
                        result = resource.start_instance(node_id)
                        result_dict[node_id] = {node_id: result}
                    for node_id in reuse_node_ids:
                        self.set_node_tags(node_id, tags)
                    count -= len(reuse_node_ids)
            if count:
                results = resource.create_instances(base_config, labels, count)
                result_dict.update(
                    {instance_id: result for result, instance_id in results}
                )
            return result_dict

    @_retry
    def terminate_node(self, node_id: str):
        with self.lock:
            result = None
            resource = self._get_resource_depending_on_node_name(node_id)
            try:
                if self.cache_stopped_nodes:
                    cli_logger.print(
                        f"Stopping instance {node_id} "
                        + cf.dimmed(
                            "(to terminate instead, "
                            "set `cache_stopped_nodes: False` "
                            "under `provider` in the cluster configuration)"
                        ),
                    )
                    result = resource.stop_instance(node_id=node_id)

                    # Check if the instance is actually stopped.
                    # GCP does not fully stop an instance even after
                    # the stop operation is finished.
                    for _ in range(MAX_POLLS_STOP):
                        instance = resource.get_instance(node_id=node_id)
                        if instance.is_stopped():
                            logger.info(f"Instance {node_id} is stopped.")
                            break
                        elif instance.is_stopping():
                            time.sleep(POLL_INTERVAL)
                        else:
                            raise RuntimeError(
                                f"Unexpected instance status." " Details: {instance}"
                            )

                    if instance.is_stopping():
                        raise RuntimeError(
                            f"Maximum number of polls: "
                            f"{MAX_POLLS_STOP} reached. "
                            f"Instance {node_id} is still in "
                            "STOPPING status."
                        )
                else:
                    result = resource.delete_instance(
                        node_id=node_id,
                    )
            except googleapiclient.errors.HttpError as http_error:
                if http_error.resp.status == 404:
                    logger.warning(
                        f"Tried to delete the node with id {node_id} "
                        "but it was already gone."
                    )
                else:
                    raise http_error from None

        return result

    @_retry
    def _get_node(self, node_id: str) -> GCPNode:
        self.non_terminated_nodes({})  # Side effect: updates cache

        with self.lock:
            if node_id in self.cached_nodes:
                return self.cached_nodes[node_id]

            resource = self._get_resource_depending_on_node_name(node_id)
            instance = resource.get_instance(node_id=node_id)

            return instance

    def _get_cached_node(self, node_id: str) -> GCPNode:
        if node_id in self.cached_nodes:
            return self.cached_nodes[node_id]

        return self._get_node(node_id)

    @staticmethod
    def bootstrap_config(cluster_config):
        return bootstrap_gcp(cluster_config)
