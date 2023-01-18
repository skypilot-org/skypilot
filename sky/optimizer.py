"""Optimizer: assigns best resources to user tasks."""
import enum
from typing import Dict, List, Optional, Tuple

import colorama
import prettytable

from sky import resources
from sky import sky_logging
from sky.utils import log_utils

logger = sky_logging.init_logger(__name__)


# DELETEME
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

    @staticmethod
    def _optimize(
        feasible_clusters: List[resources.ClusterResources],
    ) -> Optional[resources.ClusterResources]:
        # TODO(woosuk): Consider data locality in optimization.
        if not feasible_clusters:
            return None
        chosen_cluster = min(feasible_clusters,
                             key=lambda c: c.get_hourly_price())
        return chosen_cluster

    @staticmethod
    def optimize(
        feasible_clusters: List[resources.ClusterResources],
        quiet: bool = True,
    ) -> Optional[resources.ClusterResources]:
        chosen_cluster = Optimizer._optimize(feasible_clusters)
        if quiet:
            return chosen_cluster

        # FIXME
        if chosen_cluster is None:
            return None

        logger.info('== Optimizer ==')

        # Get the best cluster for each cloud.
        clusters_per_cloud = {}
        for c in feasible_clusters:
            cloud = str(c.cloud)
            if cloud not in clusters_per_cloud:
                clusters_per_cloud[cloud] = []
            clusters_per_cloud[cloud].append(c)
        best_per_cloud = {}
        for cloud, clusters in clusters_per_cloud.items():
            if cloud != str(chosen_cluster.cloud):
                best_per_cloud[cloud] = Optimizer._optimize(clusters)

        # Sort the best clusters by price.
        # The chosen cluster should be at the front of the list.
        best_clusters = sorted(best_per_cloud.values(),
                               key=lambda c: c.get_hourly_price())
        best_clusters = [chosen_cluster] + best_clusters

        # Diplay the optimization result as a table.
        # FIXME: Zone -> Region
        columns = [
            'CLOUD', 'INSTANCE', 'vCPUs', 'MEM(GiB)', 'ACCELERATORS', 'ZONE',
            '$/hr', 'CHOSEN'
        ]

        def _to_str(x: Optional[float]) -> str:
            if x is None:
                return '-'
            if x.is_integer():
                return str(int(x))
            return f'{x:.1f}'

        rows = []
        for c in best_clusters:
            if c.accelerator is None:
                acc = '-'
            else:
                acc = f'{c.accelerator.name}:{c.accelerator.count}'
            row = [
                str(c.cloud),
                c.instance_type,
                _to_str(c.cpu),
                _to_str(c.memory),
                acc,
                c.zone,
                f'{c.get_hourly_price():.2f}',
                '',
            ]
            rows.append(row)

        # Use tick sign for the chosen cluster.
        best_row = rows[0]
        best_row[-1] = (colorama.Fore.GREEN + '   ' + u'\u2714' +
                        colorama.Style.RESET_ALL)
        # Highlight the chosen cluster.
        for i, cell in enumerate(best_row):
            best_row[i] = (f'{colorama.Style.BRIGHT}{cell}'
                           f'{colorama.Style.RESET_ALL}')

        # Print the price information.
        # In addition to the best price, show the worst-case price
        # that the user may have to pay in case of failover.
        # TODO(woosuk): apply a price cap (e.g., 20%) to bound the worst case.

        # This uses the knowledge that the chosen cluster will have the lowest
        # price among the feasible clusters, which may not be true in the future.
        best_price = chosen_cluster.get_hourly_price()
        worst_cluster = max(feasible_clusters,
                            key=lambda r: r.get_hourly_price())
        worst_price = worst_cluster.get_hourly_price()
        if best_price == worst_price:
            logger.info(f'Estimated price: ${best_price:.2f}/hr')
        else:
            logger.info('Estimated price: '
                        f'${best_price:.2f}/hr - ${worst_price:.2f}/hr')

        # Print the table.
        # Here we assume that every resource has the same num_nodes.
        num_nodes = chosen_cluster.num_nodes
        plural = 's' if num_nodes > 1 else ''
        logger.info(f'{colorama.Style.BRIGHT}'
                    f'Best resources ({num_nodes} node{plural}):'
                    f'{colorama.Style.RESET_ALL}')
        table = _create_table(columns)
        table.add_rows(rows)
        logger.info(f'{table}\n')

        return chosen_cluster
