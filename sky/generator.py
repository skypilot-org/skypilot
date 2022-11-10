from typing import List, Optional

import colorama

from sky import check
from sky import clouds
from sky import global_user_state
from sky import resources as resources_lib
from sky import sky_logging
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


class CandidateGenerator:

    def __init__(self) -> None:
        self.enabled_clouds = global_user_state.get_enabled_clouds()
        self.retry = True

    def _get_feasible_clouds(
            self, cloud: Optional[clouds.Cloud]) -> List[clouds.Cloud]:
        feasible_clouds = []
        for c in self.enabled_clouds:
            if cloud is None:
                feasible_clouds.append(c)
            elif cloud.is_same_cloud(c):
                feasible_clouds.append(c)

        if feasible_clouds:
            # Found a cloud that matches the filter.
            return feasible_clouds

        if not self.retry:
            # No matching cloud found.
            return []

        # Run `sky check` and try again.
        check.check(quiet=True)
        self.retry = False
        self.enabled_clouds = global_user_state.get_enabled_clouds()

        for c in self.enabled_clouds:
            if cloud is None:
                feasible_clouds.append(c)
            elif cloud.is_same_cloud(c):
                feasible_clouds.append(c)
        return feasible_clouds

    def get_feasible_resources(
        self, resource_filter: resources_lib.ResourceFilter
    ) -> List[resources_lib.Resource]:
        feasible_clouds = self._get_feasible_clouds(resource_filter.cloud)
        if not feasible_clouds:
            # TODO: Print a warning.
            return []

        feasible_resources = []
        for cloud in feasible_clouds:
            # TODO: Support on-prem.
            feasible_resources += cloud.get_feasible_resources(resource_filter)

        if feasible_resources:
            # Found resources that match the filter.
            return feasible_resources

        # No feasible resources found. Try to find a fuzzy match.
        fuzzy_match_resources = []
        for cloud in feasible_clouds:
            fuzzy_match_resources += cloud.get_fuzzy_match_resources(
                resource_filter)
        logger.info(f'No resource satisfying {resource_filter} found.')
        logger.info(f'Did you mean: '
                    f'{colorama.Fore.CYAN}'
                    f'{sorted(fuzzy_match_resources)}'
                    f'{colorama.Style.RESET_ALL}')
        return []
