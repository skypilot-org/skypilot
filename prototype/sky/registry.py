"""Service registry."""
import collections
from typing import List

import sky
from sky import clouds
from sky import resources

Resources = resources.Resources

_CLOUDS = [
    clouds.AWS(),
    clouds.Azure(),
    clouds.GCP(),
]


def _filter_out_blocked_clouds(task: sky.Task):
    available_clouds = []
    for cloud in _CLOUDS:
        for blocked_cloud in task.blocked_clouds:
            if type(cloud) == type(blocked_cloud):
                break
        else:  # non-blocked cloud (no break)
            available_clouds.append(cloud)
    return available_clouds


def _launchable_resources_eq(r1: Resources, r2: Resources):
    """Whether the resources are the same launchable resources."""
    assert r1.cloud is not None and r2.cloud is not None
    if type(r1.cloud) != type(r2.cloud):
        return False
    if r1.instance_type is not None or r2.instance_type is not None:
        return r1.instance_type == r2.instance_type
    return r1.accelerators.keys() == r2.accelerators.keys()


def _filter_out_blocked_launchable_resources(launchable_resources: List[Resources],
                                             blocked_launchable_resources: List[Resources]):
    """Whether the resources are blocked."""
    available_resources = []
    for resources in launchable_resources:
        for blocked_resources in blocked_launchable_resources:
            if _launchable_resources_eq(resources, blocked_resources):
                break
        else:  # non-blokced launchable resources. (no break)
            available_resources.append(resources)
    return available_resources


def fill_in_launchable_resources(task: sky.Task,
                                 blocked_launchable_resources: List[Resources]):
    launchable = collections.defaultdict(list)
    for resources in task.get_resources():
        if resources.is_launchable():
            launchable[resources] = [resources]
            continue
        if resources.cloud is not None:
            launchable[
                resources] = resources.cloud.get_feasible_launchable_resources(
                    resources)
        else:
            # Remove blocked clouds.
            available_clouds = _filter_out_blocked_clouds(task)
            for cloud in available_clouds:
                feasible_resources = cloud.get_feasible_launchable_resources(
                    resources)
                feasible_resources = _filter_out_blocked_launchable_resources(
                    feasible_resources, blocked_launchable_resources)
                launchable[resources].extend(feasible_resources)

    return launchable
