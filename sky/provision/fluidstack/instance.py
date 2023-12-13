"""Cloud provision interface.

This module provides a standard low-level interface that all
providers supported by SkyPilot need to follow.
"""
import time
from typing import Any, Dict, List, Optional


from sky import status_lib
from sky.skylet.providers.fluidstack.fluidstack_utils import (
    FluidstackClient,
    FluidstackAPIError
)


TAG_RAY_CLUSTER_NAME = 'ray-cluster-name'
TAG_RAY_NODE_KIND = 'ray-node-type'

def query_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    filters = {TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    instances = FluidstackClient().list_instances(filters)
    status_map = {
        'provisioning': status_lib.ClusterStatus.INIT,
        'create': status_lib.ClusterStatus.INIT,
        'requesting': status_lib.ClusterStatus.INIT,
        'customizing': status_lib.ClusterStatus.INIT,
        'running': status_lib.ClusterStatus.UP,
        'stopping': status_lib.ClusterStatus.STOPPED,
        'stopped': status_lib.ClusterStatus.STOPPED,
    }
    if non_terminated_only:
        pass
    statuses : Dict[str, Optional[status_lib.ClusterStatus]] = {}
    for instance in instances:
        status = status_map.get(instance['status'],
                                status_lib.ClusterStatus.INIT)
        statuses[instance['id']] = status
    return statuses

def wait_for_instances(instance_ids : List[str],
                       timeout: int = 300) -> None:
    """Wait for instances to be terminated."""
    start = time.time()
    client = FluidstackClient()
    while True:
        all_terminated = True
        for instance_id in instance_ids:

            try:
                client.info(instance_id)
            except FluidstackAPIError as e:
                if e.code in [404, 410]:
                    all_terminated = all_terminated and True
            finally:
                all_terminated = all_terminated and False
        if all_terminated:
            return
        if time.time() - start > timeout:
            return
        time.sleep(5)
def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud

    label_filters = {TAG_RAY_CLUSTER_NAME: cluster_name_on_cloud}
    if worker_only:
        label_filters[TAG_RAY_NODE_KIND] = 'worker'

    instances = FluidstackClient().list_instances(label_filters)
    errors = []
    for instance in instances:
        try:
            FluidstackClient().delete(instance['id'])
        except FluidstackAPIError as e:
            #already terminated
            if e.code in [404, 410]:
                continue
            errors.append(str(e))
    if errors:
        raise RuntimeError(f'Failed to terminate instances: {errors}')
    wait_for_instances([instance['id'] for instance in instances])
