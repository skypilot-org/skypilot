"""Service registry."""
import collections

import sky
from sky import clouds
from sky.logging import init_logger
logger = init_logger(__name__)

_CLOUDS = [
    clouds.AWS(),
    clouds.Azure(),
    clouds.GCP(),
]


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
    return launchable
