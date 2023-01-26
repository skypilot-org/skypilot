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
                               node_config: Dict[str, Any], tags: Dict[str,
                                                                       str],
                               count: int, resume_stopped_nodes: bool,
                               provider_config: Dict) -> Dict[str, Any]:
    """Creates instances.

    Returns dict mapping instance id to ec2.Instance object for the created
    instances.
    """
    tpu_vms = provider_config.get(config.HAS_TPU_PROVIDER_FIELD, False)
    if tpu_vms:
        tpu_instance.create_or_resume_instances(region, cluster_name,
                                                node_config, tags, count,
                                                resume_stopped_nodes,
                                                provider_config)
    else:
        general_instance.create_or_resume_instances(region, cluster_name,
                                                    node_config, tags, count,
                                                    resume_stopped_nodes,
                                                    provider_config)


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
