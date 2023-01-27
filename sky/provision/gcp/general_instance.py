from typing import List, Dict, Optional, Any

import copy
import logging
import re
import time

from sky.provision.gcp import config, utils

logger = logging.getLogger(__name__)


def _construct_label_filter_expr(label_filters: dict) -> str:
    exprs = [
        f'(labels.{key} = {value})' for key, value in label_filters.items()
    ]
    if len(exprs) > 1:
        return f'({" AND ".join(exprs)})'
    return exprs[0]


def _construct_status_filter_expr(status_filter: list) -> str:
    exprs = [f'(status = {status})' for status in status_filter]
    if len(exprs) > 1:
        return f'({" OR ".join(exprs)})'
    return exprs[0]


def _convert_resources_to_urls(configuration_dict: Dict[str,
                                                        Any], project_id: str,
                               availability_zone: str) -> Dict[str, Any]:
    """Ensures that resources are in their full URL form.

    GCP expects machineType and accleratorType to be a full URL (e.g.
    `zones/us-west1/machineTypes/n1-standard-2`) instead of just the
    type (`n1-standard-2`)

    Args:
        configuration_dict: Dict of options that will be passed to GCP
    Returns:
        Input dictionary, but with possibly expanding `machineType` and
            `acceleratorType`.
    """
    configuration_dict = copy.deepcopy(configuration_dict)
    existing_machine_type = configuration_dict['machineType']
    if not re.search('.*/machineTypes/.*', existing_machine_type):
        configuration_dict[
            'machineType'] = 'zones/{zone}/machineTypes/{machine_type}'.format(
                zone=availability_zone,
                machine_type=configuration_dict['machineType'],
            )

    for accelerator in configuration_dict.get('guestAccelerators', []):
        gpu_type = accelerator['acceleratorType']
        if not re.search('.*/acceleratorTypes/.*', gpu_type):
            accelerator[
                'acceleratorType'] = f'projects/{project_id}/zones/{availability_zone}/acceleratorTypes/{gpu_type}'  # noqa: E501

    return configuration_dict


def _wait_for_operation(
    resource,
    operation: dict,
    project_id: str,
    availability_zone: str,
    max_polls: int = utils.MAX_POLLS,
    poll_interval: int = utils.POLL_INTERVAL,
) -> dict:
    """Poll for compute zone operation until finished."""
    logger.info("wait_for_compute_zone_operation: "
                f"Waiting for operation {operation['name']} to finish...")

    for _ in range(max_polls):
        result = (resource.zoneOperations().get(
            project=project_id,
            operation=operation["name"],
            zone=availability_zone,
        ).execute())
        if "error" in result:
            raise Exception(result["error"])

        if result["status"] == "DONE":
            logger.info("wait_for_compute_zone_operation: "
                        f"Operation {operation['name']} finished.")
            break

        time.sleep(poll_interval)

        return result


def list_instances(region: str, cluster_name: str, project_id: str,
                   status_filter: List[str], compute_client) -> list:
    filter_expr = f'(labels.{utils.TAG_RAY_CLUSTER_NAME} = {cluster_name})'
    if status_filter:
        filter_expr += ' AND ' + _construct_status_filter_expr(status_filter)
    zones = utils.get_zones_from_regions(region, project_id, compute_client)

    instances = []

    # NOTE: we add 'availability_zone' as an attribute of the nodes, as
    # we need the attribute to
    for availability_zone in zones:
        response = (compute_client.instances().list(
            project=project_id,
            zone=availability_zone,
            filter=filter_expr,
        ).execute())
        for inst in response.get('items', []):
            inst['availability_zone'] = availability_zone
            instances.append(inst)
    return instances


def update_instance_labels(resource, node: Dict, project_id: str,
                           labels: dict) -> dict:
    body = {
        "labels": dict(node["labels"], **labels),
        "labelFingerprint": node["labelFingerprint"],
    }
    node_id = node["name"]
    availability_zone = node['availability_zone']
    operation = (resource.instances().setLabels(
        project=project_id,
        zone=availability_zone,
        instance=node_id,
        body=body,
    ).execute())
    return operation


def batch_update_instance_labels(resource, instances: List[Dict],
                                 project_id: str, labels: dict):
    operations = []
    for inst in instances:
        opr = update_instance_labels(resource, inst, project_id, labels)
        opr['availability_zone'] = inst['availability_zone']
        operations.append(opr)
    for opr in operations:
        _result = _wait_for_operation(resource, opr, project_id,
                                      opr['availability_zone'])


def resume_instances(region: str, cluster_name: str, tags: Dict[str, str],
                     count: int, provider_config: Dict) -> Dict[str, Any]:
    project_id = provider_config['project_id']

    compute_client = config.construct_compute_client_from_provider_config(
        provider_config)
    # In GCP, 'TERMINATED' is equivalent to 'STOPPED' state in AWS.
    instances = list_instances(region,
                               cluster_name,
                               project_id=provider_config['project_id'],
                               status_filter=['TERMINATED'],
                               compute_client=compute_client)
    instances = instances[:count]

    for inst in instances:
        compute_client.instances().start(
            project=project_id,
            zone=inst['availability_zone'],
            instance=inst['name'],
        ).execute()

    # set labels and wait
    batch_update_instance_labels(compute_client, instances, project_id, tags)
    return instances


def create_instances(region: str, cluster_name: str,
                     node_config: Dict[str, Any], tags: Dict[str, str],
                     count: int, provider_config: Dict[str,
                                                       Any]) -> Dict[str, Any]:
    compute_client = config.construct_compute_client_from_provider_config(
        provider_config)

    project_id = provider_config['project_id']
    availability_zone = provider_config.get('availability_zone')
    if availability_zone is None:
        zones = utils.get_zones_from_regions(region, project_id, compute_client)
        availability_zone = zones[0]

    labels = tags

    # TODO: This seems result in errors in bulkInsert
    # node_config = _convert_resources_to_urls(node_config, project_id,
    #                                          availability_zone)

    # removing TPU-specific default key set in config.py
    node_config.pop('networkConfig', None)
    name = utils.generate_node_name(cluster_name, 'compute')

    labels = dict(node_config.get('labels', {}), **labels)

    node_config.update({
        'labels': dict(labels, **{utils.TAG_RAY_CLUSTER_NAME: cluster_name}),
        'name': name,
    })

    # Allow Google Compute Engine instance templates.
    #
    # Config example:
    #
    #     ...
    #     node_config:
    #         sourceInstanceTemplate: global/instanceTemplates/worker-16
    #         machineType: e2-standard-16
    #     ...
    #
    # node_config parameters override matching template parameters, if any.
    #
    # https://cloud.google.com/compute/docs/instance-templates
    # https://cloud.google.com/compute/docs/reference/rest/v1/instances/insert
    source_instance_template = node_config.pop('sourceInstanceTemplate', None)

    # Here we use the zonal API for bulk instance creation.
    # There is also regional instance creation API.
    # https://cloud.google.com/compute/docs/instances/multiple/create-in-bulk

    # 'name' is a field for creating a single instance, we pop it here
    #  and use a pattern instead.
    name_pattern = node_config.pop('name') + '-' + '#' * len(str(count))
    body = {
        'count': count,  # this is the max count
        'minCount': count,
        'namePattern': name_pattern,
        'instanceProperties': node_config,
        'sourceInstanceTemplate': source_instance_template,
    }

    operation = compute_client.instances().bulkInsert(
        project=project_id,
        zone=availability_zone,
        body=body,
    ).execute()

    _wait_for_operation(compute_client, operation, project_id,
                        availability_zone)


def create_or_resume_instances(
        region: str, cluster_name: str, node_config: Dict[str, Any],
        tags: Dict[str, str], count: int, resume_stopped_nodes: bool,
        provider_config: Optional[Dict]) -> Dict[str, Any]:
    """Creates instances.

    Returns dict mapping instance id to ec2.Instance object for the created
    instances.
    """
    # sort tags by key to support deterministic unit test stubbing
    tags = dict(sorted(copy.deepcopy(tags).items()))

    all_created_nodes = {}
    # Try to reuse previously stopped nodes with compatible configs
    if resume_stopped_nodes:
        all_created_nodes = resume_instances(region, cluster_name, tags, count,
                                             provider_config)

    remaining_count = count - len(all_created_nodes)
    if remaining_count > 0:
        create_instances(region, cluster_name, node_config, tags,
                         remaining_count, provider_config)


def stop_instances(region: str, cluster_name: str,
                   provider_config: Optional[Dict]):
    project_id = provider_config['project_id']
    compute_client = config.construct_compute_client_from_provider_config(
        provider_config)
    instances = list_instances(region,
                               cluster_name,
                               project_id=provider_config['project_id'],
                               status_filter=['RUNNING'],
                               compute_client=compute_client)
    for inst in instances:
        _operation = compute_client.instances().stop(
            project=project_id,
            zone=inst['availability_zone'],
            instance=inst['name'],
        ).execute()


def terminate_instances(region: str, cluster_name: str,
                        provider_config: Optional[Dict]):
    project_id = provider_config['project_id']
    compute_client = config.construct_compute_client_from_provider_config(
        provider_config)
    instances = list_instances(region,
                               cluster_name,
                               project_id=provider_config['project_id'],
                               status_filter=[],
                               compute_client=compute_client)

    for inst in instances:
        _operation = compute_client.instances().delete(
            project=project_id,
            zone=inst['availability_zone'],
            instance=inst['name'],
        ).execute()
