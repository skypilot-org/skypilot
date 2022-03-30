"""The Sky optimizer: assigns best resources to user tasks."""
import collections
import colorama
import enum
import pprint
import sys
import typing
from typing import Dict, List, Optional, Tuple

import numpy as np
import tabulate

from sky import check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import resources as resources_lib
from sky import sky_logging
from sky import task as task_lib

if typing.TYPE_CHECKING:
    from sky import dag as dag_lib

logger = sky_logging.init_logger(__name__)

Task = task_lib.Task

_DUMMY_SOURCE_NAME = 'sky-dummy-source'
_DUMMY_SINK_NAME = 'sky-dummy-sink'

# task -> resources -> estimated cost or time.
_TaskToCostMap = Dict[Task, Dict[resources_lib.Resources, float]]
# cloud -> list of resources that have the same accelerators.
_PerCloudCandidates = Dict[clouds.Cloud, List[resources_lib.Resources]]
# task -> per-cloud candidates
_TaskToPerCloudCandidates = Dict[Task, _PerCloudCandidates]


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
        else:
            egress_time = 0.0
        return egress_time

    @staticmethod
    def optimize(dag: 'dag_lib.Dag',
                 minimize=OptimizeTarget.COST,
                 blocked_launchable_resources: Optional[List[
                     resources_lib.Resources]] = None,
                 raise_error: bool = False):
        # This function is effectful: mutates every node in 'dag' by setting
        # node.best_resources if it is None.
        dag = Optimizer._add_dummy_source_sink_nodes(dag)
        optimized_dag, unused_best_plan = Optimizer._optimize_cost(
            dag,
            minimize_cost=minimize == OptimizeTarget.COST,
            blocked_launchable_resources=blocked_launchable_resources,
            raise_error=raise_error)
        optimized_dag = Optimizer._remove_dummy_source_sink_nodes(optimized_dag)
        return optimized_dag

    @staticmethod
    def _add_dummy_source_sink_nodes(dag: 'dag_lib.Dag'):
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
    def _remove_dummy_source_sink_nodes(dag: 'dag_lib.Dag'):
        """Removes special Source and Sink nodes."""
        source = [t for t in dag.tasks if t.name == _DUMMY_SOURCE_NAME]
        sink = [t for t in dag.tasks if t.name == _DUMMY_SINK_NAME]
        assert len(source) == len(sink) == 1, dag.tasks
        dag.remove(source[0])
        dag.remove(sink[0])
        return dag

    @staticmethod
    def _get_egress_info(
        parent: Task,
        parent_resources: resources_lib.Resources,
        node: Task,
        resources: resources_lib.Resources,
    ) -> Tuple[clouds.Cloud, clouds.Cloud, float]:
        if isinstance(parent_resources.cloud, DummyCloud):
            # Special case.  The current 'node' is a real
            # source node, and its input may be on a different
            # cloud from 'resources'.
            if node.get_inputs() is None:
                # A Task may have no inputs specified.
                return None, None, 0
            src_cloud = node.get_inputs_cloud()
            nbytes = node.get_estimated_inputs_size_gigabytes()
        else:
            src_cloud = parent_resources.cloud
            nbytes = parent.get_estimated_outputs_size_gigabytes()
        dst_cloud = resources.cloud
        return src_cloud, dst_cloud, nbytes

    @staticmethod
    def _egress_cost_or_time(minimize_cost: bool, parent: Task,
                             parent_resources: resources_lib.Resources,
                             node: Task, resources: resources_lib.Resources):
        """Computes the egress cost or time depending on 'minimize_cost'."""
        src_cloud, dst_cloud, nbytes = Optimizer._get_egress_info(
            parent, parent_resources, node, resources)
        if nbytes == 0:
            return 0

        if minimize_cost:
            fn = Optimizer._egress_cost
        else:
            fn = Optimizer._egress_time
        return fn(src_cloud, dst_cloud, nbytes)

    @staticmethod
    def _estimate_nodes_cost_or_time(
        topo_order: List[Task],
        minimize_cost: bool = True,
        blocked_launchable_resources: Optional[List[
            resources_lib.Resources]] = None,
        raise_error: bool = False,
    ) -> Tuple[_TaskToCostMap, _TaskToPerCloudCandidates]:
        """Estimates the cost/time of each task-resource mapping in the DAG.

        Note that the egress cost/time is not considered in this function.
        The estimated run time of a task running on a resource is given by
        `task.estimate_runtime(resources)` or 1 hour by default.
        The estimated cost is `task.num_nodes * resources.get_cost(runtime)`.
        """
        # Cost of running the task on the resources.
        # node -> {resources -> cost}
        node_to_cost_map: _TaskToCostMap = collections.defaultdict(dict)

        # node -> cloud -> list of resources that satisfy user's requirements.
        node_to_candidate_map: _TaskToPerCloudCandidates = {}

        # Compute the estimated cost/time for each node.
        for node_i, node in enumerate(topo_order):
            if node_i == 0:
                # Base case: a special source node.
                node_to_cost_map[node][list(node.get_resources())[0]] = 0
                continue

            # Don't print for the last node, Sink.
            do_print = node_i != len(topo_order) - 1
            if do_print:
                logger.debug('#### {} ####'.format(node))

            if node_i < len(topo_order) - 1:
                # Convert partial resource labels to launchable resources.
                launchable_resources, cloud_candidates = \
                    _fill_in_launchable_resources(
                        node,
                        blocked_launchable_resources
                    )
                node_to_candidate_map[node] = cloud_candidates
            else:
                # Dummy sink node.
                launchable_resources = node.get_resources()
                launchable_resources = {
                    list(node.get_resources())[0]: launchable_resources
                }

            num_resources = len(node.get_resources())
            for orig_resources, launchable_list in launchable_resources.items():
                if not launchable_list:
                    error_msg = (
                        f'No launchable resource found for task {node}. '
                        'To fix: relax its resource requirements.\n'
                        'Hint: \'sky show-gpus --all\' '
                        'to list available accelerators.\n'
                        '      \'sky check\' to check the enabled clouds.')
                    if raise_error:
                        raise exceptions.ResourcesUnavailableError(error_msg)
                    else:
                        logger.error(error_msg)
                        sys.exit(1)
                if num_resources == 1 and node.time_estimator_func is None:
                    logger.debug(
                        'Defaulting the task\'s estimated time to 1 hour.')
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
                    node_to_cost_map[node][resources] = estimated_cost
        return node_to_cost_map, node_to_candidate_map

    @staticmethod
    def _optimize_by_dp(
        topo_order: List[Task],
        node_to_cost_map: _TaskToCostMap,
        minimize_cost: bool = True,
    ) -> Tuple[Dict[Task, resources_lib.Resources], float]:
        """Optimizes a chain DAG using a dynamic programming algorithm."""
        # node -> { resources -> best estimated cost }
        dp_best_obj = collections.defaultdict(dict)
        # node -> { resources -> best parent resources }
        dp_point_backs = collections.defaultdict(dict)

        # Computes dp_best_obj[node][resources]
        # = my estimated cost + min_phw { dp_best_obj(p, phw) +
        #                                 egress_cost(p, phw, hw) }
        # where p is the parent of the node.
        for node_i, node in enumerate(topo_order):
            if node_i == 0:
                # Base case: a special source node.
                dp_best_obj[node][list(node.get_resources())[0]] = 0
                continue

            parent = topo_order[node_i - 1]
            # FIXME: Account for egress costs for multi-node clusters
            for resources, execution_cost in node_to_cost_map[node].items():
                min_pred_cost_plus_egress = np.inf
                for parent_resources, parent_cost in \
                    dp_best_obj[parent].items():
                    egress_cost = Optimizer._egress_cost_or_time(
                        minimize_cost, parent, parent_resources, node,
                        resources)

                    if parent_cost + egress_cost < min_pred_cost_plus_egress:
                        min_pred_cost_plus_egress = parent_cost + egress_cost
                        best_parent_hardware = parent_resources

                dp_point_backs[node][resources] = best_parent_hardware
                dp_best_obj[node][resources] = \
                    execution_cost + min_pred_cost_plus_egress

        # Compute the total objective value of the DAG.
        sink_node = topo_order[-1]
        total_obj = dp_best_obj[sink_node]
        assert len(total_obj) == 1, f'Should be DummyCloud: {total_obj}'
        best_resources, best_total_obj = list(total_obj.items())[0]

        # Find the best plan for the DAG.
        # node -> best resources
        best_plan = {}
        for node in reversed(topo_order):
            best_plan[node] = best_resources
            node.best_resources = best_resources
            if node.name != _DUMMY_SOURCE_NAME:
                best_resources = dp_point_backs[node][best_resources]
        return best_plan, best_total_obj

    @staticmethod
    def _compute_total_time(
        graph,
        topo_order: List[Task],
        plan: Dict[Task, resources_lib.Resources],
    ) -> float:
        """Estimates the total time of running the DAG by the plan."""
        cache_finish_time = {}

        def finish_time(node):
            if node in cache_finish_time:
                return cache_finish_time[node]

            resources = plan[node]
            if node.time_estimator_func is None:
                execution_time = 1 * 3600
            else:
                # The execution time of dummy nodes is always 0,
                # as they have a time estimator lambda _: 0.
                execution_time = node.estimate_runtime(resources)

            pred_finish_times = [0]
            for pred in graph.predecessors(node):
                # FIXME: Account for egress time for multi-node clusters
                egress_time = Optimizer._egress_cost_or_time(
                    False, pred, plan[pred], node, resources)
                pred_finish_times.append(finish_time(pred) + egress_time)

            cache_finish_time[node] = execution_time + max(pred_finish_times)
            return cache_finish_time[node]

        sink_node = topo_order[-1]
        return finish_time(sink_node)

    @staticmethod
    def _compute_total_cost(
        graph,
        topo_order: List[Task],
        plan: Dict[Task, resources_lib.Resources],
    ) -> float:
        """Estimates the total cost of running the DAG by the plan."""
        total_cost = 0
        for node in topo_order:
            resources = plan[node]
            if node.time_estimator_func is None:
                execution_time = 1 * 3600
            else:
                # The execution time of dummy nodes is always 0,
                # as they have a time estimator lambda _: 0.
                execution_time = node.estimate_runtime(resources)

            cost_per_node = resources.get_cost(execution_time)
            total_cost += cost_per_node * node.num_nodes

            for pred in graph.predecessors(node):
                # FIXME: Account for egress costs for multi-node clusters
                egress_cost = Optimizer._egress_cost_or_time(
                    True, pred, plan[pred], node, resources)
                total_cost += egress_cost
        return total_cost

    @staticmethod
    def _print_egress_plan(graph, plan, minimize_cost):
        message_data = []
        for parent, child in graph.edges():
            src_cloud, dst_cloud, nbytes = Optimizer._get_egress_info(
                parent, plan[parent], child, plan[child])
            if nbytes == 0:
                continue

            if minimize_cost:
                fn = Optimizer._egress_cost
            else:
                fn = Optimizer._egress_time
            cost_or_time = fn(src_cloud, dst_cloud, nbytes)

            if cost_or_time > 0:
                if parent.name == _DUMMY_SOURCE_NAME:
                    egress = (f'{child.get_inputs()} ({src_cloud}) -> '
                              f'{child} ({dst_cloud})')
                else:
                    egress = f'{parent} ({src_cloud}) -> {child} ({dst_cloud})'
                message_data.append((egress, nbytes, cost_or_time))

        if message_data:
            metric = 'COST ($)' if minimize_cost else 'TIME (s)'
            message = tabulate.tabulate(
                reversed(message_data),
                headers=['EGRESS', 'SIZE (GB)', metric],
                tablefmt='plain',
                numalign='right',
            )
            logger.info(f'{message}\n')

    @staticmethod
    def print_optimized_plan(
        graph,
        best_plan: Dict[Task, resources_lib.Resources],
        total_time: float,
        total_cost: float,
        node_to_cost_map: _TaskToCostMap,
        minimize_cost: bool,
    ):
        if minimize_cost:
            logger.info('Optimizer - plan minimizing cost')
        else:
            logger.info('Optimizer - plan minimizing run time')
        logger.info(f'Estimated total run time: ~{total_time / 3600:.1f} hr, '
                    f'total cost: ~${total_cost:.1f}')

        # Do not print Source or Sink.
        message_data = [
            (t, f'{t.num_nodes}x {repr(r)}' if t.num_nodes > 1 else repr(r))
            for t, r in best_plan.items()
            if t.name not in (_DUMMY_SOURCE_NAME, _DUMMY_SINK_NAME)
        ]
        message = tabulate.tabulate(reversed(message_data),
                                    headers=['TASK', 'BEST_RESOURCE'],
                                    tablefmt='plain')
        logger.info(f'\n{message}\n')

        Optimizer._print_egress_plan(graph, best_plan, minimize_cost)

        # Print the list of resouces that the optimizer considered.
        should_print = any(len(v) > 1 for v in node_to_cost_map.values())
        if should_print:
            node_to_cost_map = {
                k: v
                for k, v in node_to_cost_map.items()
                if k.name not in (_DUMMY_SOURCE_NAME, _DUMMY_SINK_NAME)
            }
            metric = 'cost ($)' if minimize_cost else 'time (hr)'
            for k, v in node_to_cost_map.items():
                node_to_cost_map[k] = {
                    resources: round(cost, 2) if minimize_cost \
                        else round(cost / 3600, 2)
                    for resources, cost in v.items()
                }

            num_tasks = len(node_to_cost_map)
            if num_tasks > 1:
                logger.info(f'Details: task -> {{resources -> {metric}}}')
                logger.info('%s\n', pprint.pformat(node_to_cost_map))
            elif num_tasks == 1:
                logger.info(f'Considered resources -> {metric}')
                logger.info('%s\n',
                            pprint.pformat(list(node_to_cost_map.values())[0]))

    @staticmethod
    def _print_candidates(node_to_candidate_map: _TaskToPerCloudCandidates):
        for node, candidate_set in node_to_candidate_map.items():
            accelerator = list(node.get_resources())[0].accelerators
            is_multi_instances = False
            if accelerator:
                acc_name, acc_count = list(accelerator.items())[0]
                for cloud, candidate_list in candidate_set.items():
                    if len(candidate_list) > 1:
                        is_multi_instances = True
                        instance_list = [
                            res.instance_type for res in candidate_list
                        ]
                        logger.info(
                            f'Multiple {cloud} instances satisfy '
                            f'{acc_name}:{int(acc_count)}. '
                            f'The cheapest {candidate_list[0]!r} is considered '
                            f'among:\n{instance_list}.\n')
            if is_multi_instances:
                logger.info(
                    f'To list more details, run \'sky show-gpus {acc_name}\'.')

    @staticmethod
    def _optimize_cost(
        dag: 'dag_lib.Dag',
        minimize_cost: bool = True,
        blocked_launchable_resources: Optional[List[
            resources_lib.Resources]] = None,
        raise_error: bool = False,
    ) -> Tuple['dag_lib.Dag', Dict[Task, resources_lib.Resources]]:
        """Finds the optimal task-resource mapping for the entire DAG.

        The optimal mapping should consider the egress cost/time so that
        the total estimated cost/time of the DAG becomes the minimum.
        """
        import networkx as nx  # pylint: disable=import-outside-toplevel
        # TODO: The output of this function is useful. Should generate a
        # text plan and print to both console and a log file.

        graph = dag.get_graph()
        topo_order = list(nx.topological_sort(graph))

        node_to_cost_map, node_to_candidate_map = \
            Optimizer._estimate_nodes_cost_or_time(
                topo_order,
                minimize_cost,
                blocked_launchable_resources,
                raise_error)

        if dag.is_chain():
            best_plan, best_total_obj = Optimizer._optimize_by_dp(
                topo_order, node_to_cost_map, minimize_cost)
        else:
            raise NotImplementedError('Currently Sky only supports chain DAGs.')

        if minimize_cost:
            total_time = Optimizer._compute_total_time(graph, topo_order,
                                                       best_plan)
            total_cost = best_total_obj
        else:
            total_time = best_total_obj
            total_cost = Optimizer._compute_total_cost(graph, topo_order,
                                                       best_plan)

        Optimizer.print_optimized_plan(graph, best_plan, total_time, total_cost,
                                       node_to_cost_map, minimize_cost)
        Optimizer._print_candidates(node_to_candidate_map)
        return dag, best_plan


class DummyResources(resources_lib.Resources):
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
        launchable_resources: List[resources_lib.Resources],
        blocked_launchable_resources: List[resources_lib.Resources]):
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
    blocked_launchable_resources: Optional[List[resources_lib.Resources]],
    try_fix_with_sky_check: bool = True,
) -> Tuple[Dict[resources_lib.Resources, List[resources_lib.Resources]],
           _PerCloudCandidates]:
    enabled_clouds = global_user_state.get_enabled_clouds()
    if len(enabled_clouds) == 0 and try_fix_with_sky_check:
        check.check(quiet=True)
        return _fill_in_launchable_resources(task, blocked_launchable_resources,
                                             False)
    launchable = collections.defaultdict(list)
    cloud_candidates = collections.defaultdict(resources_lib.Resources)
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
        else:
            clouds_list = [resources.cloud
                          ] if resources.cloud is not None else enabled_clouds
            all_fuzzy_candidates = set()
            for cloud in clouds_list:
                (feasible_resources, fuzzy_candidate_list
                ) = cloud.get_feasible_launchable_resources(resources)
                if len(feasible_resources) > 0:
                    # Assume feasible_resources is sorted by prices and
                    # only append the cheapest option for each cloud
                    launchable[resources].append(feasible_resources[0])
                    cloud_candidates[cloud] = feasible_resources
                else:
                    all_fuzzy_candidates.update(fuzzy_candidate_list)
            if len(launchable[resources]) == 0:
                logger.info(f'No resource satisfying {resources.accelerators} '
                            f'on {clouds_list}.')
                if len(all_fuzzy_candidates) > 0:
                    logger.info('Did you mean: '
                                f'{colorama.Fore.CYAN}'
                                f'{sorted(all_fuzzy_candidates)}'
                                f'{colorama.Style.RESET_ALL}')

        launchable[resources] = _filter_out_blocked_launchable_resources(
            launchable[resources], blocked_launchable_resources)

    return launchable, cloud_candidates
