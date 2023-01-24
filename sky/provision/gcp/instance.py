from typing import List

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
    for availability_zone in zones:
        response = (compute_client.instances().list(
            project=project_id,
            zone=availability_zone,
            filter=filter_expr,
        ).execute())
        instances.extend(response.get('items', []))

    return instances
    # return [GCPComputeNode(i, self) for i in instances]
