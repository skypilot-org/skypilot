"""Service registry."""
import collections
from typing import List, Optional

import sky
from sky import clouds
from sky import resources as resources_lib

Resources = resources_lib.Resources

_CLOUDS = [
    clouds.AWS(),
    # FIXME(zongheng): as of 1/16/2022, our Azure subscriptions are disabled.
    # clouds.Azure(),
    clouds.GCP(),
]


def _filter_out_blocked_clouds(task: sky.Task):
    available_clouds = []
    for cloud in _CLOUDS:
        for blocked_cloud in task.blocked_clouds:
            if cloud.is_same_cloud(blocked_cloud):
                break
        else:  # non-blocked cloud (no break)
            available_clouds.append(cloud)
    return available_clouds


def _filter_out_blocked_launchable_resources(
        launchable_resources: List[Resources],
        blocked_launchable_resources: List[Resources]):
    """Whether the resources are blocked."""
    available_resources = []
    for resources in launchable_resources:
        for blocked_resources in blocked_launchable_resources:
            if resources.is_launchable_fuzzy_equal(blocked_resources):
                break
        else:  # non-blokced launchable resources. (no break)
            available_resources.append(resources)
    return available_resources


def fill_in_launchable_resources(
        task: sky.Task,
        blocked_launchable_resources: Optional[List[Resources]],
):
    if blocked_launchable_resources is None:
        blocked_launchable_resources = []
    launchable = collections.defaultdict(list)
    for resources in task.get_resources():
        if resources.is_launchable():
            launchable[resources] = [resources]
        elif resources.cloud is not None:
            launchable[
                resources] = resources.cloud.get_feasible_launchable_resources(
                    resources)
        else:
            # Remove blocked clouds.
            available_clouds = _filter_out_blocked_clouds(task)
            for cloud in available_clouds:
                feasible_resources = cloud.get_feasible_launchable_resources(
                    resources)
                launchable[resources].extend(feasible_resources)
        launchable[resources] = _filter_out_blocked_launchable_resources(
            launchable[resources], blocked_launchable_resources)

    return launchable
