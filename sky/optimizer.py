"""Optimizer: assigns best resources to user tasks."""
import collections
import enum
import typing
from typing import Dict, List, Optional, Tuple

import colorama
import numpy as np
import prettytable

from sky import check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import generator
from sky import resources
from sky import sky_logging
from sky import task as task_lib
from sky.utils import env_options
from sky.utils import ux_utils
from sky.utils import log_utils

if typing.TYPE_CHECKING:
    from sky import dag as dag_lib

logger = sky_logging.init_logger(__name__)

Task = task_lib.Task


# Constants: minimize what target?
class OptimizeTarget(enum.Enum):
    COST = 0
    TIME = 1


# For logging purposes.
def _create_table(field_names: List[str]) -> prettytable.PrettyTable:
    table_kwargs = {
        'hrules': prettytable.FRAME,
        'vrules': prettytable.NONE,
        'border': True,
    }
    return log_utils.create_table(field_names, **table_kwargs)


class Optimizer:
    """Optimizer: assigns best resources to user tasks."""

    @staticmethod
    def optimize(
        resource_filter: resources.ResourceFilter,
        blocked_resources: List[resources.ClusterResources],
        quiet: bool = True,
    ) -> Optional[resources.ClusterResources]:
        # TODO(woosuk): Consider data locality in optimization.
        candidate_generator = generator.CandidateGenerator()
        feasible_resources = candidate_generator.get_feasible_resources(
            resource_filter, get_smallest_vms=True)
        available_resources = []
        for r in feasible_resources:
            for b in blocked_resources:
                if b == r:
                    # r is blocked.
                    break
                if b.is_subset_of(r):
                    # r is harder to get than b.
                    break
            else:
                available_resources.append(r)

        if not available_resources:
            return None

        available_resources.sort(key=lambda r: r.get_hourly_price())
        if quiet:
            return available_resources[0]

        # Prune.
        tmp_resources = []
        while available_resources:
            x = available_resources.pop(0)
            available_resources = [
                r for r in available_resources if not x.is_subset_of(r)
            ]
            tmp_resources.append(x)
        available_resources = tmp_resources

        # Get the best resources per cloud.
        best_per_cloud = {}
        best_resources = []
        for r in available_resources:
            cloud = str(r.cloud)
            if cloud not in best_per_cloud:
                best_per_cloud[cloud] = r
                best_resources.append(r)
        assert best_resources[0] == available_resources[0]

        # Diplay the optimization result as a table.
        columns = [
            'CLOUD', 'INSTANCE', 'vCPUs', 'MEM(GiB)', 'ACCELERATORS', '$/hr',
            'CHOSEN'
        ]

        def _to_str(x: Optional[float]) -> str:
            if x is None:
                return '-'
            if x.is_integer():
                return str(int(x))
            return f'{x:.1f}'

        rows = []
        for r in best_resources:
            if r.accelerator is None:
                acc = '-'
            else:
                acc = f'{r.accelerator.name}:{r.accelerator.count}'
            row = [
                str(r.cloud),
                r.instance_type,
                _to_str(r.num_vcpus),
                _to_str(r.cpu_memory),
                acc,
                f'{r.get_hourly_price():.2f}',
                '',
            ]
            rows.append(row)

        # Use tick sign for the chosen resources.
        best_row = rows[0]
        best_row[-1] = (colorama.Fore.GREEN + '   ' + u'\u2714' +
                        colorama.Style.RESET_ALL)
        # Highlight the chosen resources.
        for i, cell in enumerate(best_row):
            best_row[i] = (f'{colorama.Style.BRIGHT}{cell}'
                           f'{colorama.Style.RESET_ALL}')

        # Print the price information.
        # In addition to the best price, show the worst-case price
        # that the user may have to pay in case of failover.
        # TODO(woosuk): apply a price cap (e.g., 20%) to bound the worst case.
        best_price = best_resources[0].get_hourly_price()
        worst_resources = max(available_resources,
                              key=lambda r: r.get_hourly_price())
        worst_price = worst_resources.get_hourly_price()
        if best_price == worst_price:
            logger.info(f'Estimated price: ${best_price:.2f}/hr')
        else:
            logger.info('Estimated price: '
                        f'${best_price:.2f}/hr - ${worst_price:.2f}/hr')

        # Print the table.
        num_nodes = resource_filter.num_nodes
        plural = 's' if num_nodes > 1 else ''
        logger.info(f'{colorama.Style.BRIGHT}'
                    f'Best resources ({num_nodes} node{plural}):'
                    f'{colorama.Style.RESET_ALL}')
        table = _create_table(columns)
        table.add_rows(rows)
        logger.info(f'{table}\n')

        return best_resources[0]
