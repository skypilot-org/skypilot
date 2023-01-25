from typing import List, Optional, Dict
import logging

from googleapiclient import errors

from sky.provision.gcp import config

logger = logging.getLogger(__name__)
# Tag uniquely identifying all nodes of a cluster
TAG_RAY_CLUSTER_NAME = "ray-cluster-name"

# Data transfer within the same region but different availability zone costs $0.01/GB:
# https://cloud.google.com/vpc/network-pricing


def _get_zones_from_regions(region: str, project_id: str,
                            compute_client) -> List[str]:
    response = compute_client.zones().list(
        project=project_id, filter=f'name eq "^{region}-.*"').execute()
    return [zone['name'] for zone in response.get('items', [])]


def _construct_label_filter_expr(label_filters: dict) -> str:
    exprs = [
        f'(labels.{key} = {value})' for key, value in label_filters.items()
    ]
    return f'({" AND ".join(exprs)})'


def _construct_status_filter_expr(status_filter: list) -> str:
    exprs = [f'(status = {status})' for status in status_filter]
    return f'({" OR ".join(exprs)})'


def _list_instances(region: str, cluster_name: str, project_id: str,
                    tpu_vms: bool, compute_client) -> list:
    filter_expr = f'(labels.{TAG_RAY_CLUSTER_NAME} = {cluster_name})'
    zones = _get_zones_from_regions(region, project_id, compute_client)

    instances = []

    # NOTE: we add 'availability_zone' as an attribute of the nodes, as
    # we need the attribute to
    for availability_zone in zones:
        if not tpu_vms:
            response = (compute_client.instances().list(
                project=project_id,
                zone=availability_zone,
                filter=filter_expr,
            ).execute())
            for inst in response.get('items', []):
                inst['availability_zone'] = availability_zone
                instances.append(inst)
        else:
            path = f'projects/{project_id}/locations/{availability_zone}'
            try:
                response = compute_client.projects().locations().nodes().list(
                    parent=path).execute()
            except errors.HttpError as e:
                # SKY: Catch HttpError when accessing unauthorized region.
                # Return empty list instead of raising exception to not break
                # ray down.
                logger.warning(f'googleapiclient.errors.HttpError: {e.reason}')
                continue
            for node in response.get('nodes', []):
                labels = node.get('labels')
                if labels.get(TAG_RAY_CLUSTER_NAME, None) == cluster_name:
                    node['availability_zone'] = availability_zone
                    instances.append(node)
    return instances


def stop_instances(region: str, cluster_name: str,
                   provider_config: Optional[Dict]):
    project_id = provider_config['project_id']
    tpu_vms = provider_config.get(config.HAS_TPU_PROVIDER_FIELD, False)
    compute = config.construct_compute_clients_from_provider_config(
        provider_config)
    instances = _list_instances(region,
                                cluster_name,
                                project_id=provider_config['project_id'],
                                tpu_vms=tpu_vms,
                                compute_client=compute)
    if tpu_vms:
        for inst in instances:
            _operation = compute.projects().locations().nodes().stop(
                name=inst['name']).execute()
    else:
        for inst in instances:
            _operation = compute.instances().stop(
                project=project_id,
                zone=inst['availability_zone'],
                instance=inst['name'],
            ).execute()


def terminate_instances(region: str, cluster_name: str,
                        provider_config: Optional[Dict]):
    project_id = provider_config['project_id']
    tpu_vms = provider_config.get(config.HAS_TPU_PROVIDER_FIELD, False)

    compute = config.construct_compute_clients_from_provider_config(
        provider_config)
    instances = _list_instances(region,
                                cluster_name,
                                project_id=provider_config['project_id'],
                                tpu_vms=tpu_vms,
                                compute_client=compute)
    if tpu_vms:
        for inst in instances:
            _operation = compute.projects().locations().nodes().delete(
                name=inst['name']).execute()
    else:
        for inst in instances:
            _operation = compute.instances().delete(
                project=project_id,
                zone=inst['availability_zone'],
                instance=inst['name'],
            ).execute()
