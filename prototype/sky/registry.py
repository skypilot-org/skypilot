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
    return type(__l.cloud) == type(__r.cloud) and __l.instance_type == __r.instance_type

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
            for cloud in _CLOUDS:
                launchable[resources].extend(
                    cloud.get_feasible_launchable_resources(resources))

        # Remove blocked resources from launchable resources.
        for orig_resources, launchable_list in launchable.items():
            new_launchable_list = []            
            for launchable_resources in launchable_list:
                for blocked_resources in task.blocked_resources:
                    if _resource_eq(launchable_resources, blocked_resources):
                        break
                else: # Not blocked resources.
                    new_launchable_list.append(launchable_resources)
            launchable[orig_resources] = new_launchable_list
            
    return launchable
