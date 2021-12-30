"""Service registry."""
import collections
from typing import Dict, List, Optional

import sky
from sky import clouds
from sky import global_user_state
from sky import resources as resources_lib

Resources = resources_lib.Resources

ALL_CLOUDS = [clouds.AWS(), clouds.Azure(), clouds.GCP()]


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
) -> Dict[Resources, List[Resources]]:
    enabled_clouds = global_user_state.get_enabled_clouds()
    launchable = collections.defaultdict(list)
    if blocked_launchable_resources is None:
        blocked_launchable_resources = []
    for resources in task.get_resources():
        if resources.cloud is not None and not clouds.cloud_in_list(
                resources.cloud, enabled_clouds):
            launchable[resources] = []
        elif resources.is_launchable():
            launchable[resources] = [resources]
        elif resources.cloud is not None:
            launchable[
                resources] = resources.cloud.get_feasible_launchable_resources(
                    resources)
        else:
            for cloud in enabled_clouds:
                feasible_resources = cloud.get_feasible_launchable_resources(
                    resources)
                launchable[resources].extend(feasible_resources)
        launchable[resources] = _filter_out_blocked_launchable_resources(
            launchable[resources], blocked_launchable_resources)

    return launchable
