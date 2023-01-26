from typing import Dict, List, Optional, Any

import copy
import logging
import time

from googleapiclient import errors

from sky.provision.gcp import config, utils

logger = logging.getLogger(__name__)


def _wait_for_operation(
    tpu_client,
    operation: dict,
    max_polls: int = utils.MAX_POLLS,
    poll_interval: int = utils.POLL_INTERVAL,
) -> dict:
    """Poll for TPU operation until finished."""
    logger.info("wait_for_tpu_operation: "
                f"Waiting for operation {operation['name']} to finish...")

    for _ in range(max_polls):
        result = (tpu_client.projects().locations().operations().get(
            name=f"{operation['name']}").execute())
        if "error" in result:
            raise Exception(result["error"])

        if "response" in result:
            logger.info("wait_for_tpu_operation: "
                        f"Operation {operation['name']} finished.")
            break

        time.sleep(poll_interval)

    return result


def list_instances(region: str, cluster_name: str, project_id: str,
                   status_filter: List[str], compute_client,
                   tpu_client) -> list:
    zones = utils.get_zones_from_regions(region, project_id, compute_client)
    instances = []
    # NOTE: we add 'availability_zone' as an attribute of the nodes, as
    # we need the attribute to
    for availability_zone in zones:
        path = f'projects/{project_id}/locations/{availability_zone}'
        try:
            response = tpu_client.projects().locations().nodes().list(
                parent=path).execute()
        except errors.HttpError as e:
            # SKY: Catch HttpError when accessing unauthorized region.
            # Return empty list instead of raising exception to not break
            # ray down.
            logger.warning(f'googleapiclient.errors.HttpError: {e.reason}')
            continue
        for node in response.get('nodes', []):
            labels = node.get('labels')
            state = node.get('state')
            if labels.get(utils.TAG_RAY_CLUSTER_NAME, None) == cluster_name:
                if not status_filter or state in status_filter:
                    node['availability_zone'] = availability_zone
                    instances.append(node)
    return instances


# this sometimes fails without a clear reason, so we retry it
# MAX_POLLS times
@utils.retry_on_exception(errors.HttpError, "unable to queue the operation")
def update_instance_labels(tpu_client, instance: dict, labels: dict) -> dict:
    body = {
        "labels": dict(instance["labels"], **labels),
    }
    update_mask = "labels"

    operation = (tpu_client.projects().locations().nodes().patch(
        name=instance["name"],
        updateMask=update_mask,
        body=body,
    ).execute())
    return operation


def batch_update_instance_labels(tpu_client, instances: List[Dict],
                                 project_id: str, labels: dict):
    operations = []
    for inst in instances:
        opr = update_instance_labels(tpu_client, inst, project_id, labels)
        opr['availability_zone'] = inst['availability_zone']
        operations.append(opr)
    for opr in operations:
        _result = _wait_for_operation(tpu_client, opr)


def resume_instances(region: str, cluster_name: str, tags: Dict[str, str],
                     count: int, provider_config: Dict) -> Dict[str, Any]:
    project_id = provider_config['project_id']

    compute_client = config.construct_compute_client_from_provider_config(
        provider_config)
    tpu_client = config.construct_tpu_client_from_provider_config(
        provider_config)
    # for GCP, 'TERMINATED'
    instances = list_instances(region,
                               cluster_name,
                               project_id=provider_config['project_id'],
                               status_filter=['TERMINATED'],
                               compute_client=compute_client,
                               tpu_client=tpu_client)
    instances = instances[:count]

    for inst in instances:
        tpu_client.instances().start(
            project=project_id,
            zone=inst['availability_zone'],
            instance=inst['name'],
        ).execute()

    # set labels and wait
    batch_update_instance_labels(tpu_client, instances, project_id, tags)
    return instances


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
    compute_client = config.construct_compute_client_from_provider_config(
        provider_config)
    tpu_client = config.construct_tpu_client_from_provider_config(
        provider_config)
    instances = list_instances(region,
                               cluster_name,
                               project_id=provider_config['project_id'],
                               status_filter=['RUNNING'],
                               compute_client=compute_client,
                               tpu_client=tpu_client)
    for inst in instances:
        _operation = tpu_client.projects().locations().nodes().stop(
            name=inst['name']).execute()


def terminate_instances(region: str, cluster_name: str,
                        provider_config: Optional[Dict]):
    compute_client = config.construct_compute_client_from_provider_config(
        provider_config)
    tpu_client = config.construct_tpu_client_from_provider_config(
        provider_config)
    instances = list_instances(region,
                               cluster_name,
                               project_id=provider_config['project_id'],
                               status_filter=[],
                               compute_client=compute_client,
                               tpu_client=tpu_client)
    for inst in instances:
        _operation = tpu_client.projects().locations().nodes().delete(
            name=inst['name']).execute()
