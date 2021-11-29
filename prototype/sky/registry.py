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


def _resource_eq(__l: Resources, __r: Resources) -> bool:
    assert __l.cloud is not None and __l.instance_type is not None
    return type(__l.cloud) == type(
        __r.cloud) and __l.instance_type == __r.instance_type


def _remove_task_blocked_resources(task: sky.Task, launchable: dict):
    """Remove blocked resources from launchable resources."""
    for orig_resources, launchable_list in launchable.items():
        new_launchable_list = []
        for launchable_resources in launchable_list:
            for blocked_resources in task.blocked_resources:
                if _resource_eq(launchable_resources, blocked_resources):
                    break
            else:  # Not blocked resources.
                new_launchable_list.append(launchable_resources)
        launchable[orig_resources] = new_launchable_list
    return launchable


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

        launchable = _remove_task_blocked_resources(task, launchable)
    return launchable
