from typing import Optional, Dict, Any

import copy
import logging

from sky.provision.gcp import config, general_instance, tpu_instance

logger = logging.getLogger(__name__)

# Data transfer within the same region but different availability zone costs $0.01/GB:
# https://cloud.google.com/vpc/network-pricing
# Lifecycle: https://cloud.google.com/compute/docs/instances/instance-life-cycle


def resume_instances(region: str, cluster_name: str, tags: Dict[str, str],
                     count: int, provider_config: Dict) -> Dict[str, Any]:
    tpu_vms = provider_config.get(config.HAS_TPU_PROVIDER_FIELD, False)
    if tpu_vms:
        return tpu_instance.resume_instances(region, cluster_name, tags, count,
                                             provider_config)
    else:
        return general_instance.resume_instances(region, cluster_name, tags,
                                                 count, provider_config)


def create_or_resume_instances(region: str, cluster_name: str,
                               node_config: Dict[str, Any],
                               tags: Dict[str, str], count: int,
                               resume_stopped_nodes: bool) -> Dict[str, Any]:
    """Creates instances.

    Returns dict mapping instance id to ec2.Instance object for the created
    instances.
    """
    # sort tags by key to support deterministic unit test stubbing
    tags = dict(sorted(copy.deepcopy(tags).items()))

    all_created_nodes = {}
    # Try to reuse previously stopped nodes with compatible configs
    if resume_stopped_nodes:
        all_created_nodes = resume_instances(region, cluster_name, tags, count)

    remaining_count = count - len(all_created_nodes)
    if remaining_count > 0:
        created_nodes_dict = create_instances(region, cluster_name, node_config,
                                              tags, remaining_count)
        all_created_nodes.update(created_nodes_dict)
    return all_created_nodes


def stop_instances(region: str, cluster_name: str,
                   provider_config: Optional[Dict]):
    tpu_vms = provider_config.get(config.HAS_TPU_PROVIDER_FIELD, False)
    if tpu_vms:
        tpu_instance.stop_instances(region, cluster_name, provider_config)
    else:
        general_instance.stop_instances(region, cluster_name, provider_config)


def terminate_instances(region: str, cluster_name: str,
                        provider_config: Optional[Dict]):
    tpu_vms = provider_config.get(config.HAS_TPU_PROVIDER_FIELD, False)
    if tpu_vms:
        tpu_instance.terminate_instances(region, cluster_name, provider_config)
    else:
        general_instance.terminate_instances(region, cluster_name,
                                             provider_config)
