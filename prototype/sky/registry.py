"""Service registry."""
import collections
from typing import Dict, List, Optional

import sky
from sky import clouds
from sky import resources as resources_lib

Resources = resources_lib.Resources

def _is_cloud_in_list(cloud: clouds.Cloud, enabled_clouds: List[clouds.Cloud]):
    for c in enabled_clouds:
        if cloud.is_same_cloud(c):
            return True
    return False


def _launchable_resources_fuzzy_eq(r1: Resources, r2: Resources):
    """Whether the resources are the fuzzily same launchable resources."""
    assert r1.cloud is not None and r2.cloud is not None
    if not r1.cloud.is_same_cloud(r2.cloud):
        return False
    if r1.instance_type is not None or r2.instance_type is not None:
        return r1.instance_type == r2.instance_type
    # For GCP, when a accelerator type fails to launch, it should be blocked
    # regardless of the count, since the larger number will fail either.
    return r1.accelerators.keys() == r2.accelerators.keys()


def _filter_out_blocked_launchable_resources(
        launchable_resources: List[Resources],
        blocked_launchable_resources: List[Resources]):
    """Whether the resources are blocked."""
    available_resources = []
    for resources in launchable_resources:
        for blocked_resources in blocked_launchable_resources:
            if _launchable_resources_fuzzy_eq(resources, blocked_resources):
                break
        else:  # non-blokced launchable resources. (no break)
            available_resources.append(resources)
    return available_resources


def fill_in_launchable_resources(
        task: sky.Task,
        blocked_launchable_resources: Optional[List[Resources]],
) -> Dict[Resources, List[Resources]]:
    if blocked_launchable_resources is None:
        blocked_launchable_resources = []
    launchable = collections.defaultdict(list)
    for resources in task.get_resources():
        if resources.cloud is not None and not _is_cloud_in_list(
                resources.cloud, task.enabled_clouds):
            launchable[resources] = []
        elif resources.is_launchable():
            launchable[resources] = [resources]
        elif resources.cloud is not None:
            launchable[
                resources] = resources.cloud.get_feasible_launchable_resources(
                    resources)
        else:
            for cloud in task.enabled_clouds:
                feasible_resources = cloud.get_feasible_launchable_resources(
                    resources)
                launchable[resources].extend(feasible_resources)
        launchable[resources] = _filter_out_blocked_launchable_resources(
            launchable[resources], blocked_launchable_resources)

    return launchable
