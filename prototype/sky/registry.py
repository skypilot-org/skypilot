"""Service registry."""
import collections

import sky
from sky import clouds
from sky import resources

Resources = resources.Resources

_CLOUDS = [
    clouds.AWS(),
    clouds.Azure(),
    clouds.GCP(),
]


def _get_available_clouds(task):
    available_clouds = []
    for cloud in _CLOUDS:
        for blocked_cloud in task.blocked_clouds:
            if type(cloud) == type(blocked_cloud):
                break
        else:
            available_clouds.append(cloud)
    return available_clouds


def fill_in_launchable_resources(task: sky.Task):
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
            available_clouds = _get_available_clouds(task)
            for cloud in available_clouds:
                launchable[resources].extend(
                    cloud.get_feasible_launchable_resources(resources))

    return launchable
