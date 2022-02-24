"""The Sky optimizer: assigns best resources to user tasks."""
import collections
import enum
import pprint
from typing import Dict, List, Optional

import numpy as np
import tabulate

from sky import check
from sky import clouds
from sky import dag as dag_lib
from sky import exceptions
from sky import global_user_state
from sky import resources as resources_lib
from sky import sky_logging
from sky import task as task_lib

logger = sky_logging.init_logger(__name__)

Dag = dag_lib.Dag
Resources = resources_lib.Resources
Task = task_lib.Task

_DUMMY_SOURCE_NAME = 'sky-dummy-source'
_DUMMY_SINK_NAME = 'sky-dummy-sink'


# Constants: minimize what target?
class OptimizeTarget(enum.Enum):
    COST = 0
    TIME = 1


class Optimizer:
    """The Sky optimizer: assigns best resources to user tasks."""

    @staticmethod
    def _egress_cost(src_cloud: clouds.Cloud, dst_cloud: clouds.Cloud,
                     gigabytes: float):
        if isinstance(src_cloud, DummyCloud) or isinstance(
                dst_cloud, DummyCloud):
            return 0.0

        if not src_cloud.is_same_cloud(dst_cloud):
            egress_cost = src_cloud.get_egress_cost(num_gigabytes=gigabytes)
        else:
            egress_cost = 0.0
        if egress_cost > 0:
            logger.info(f'  {src_cloud} -> {dst_cloud} egress cost: '
                        f'${egress_cost} for {gigabytes:.1f} GB')
        return egress_cost

    @staticmethod
    def _egress_time(src_cloud: clouds.Cloud, dst_cloud: clouds.Cloud,
                     gigabytes: float):
        """Returns estimated egress time in seconds."""
        # FIXME: estimate bandwidth between each cloud-region pair.
        if isinstance(src_cloud, DummyCloud) or isinstance(
                dst_cloud, DummyCloud):
            return 0.0
        if not src_cloud.is_same_cloud(dst_cloud):
            # 10Gbps is close to the average of observed b/w from S3
            # (us-west,east-2) to GCS us-central1, assuming highly sharded files
            # (~128MB per file).
            bandwidth_gbps = 10
            egress_time = gigabytes * 8 / bandwidth_gbps
            logger.info(f'  {src_cloud} -> {dst_cloud} egress time: '
                        f'{egress_time} s for {gigabytes:.1f} GB')
        else:
            egress_time = 0.0
        return egress_time

    @staticmethod
    def optimize(
            dag: Dag,
            minimize=OptimizeTarget.COST,
            blocked_launchable_resources: Optional[List[Resources]] = None):
        # This function is effectful: mutates every node in 'dag' by setting
        # node.best_resources if it is None.
        dag = Optimizer._add_dummy_source_sink_nodes(dag)
        optimized_dag, unused_best_plan = Optimizer._optimize_cost(
            dag,
            minimize_cost=minimize == OptimizeTarget.COST,
            blocked_launchable_resources=blocked_launchable_resources)
        optimized_dag = Optimizer._remove_dummy_source_sink_nodes(optimized_dag)
        return optimized_dag

    @staticmethod
    def _add_dummy_source_sink_nodes(dag: Dag):
        """Adds special Source and Sink nodes.

        The two special nodes are for conveniently handling cases such as

          { A, B } --> C (multiple sources)

        or

          A --> { B, C } (multiple sinks)

        Adds new edges:
            Source --> for all node with in degree 0
            For all node with out degree 0 --> Sink
        """
        graph = dag.get_graph()
        zero_indegree_nodes = []
        zero_outdegree_nodes = []
        for node, in_degree in graph.in_degree():
            if in_degree == 0:
                zero_indegree_nodes.append(node)
        for node, out_degree in graph.out_degree():
            if out_degree == 0:
                zero_outdegree_nodes.append(node)

        def make_dummy(name):
            dummy = Task(name)
            dummy.set_resources({DummyResources(DummyCloud(), None)})
            dummy.set_time_estimator(lambda _: 0)
            return dummy

        with dag:
            source = make_dummy(_DUMMY_SOURCE_NAME)
            for real_source_node in zero_indegree_nodes:
                source >> real_source_node  # pylint: disable=pointless-statement
            sink = make_dummy(_DUMMY_SINK_NAME)
            for real_sink_node in zero_outdegree_nodes:
                real_sink_node >> sink  # pylint: disable=pointless-statement
        return dag

    @staticmethod
    def _remove_dummy_source_sink_nodes(dag: Dag):
        """Removes special Source and Sink nodes."""
        source = [t for t in dag.tasks if t.name == _DUMMY_SOURCE_NAME]
        sink = [t for t in dag.tasks if t.name == _DUMMY_SINK_NAME]
        assert len(source) == len(sink) == 1, dag.tasks
        dag.remove(source[0])
        dag.remove(sink[0])
        return dag

    @staticmethod
    def _egress_cost_or_time(minimize_cost: bool, parent: Task,
                             parent_resources: Resources, node: Task,
                             resources: Resources):
        """Computes the egress cost or time depending on 'minimize_cost'."""
        if isinstance(parent_resources.cloud, DummyCloud):
            # Special case.  The current 'node' is a real
            # source node, and its input may be on a different
            # cloud from 'resources'.
            if node.get_inputs() is None:
                # A Task may have no inputs specified.
                return 0
            src_cloud = node.get_inputs_cloud()
            nbytes = node.get_estimated_inputs_size_gigabytes()
        else:
            src_cloud = parent_resources.cloud
            nbytes = parent.get_estimated_outputs_size_gigabytes()
        dst_cloud = resources.cloud
        if minimize_cost:
            fn = Optimizer._egress_cost
        else:
            fn = Optimizer._egress_time
        return fn(src_cloud, dst_cloud, nbytes)

    @staticmethod
    def _optimize_cost(
        dag: Dag,
        minimize_cost: bool = True,
        blocked_launchable_resources: Optional[List[Resources]] = None,
    ):
        import networkx as nx  # pylint: disable=import-outside-toplevel
        # TODO: The output of this function is useful. Should generate a
        # text plan and print to both console and a log file.
        graph = dag.get_graph()
        topo_order = list(nx.topological_sort(graph))

        # FIXME: write to (node, cloud) as egress cost depends only on clouds.
        # node -> {resources -> best estimated cost}
        dp_best_cost = collections.defaultdict(dict)
        # d[node][resources][parent] = (best parent resources, best parent cost)
        dp_point_backs = collections.defaultdict(
            lambda: collections.defaultdict(dict))

        for node_i, node in enumerate(topo_order):
            # Base case: a special source node.
            if node_i == 0:
                dp_best_cost[node][list(node.get_resources())[0]] = 0
                continue
            # Don't print for the last node, Sink.
            do_print = node_i != len(topo_order) - 1
            if do_print:
                logger.debug('#### {} ####'.format(node))
            if node_i < len(topo_order) - 1:
                # Convert partial resource labels to launchable resources.
                launchable_resources = \
                    _fill_in_launchable_resources(
                        node,
                        blocked_launchable_resources
                    )
            else:
                # Dummy sink node.
                launchable_resources = node.get_resources()
                launchable_resources = {
                    list(node.get_resources())[0]: launchable_resources
                }
            num_resources = len(node.get_resources())
            parents = list(graph.predecessors(node))
            for orig_resources, launchable_list in launchable_resources.items():
                if not launchable_list:
                    raise exceptions.ResourcesUnavailableError(
                        f'No launchable resource found for task {node}. '
                        'To fix: relax its resource requirements.\n'
                        'Run \'sky show-gpus --cloud [CLOUD]\' to show'
                        ' available options.')
                if num_resources == 1 and node.time_estimator_func is None:
                    logger.info('Defaulting estimated time to 1 hr. '
                                'Call Task.set_time_estimator() to override.')
                    estimated_runtime = 1 * 3600
                else:
                    # We assume the time estimator takes in a partial resource
                    #    Resources('V100')
                    # and treats their launchable versions
                    #    Resources(AWS, 'p3.2xlarge'),
                    #    Resources(GCP, '...', 'V100'),
                    #    ...
                    # as having the same run time.
                    # FIXME(zongheng): take 'num_nodes' as an arg/into
                    # account. It may be another reason to treat num_nodes as
                    # part of a Resources.
                    estimated_runtime = node.estimate_runtime(orig_resources)
                for resources in launchable_list:
                    # Computes dp_best_cost[node][resources]
                    # = my estimated cost +
                    #      sum_p min_phw { dp_best_cost(p, phw) +
                    #                      egress_cost(p, phw, hw) }
                    # where p in Parents(node).
                    assert resources not in dp_best_cost[node]
                    if do_print:
                        logger.debug(f'resources: {resources}')

                    if minimize_cost:
                        cost_per_node = resources.get_cost(estimated_runtime)
                        estimated_cost = cost_per_node * node.num_nodes
                    else:
                        # Minimize run time; overload the term 'cost'.
                        estimated_cost = estimated_runtime
                    if do_print:
                        logger.debug(
                            '  estimated_runtime: {:.0f} s ({:.1f} hr)'.format(
                                estimated_runtime, estimated_runtime / 3600))
                        if minimize_cost:
                            logger.debug(
                                '  estimated_cost (not incl. egress): ${:.1f}'.
                                format(estimated_cost))

                    # FIXME: Account for egress costs for multi-node clusters
                    sum_parent_cost_and_egress = 0
                    for parent in parents:
                        min_pred_cost_plus_egress = np.inf
                        for parent_resources, parent_cost in dp_best_cost[
                                parent].items():
                            egress_cost = Optimizer._egress_cost_or_time(
                                minimize_cost, parent, parent_resources, node,
                                resources)
                            if parent_cost + egress_cost < \
                               min_pred_cost_plus_egress:
                                min_pred_cost_plus_egress = \
                                    parent_cost + egress_cost
                                best_parent_hardware = parent_resources
                        sum_parent_cost_and_egress += min_pred_cost_plus_egress
                        dp_point_backs[node][resources][parent] = (
                            best_parent_hardware, min_pred_cost_plus_egress)
                    dp_best_cost[node][
                        resources] = estimated_cost + sum_parent_cost_and_egress

        # Dict: node -> (resources, cost).
        best_plan = Optimizer.read_optimized_plan(dp_best_cost, topo_order,
                                                  dp_point_backs, minimize_cost)

        # If it's 1 resource choice, the info is already printed.
        should_print = any(len(v) > 1 for v in dp_best_cost.values())
        if should_print:
            dp_best_cost = {
                k: v
                for k, v in dp_best_cost.items()
                if k.name not in (_DUMMY_SOURCE_NAME, _DUMMY_SINK_NAME)
            }
            metric = 'cost' if minimize_cost else 'time'
            if len(dp_best_cost) > 1:
                logger.info(f'Details: task -> {{resources -> {metric}}}')
                logger.info('%s\n', pprint.pformat(dp_best_cost))
            elif len(dp_best_cost) == 1:
                logger.info(f'Considered resources -> {metric}')
                logger.info('%s\n',
                            pprint.pformat(list(dp_best_cost.values())[0]))

        return dag, best_plan

    @staticmethod
    def read_optimized_plan(dp_best_cost, topo_order, dp_point_backs,
                            minimize_cost):
        message_data = []
        best_plan = {}

        def _walk(node, best_hardware, best_cost):
            if node.best_resources is None:
                # Record the best decision for 'node'.
                message_data.append((node, best_hardware))
                best_plan[node] = (best_hardware, best_cost)
                node.best_resources = best_hardware
            # Recurse back to parent(s).
            for tup in dp_point_backs[node][best_hardware].items():
                parent, (parent_best_hardware, parent_best_cost) = tup
                _walk(parent, parent_best_hardware, parent_best_cost)

        # Start at Sink, the last node in the topo order.
        node = topo_order[-1]
        # Find the best (hardware, cost) for node.
        best_costs = dp_best_cost[node]
        assert len(best_costs) == 1, f'Should be DummyCloud: {best_costs}'
        h, overall_best = list(best_costs.items())[0]
        _walk(node, h, overall_best)

        if minimize_cost:
            logger.info('Optimizer - plan minimizing cost (~${:.1f}):'.format(
                overall_best))
        else:
            logger.info(
                'Optimizer - plan minimizing run time (~{:.1f} hr):'.format(
                    overall_best / 3600))
        # Do not print Source or Sink.
        message_data = [
            (t, f'{t.num_nodes}x {repr(r)}' if t.num_nodes > 1 else repr(r))
            for (t, r) in message_data
            if t.name not in (_DUMMY_SOURCE_NAME, _DUMMY_SINK_NAME)
        ]
        message = tabulate.tabulate(reversed(message_data),
                                    headers=['TASK', 'BEST_RESOURCE'],
                                    tablefmt='plain')
        logger.info(f'\n{message}\n')
        return best_plan


class DummyResources(Resources):
    """A dummy Resources that has zero egress cost from/to."""

    _REPR = 'DummyCloud'

    def __repr__(self) -> str:
        return DummyResources._REPR

    def get_cost(self, seconds):
        return 0


class DummyCloud(clouds.Cloud):
    """A dummy Cloud that has zero egress cost from/to."""
    pass


def _cloud_in_list(cloud: clouds.Cloud, lst: List[clouds.Cloud]) -> bool:
    return any(cloud.is_same_cloud(c) for c in lst)


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


def _fill_in_launchable_resources(
    task: Task,
    blocked_launchable_resources: Optional[List[Resources]],
    try_fix_with_sky_check: bool = True,
) -> Dict[Resources, List[Resources]]:
    enabled_clouds = global_user_state.get_enabled_clouds()
    if len(enabled_clouds) == 0 and try_fix_with_sky_check:
        check.check(quiet=True)
        return _fill_in_launchable_resources(task, blocked_launchable_resources,
                                             False)
    launchable = collections.defaultdict(list)
    if blocked_launchable_resources is None:
        blocked_launchable_resources = []
    for resources in task.get_resources():
        if resources.cloud is not None and not _cloud_in_list(
                resources.cloud, enabled_clouds):
            if try_fix_with_sky_check:
                check.check(quiet=True)
                return _fill_in_launchable_resources(
                    task, blocked_launchable_resources, False)
            raise exceptions.ResourcesUnavailableError(
                f'Task {task} requires {resources.cloud} which is not '
                'enabled. Run `sky check` to enable access to it, '
                'or change the cloud requirement.')
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
