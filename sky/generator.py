from typing import List, Optional, Tuple

import colorama

from sky import check
from sky import clouds
from sky import global_user_state
from sky import resources
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Move this to catalog utils?
_NUM_ACC_TO_MINIMUM_HOST = {
    # Low-end GPUs: 4 vCPUs, 16GB CPU RAM per GPU
    'K80': {
        1: (4.0, 16.0),
        2: (8.0, 32.0),
        4: (16.0, 64.0),
        8: (32.0, 128.0),
        16: (64.0, 256.0),
    },
    'T4': {
        1: (4.0, 16.0),
        2: (8.0, 32.0),
        4: (16.0, 64.0),
        8: (32.0, 128.0),
    },
    # High-end GPUs: 6 vCPUs, 32GB CPU RAM per GPU
    'P100': {
        1: (6.0, 32.0),
        2: (12.0, 64.0),
        4: (24.0, 128.0),
        8: (48.0, 256.0),
    },
    'V100': {
        1: (6.0, 32.0),
        2: (12.0, 64.0),
        4: (24.0, 128.0),
        8: (48.0, 256.0),
    },
}


class CandidateGenerator:

    def __init__(self) -> None:
        self.enabled_clouds = global_user_state.get_enabled_clouds()
        self.retry = True

    def _get_feasible_clouds(
            self, cloud: Optional[clouds.Cloud]) -> List[clouds.Cloud]:
        if cloud is None:
            feasible_clouds = self.enabled_clouds
        else:
            feasible_clouds = [cloud]

        assert str(cloud) != 'Local'
        # FIXME(woosuk): Exclude local cloud for now.
        feasible_clouds = [c for c in feasible_clouds if str(c) != 'Local']
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

    @staticmethod
    def get_minimum_host_size(acc_name: str,
                              acc_count: int) -> Optional[Tuple[float, float]]:

        # if acc_name in _NUM_ACC_TO_MINIMUM_HOST:
        #     return _NUM_ACC_TO_MINIMUM_HOST[acc_name][acc_count]
        # elif acc_name.startswith('tpu-'):
        #     num_tpu_cores = int(acc_name.split('-')[2])
        #     num_vcpus = float(num_tpu_cores)
        #     cpu_memory = 4 * num_vcpus
        #     return num_vcpus, cpu_memory
        # return None
        # TODO: Implement this.
        return

    def get_feasible_resources(
        self,
        resource_filter: resources.ResourceFilter,
        get_smallest_vms: bool = False,
    ) -> List[resources.ClusterResources]:
        feasible_clouds = self._get_feasible_clouds(resource_filter.cloud)
        if not feasible_clouds:
            # TODO: Print a warning.
            return []

        feasible_resources = []
        for cloud in feasible_clouds:
            # TODO: Support on-prem.
            feasible_resources += cloud.get_feasible_resources(
                resource_filter, get_smallest_vms)

        if feasible_resources:
            # Found resources that match the filter.
            return feasible_resources

        return []
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
