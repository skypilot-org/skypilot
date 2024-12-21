"""Optimizer: assigns best resources to user tasks."""
import collections
import copy
import enum
import json
import typing
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import colorama
import numpy as np
import prettytable

from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import resources as resources_lib
from sky import sky_logging
from sky import task as task_lib
from sky.adaptors import common as adaptors_common
from sky.utils import env_options
from sky.utils import log_utils
from sky.utils import resources_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import networkx as nx

    from sky import dag as dag_lib
else:
    nx = adaptors_common.LazyImport('networkx')

logger = sky_logging.init_logger(__name__)

_DUMMY_SOURCE_NAME = 'skypilot-dummy-source'
_DUMMY_SINK_NAME = 'skypilot-dummy-sink'

# task -> resources -> estimated cost or time.
_TaskToCostMap = Dict[task_lib.Task, Dict[resources_lib.Resources, float]]
# cloud -> list of resources that have the same accelerators.
_PerCloudCandidates = Dict[clouds.Cloud, List[resources_lib.Resources]]
# task -> per-cloud candidates
_TaskToPerCloudCandidates = Dict[task_lib.Task, _PerCloudCandidates]


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


def _is_dag_resources_ordered(dag: 'dag_lib.Dag') -> bool:
    graph = dag.get_graph()
    topo_order = list(nx.topological_sort(graph))
    for node in topo_order:
        if isinstance(node.resources, list):
            return True
    return False


class Optimizer:
    """Optimizer: assigns best resources to user tasks."""

    @staticmethod
    def _egress_cost(src_cloud: clouds.Cloud, dst_cloud: clouds.Cloud,
                     gigabytes: float) -> float:
        """Returns estimated egress cost."""
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
                     gigabytes: float) -> float:
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
    @timeline.event
    def optimize(dag: 'dag_lib.Dag',
                 minimize: OptimizeTarget = OptimizeTarget.COST,
                 blocked_resources: Optional[Iterable[
                     resources_lib.Resources]] = None,
                 quiet: bool = False):
        """Find the best execution plan for the given DAG.

        Args:
            dag: the DAG to optimize.
            minimize: whether to minimize cost or time.
            blocked_resources: a list of resources that should not be used.
            quiet: whether to suppress logging.

        Raises:
            exceptions.ResourcesUnavailableError: if no resources are available
                for a task.
            exceptions.NoCloudAccessError: if no public clouds are enabled.
        """
        with rich_utils.safe_status(ux_utils.spinner_message('Optimizing')):
            _check_specified_clouds(dag)

            # This function is effectful: mutates every node in 'dag' by setting
            # node.best_resources if it is None.
            Optimizer._add_dummy_source_sink_nodes(dag)
            try:
                unused_best_plan = Optimizer._optimize_dag(
                    dag=dag,
                    minimize_cost=minimize == OptimizeTarget.COST,
                    blocked_resources=blocked_resources,
                    quiet=quiet)
            finally:
                # Make sure to remove the dummy source/sink nodes, even if the
                # optimization fails.
                Optimizer._remove_dummy_source_sink_nodes(dag)
            return dag

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
            dummy = task_lib.Task(name)
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

    @staticmethod
    def _remove_dummy_source_sink_nodes(dag: 'dag_lib.Dag'):
        """Removes special Source and Sink nodes."""
        source = [t for t in dag.tasks if t.name == _DUMMY_SOURCE_NAME]
        sink = [t for t in dag.tasks if t.name == _DUMMY_SINK_NAME]
        if not source and not sink:
            return
        assert len(source) == len(sink) == 1, dag.tasks
        dag.remove(source[0])
        dag.remove(sink[0])

    @staticmethod
    def _get_egress_info(
        parent: task_lib.Task,
        parent_resources: resources_lib.Resources,
        node: task_lib.Task,
        resources: resources_lib.Resources,
    ) -> Tuple[Optional[clouds.Cloud], Optional[clouds.Cloud], Optional[float]]:
        if isinstance(parent_resources.cloud, DummyCloud):
            # Special case.  The current 'node' is a real
            # source node, and its input may be on a different
            # cloud from 'resources'.
            if node.get_inputs() is None:
                # A task_lib.Task may have no inputs specified.
                return None, None, 0
            src_cloud = node.get_inputs_cloud()
            nbytes = node.get_estimated_inputs_size_gigabytes()
        else:
            src_cloud = parent_resources.cloud
            nbytes = parent.get_estimated_outputs_size_gigabytes()
        dst_cloud = resources.cloud
        return src_cloud, dst_cloud, nbytes

    @staticmethod
    def _egress_cost_or_time(minimize_cost: bool, parent: task_lib.Task,
                             parent_resources: resources_lib.Resources,
                             node: task_lib.Task,
                             resources: resources_lib.Resources):
        """Computes the egress cost or time depending on 'minimize_cost'."""
        src_cloud, dst_cloud, nbytes = Optimizer._get_egress_info(
            parent, parent_resources, node, resources)
        if not nbytes:
            # nbytes can be None, if the task has no inputs/outputs.
            return 0
        assert src_cloud is not None and dst_cloud is not None, (src_cloud,
                                                                 dst_cloud,
                                                                 nbytes)

        if minimize_cost:
            fn = Optimizer._egress_cost
        else:
            fn = Optimizer._egress_time
        return fn(src_cloud, dst_cloud, nbytes)

    @staticmethod
    def _estimate_nodes_cost_or_time(
        topo_order: List[task_lib.Task],
        minimize_cost: bool = True,
        blocked_resources: Optional[Iterable[resources_lib.Resources]] = None,
        quiet: bool = False
    ) -> Tuple[_TaskToCostMap, _TaskToPerCloudCandidates]:
        """Estimates the cost/time of each task-resource mapping in the DAG.

        Note that the egress cost/time is not considered in this function.
        The estimated run time of a task running on a resource is given by
        `task.estimate_runtime(resources)` or 1 hour by default.
        The estimated cost is `task.num_nodes * resources.get_cost(runtime)`.
        """
        # Cost/time of running the task on the resources.
        # node -> {resources -> cost/time}
        node_to_cost_map: _TaskToCostMap = collections.defaultdict(dict)

        # node -> cloud -> list of resources that satisfy user's requirements.
        node_to_candidate_map: _TaskToPerCloudCandidates = {}

        def get_available_reservations(
            launchable_resources: Dict[resources_lib.Resources,
                                       List[resources_lib.Resources]]
        ) -> Dict[resources_lib.Resources, int]:
            if not resources_utils.need_to_query_reservations():
                return {}

            num_available_reserved_nodes_per_resource = {}

            def get_reservations_available_resources(
                    resources: resources_lib.Resources):
                num_available_reserved_nodes_per_resource[resources] = sum(
                    resources.get_reservations_available_resources().values())

            launchable_resources_list: List[resources_lib.Resources] = sum(
                launchable_resources.values(), [])
            with rich_utils.safe_status(
                    ux_utils.spinner_message('Checking reserved resources')):
                subprocess_utils.run_in_parallel(
                    get_reservations_available_resources,
                    launchable_resources_list)
            return num_available_reserved_nodes_per_resource

        # Compute the estimated cost/time for each node.
        for node_i, node in enumerate(topo_order):
            if node_i == 0:
                # Base case: a special source node.
                node_to_cost_map[node][list(node.resources)[0]] = 0
                continue

            # Don't print for the last node, Sink.
            do_print = node_i != len(topo_order) - 1
            if do_print:
                logger.debug('#### {} ####'.format(node))

            fuzzy_candidates: List[str] = []
            if node_i < len(topo_order) - 1:
                # Convert partial resource labels to launchable resources.
                launchable_resources, cloud_candidates, fuzzy_candidates = (
                    _fill_in_launchable_resources(
                        task=node,
                        blocked_resources=blocked_resources,
                        quiet=quiet))
                node_to_candidate_map[node] = cloud_candidates
            else:
                # Dummy sink node.
                launchable_resources = {
                    list(node.resources)[0]: list(node.resources)
                }

            # Fetch reservations in advance and in parallel to speed up the
            # reservation info fetching.
            num_resources = len(list(node.resources))
            num_available_reserved_nodes_per_resource = (
                get_available_reservations(launchable_resources))

            for orig_resources, launchable_list in launchable_resources.items():
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
                    if node.time_estimator_func is None:
                        estimated_runtime = 1 * 3600
                    else:
                        estimated_runtime = node.estimate_runtime(
                            orig_resources)

                for resources in launchable_list:
                    if do_print:
                        logger.debug(f'resources: {resources}')

                    if minimize_cost:
                        cost_per_node = resources.get_cost(estimated_runtime)
                        num_available_reserved_nodes = (
                            num_available_reserved_nodes_per_resource.get(
                                resources, 0))

                        # We consider the cost of the unused reservation
                        # resources to be 0 since we are already paying for
                        # them.
                        # TODO: different policies can be applied here for
                        # whether to choose reserved instances.
                        estimated_cost_or_time = cost_per_node * max(
                            node.num_nodes - num_available_reserved_nodes, 0)
                    else:
                        # Minimize run time.
                        estimated_cost_or_time = estimated_runtime
                    if do_print:
                        logger.debug(
                            '  estimated_runtime: {:.0f} s ({:.1f} hr)'.format(
                                estimated_runtime, estimated_runtime / 3600))
                        if minimize_cost:
                            logger.debug(
                                '  estimated_cost (not incl. egress): ${:.1f}'.
                                format(estimated_cost_or_time))
                    node_to_cost_map[node][resources] = estimated_cost_or_time
            if not node_to_cost_map[node]:
                source_hint = 'catalog'
                # If Kubernetes was included in the search space, then
                # mention "kubernetes cluster" and/instead of "catalog"
                # in the error message.
                enabled_clouds = (
                    sky_check.get_cached_enabled_clouds_or_refresh())
                if clouds.cloud_in_iterable(clouds.Kubernetes(),
                                            enabled_clouds):
                    if any(orig_resources.cloud is None
                           for orig_resources in node.resources):
                        source_hint = 'catalog and kubernetes cluster'
                    elif all(
                            isinstance(orig_resources.cloud, clouds.Kubernetes)
                            for orig_resources in node.resources):
                        source_hint = 'kubernetes cluster'

                bold = colorama.Style.BRIGHT
                cyan = colorama.Fore.CYAN
                reset = colorama.Style.RESET_ALL
                fuzzy_candidates_str = ''
                if fuzzy_candidates:
                    fuzzy_candidates_str = (
                        f'\nTry one of these offered accelerators: {cyan}'
                        f'{fuzzy_candidates}{reset}')
                node_resources_reprs = ', '.join(f'{node.num_nodes}x ' +
                                                 r.repr_with_region_zone
                                                 for r in node.resources)
                error_msg = (
                    f'{source_hint.capitalize()} does not contain any '
                    f'instances satisfying the request: '
                    f'{node_resources_reprs}.'
                    f'\nTo fix: relax or change the '
                    f'resource requirements.{fuzzy_candidates_str}\n\n'
                    f'Hint: {bold}sky show-gpus{reset} '
                    'to list available accelerators.\n'
                    f'      {bold}sky check{reset} to check the enabled '
                    'clouds.')
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesUnavailableError(error_msg)
        return node_to_cost_map, node_to_candidate_map

    @staticmethod
    def _optimize_by_dp(
        topo_order: List[task_lib.Task],
        node_to_cost_map: _TaskToCostMap,
        minimize_cost: bool = True,
    ) -> Tuple[Dict[task_lib.Task, resources_lib.Resources], float]:
        """Optimizes a chain DAG using a dynamic programming algorithm."""
        # node -> { resources -> best estimated cost }
        dp_best_objective: Dict[task_lib.Task,
                                Dict[resources_lib.Resources,
                                     float]] = collections.defaultdict(dict)
        # node -> { resources -> best parent resources }
        dp_point_backs: Dict[task_lib.Task, Dict[
            resources_lib.Resources,
            resources_lib.Resources]] = collections.defaultdict(dict)

        # Computes dp_best_objective[node][resources]
        # = my estimated cost + min_phw { dp_best_objective(p, phw) +
        #                                 egress_cost(p, phw, hw) }
        # where p is the parent of the node.
        for node_i, node in enumerate(topo_order):
            if node_i == 0:
                # Base case: a special source node.
                dp_best_objective[node][list(node.resources)[0]] = 0
                continue

            parent = topo_order[node_i - 1]
            # FIXME: Account for egress costs for multi-node clusters
            for resources, execution_cost in node_to_cost_map[node].items():
                min_pred_cost_plus_egress = np.inf
                for parent_resources, parent_cost in \
                    dp_best_objective[parent].items():
                    egress_cost = Optimizer._egress_cost_or_time(
                        minimize_cost, parent, parent_resources, node,
                        resources)

                    if parent_cost + egress_cost < min_pred_cost_plus_egress:
                        min_pred_cost_plus_egress = parent_cost + egress_cost
                        best_parent_hardware = parent_resources

                dp_point_backs[node][resources] = best_parent_hardware
                dp_best_objective[node][resources] = \
                    execution_cost + min_pred_cost_plus_egress

        # Compute the total objective value of the DAG.
        sink_node = topo_order[-1]
        total_objective = dp_best_objective[sink_node]
        assert len(total_objective) == 1, \
            f'Should be DummyCloud: {total_objective}'
        best_resources, best_total_objective = list(total_objective.items())[0]

        # Find the best plan for the DAG.
        # node -> best resources
        best_plan = {}
        for node in reversed(topo_order):
            best_plan[node] = best_resources
            node.best_resources = best_resources
            if node.name != _DUMMY_SOURCE_NAME:
                best_resources = dp_point_backs[node][best_resources]
        return best_plan, best_total_objective

    @staticmethod
    def _optimize_by_ilp(
        graph: 'nx.DiGraph',
        topo_order: List[task_lib.Task],
        node_to_cost_map: _TaskToCostMap,
        minimize_cost: bool = True,
    ) -> Tuple[Dict[task_lib.Task, resources_lib.Resources], float]:
        """Optimizes a general DAG using an ILP solver.

        Notations:
            V: the set of nodes (tasks).
            E: the set of edges (dependencies).
            k: node -> [r.cost for r in node.resources].
            F: (node i, node j) -> the egress cost/time between node i and j.
            c: node -> one-hot decision vector. c[node][i] = 1 means
                the node is assigned to the i-th resource.
            e: (node i, node j) -> linearization of c[node i] x c[node j].
              e[node i][node j][a][b] = 1 means node i and node j are assigned
              to the a-th and the b-th resources, respectively.

        Objective:
            For cost optimization,
                minimize_{c} sum(c[v]^T @ k[v] for each v in V) +
                             sum(c[u]^T @ F[u][v] @ c[v] for each u, v in E)
                s.t. sum(c[v] == 1) for each v in V
            which is equivalent (linearized) to,
                minimize_{c, e} sum(c[v]^T @ k[v] for each v in V) +
                                sum(e[u][v]^T @ F[u][v] for each u, v in E)
                s.t. sum(c[v] == 1) for each v in V (i.e., c is one-hot)
                     sum(e[u][v] == 1) for each u, v in E (i.e., e is one-hot)
                     e[u][v] = flatten(c[u] @ c[v]^T) for each u, v in E
            The first term of the objective indicates the execution cost
            of the task v, and the second term indicates the egress cost
            of the parent task u to the task v.

            For time optimization,
                minimize_{c} finish_time[sink_node]
                s.t. finish_time[v] >= c[v]^T @ k[v] + finish_time[u] +
                                       c[u]^T @ F[u][v] @ c[v]
                     for each u, v in E
                     sum(c[v] == 1) for each v in V
            which is equivalent (linearized) to,
                minimize_{c, e} finish_time[sink_node]
                s.t. finish_time[v] >= c[v]^T @ k[v] + finish_time[u] +
                                       e[u][v]^T @ F[u][v]
                     for each u, v in E
                     sum(c[v] == 1) for each v in V (i.e., c is one-hot)
                     sum(e[u][v] == 1) for each u, v in E (i.e., e is one-hot)
                     e[u][v] = flatten(c[u] @ c[v]^T) for each u, v in E
            The first term of the objective indicates the execution time
            of the task v, and the other two terms indicate that the task v
            starts executing no sooner than its parent tasks are finished and
            the output data from the parents has arrived to the task v.
        """
        import pulp  # pylint: disable=import-outside-toplevel

        if minimize_cost:
            prob = pulp.LpProblem('Cost-Optimization', pulp.LpMinimize)
        else:
            prob = pulp.LpProblem('Runtime-Optimization', pulp.LpMinimize)

        # Prepare the constants.
        V = topo_order  # pylint: disable=invalid-name
        E = graph.edges()  # pylint: disable=invalid-name
        k = {
            node: list(resource_cost_map.values())
            for node, resource_cost_map in node_to_cost_map.items()
        }
        F: Dict[Any, Dict[Any, List[float]]] = collections.defaultdict(dict)  # pylint: disable=invalid-name
        for u, v in E:
            F[u][v] = []
            for r_u in node_to_cost_map[u].keys():
                for r_v in node_to_cost_map[v].keys():
                    F[u][v].append(
                        Optimizer._egress_cost_or_time(minimize_cost, u, r_u, v,
                                                       r_v))

        # Define the decision variables.
        c = {
            v: pulp.LpVariable.matrix(v.name, (range(len(k[v])),), cat='Binary')
            for v in V
        }

        e: Dict[Any,
                Dict[Any,
                     List[pulp.LpVariable]]] = collections.defaultdict(dict)
        for u, v in E:
            num_vars = len(c[u]) * len(c[v])
            e[u][v] = pulp.LpVariable.matrix(f'({u.name}->{v.name})',
                                             (range(num_vars),),
                                             cat='Binary')

        # Formulate the constraints.
        # 1. c[v] is an one-hot vector.
        for v in V:
            prob += pulp.lpSum(c[v]) == 1

        # 2. e[u][v] is an one-hot vector.
        for u, v in E:
            prob += pulp.lpSum(e[u][v]) == 1

        # 3. e[u][v] linearizes c[u] x c[v].
        for u, v in E:
            e_uv = e[u][v]  # 1-d one-hot vector
            N_u = len(c[u])  # pylint: disable=invalid-name
            N_v = len(c[v])  # pylint: disable=invalid-name

            for row in range(N_u):
                prob += pulp.lpSum(
                    e_uv[N_v * row + col] for col in range(N_v)) == c[u][row]

            for col in range(N_v):
                prob += pulp.lpSum(
                    e_uv[N_v * row + col] for row in range(N_u)) == c[v][col]

        # Formulate the objective.
        if minimize_cost:
            objective = 0
            for v in V:
                objective += pulp.lpDot(c[v], k[v])
            for u, v in E:
                objective += pulp.lpDot(e[u][v], F[u][v])
        else:
            # We need additional decision variables.
            finish_time = {
                v: pulp.LpVariable(f'lat({v})', lowBound=0) for v in V
            }
            for u, v in E:
                prob += finish_time[v] >= (pulp.lpDot(
                    c[v], k[v]) + finish_time[u] + pulp.lpDot(e[u][v], F[u][v]))
            sink_node = V[-1]
            objective = finish_time[sink_node]
        prob += objective

        # Solve the optimization problem.
        prob.solve(solver=pulp.PULP_CBC_CMD(msg=False))
        assert prob.status != pulp.LpStatusInfeasible, \
            'Cannot solve the optimization problem'
        best_total_objective = prob.objective.value()

        # Find the best plan for the DAG.
        # node -> best resources
        best_plan = {}
        for node, variables in c.items():
            selected = [variable.value() for variable in variables].index(1)
            best_resources = list(node_to_cost_map[node].keys())[selected]
            node.best_resources = best_resources
            best_plan[node] = best_resources
        return best_plan, best_total_objective

    @staticmethod
    def _compute_total_time(
        graph,
        topo_order: List[task_lib.Task],
        plan: Dict[task_lib.Task, resources_lib.Resources],
    ) -> float:
        """Estimates the total time of running the DAG by the plan."""
        cache_finish_time: Dict[task_lib.Task, float] = {}

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
        topo_order: List[task_lib.Task],
        plan: Dict[task_lib.Task, resources_lib.Resources],
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
            if not nbytes:
                # nbytes can be None, if the task has no inputs/outputs.
                return 0
            assert src_cloud is not None and dst_cloud is not None, (src_cloud,
                                                                     dst_cloud,
                                                                     nbytes)

            if minimize_cost:
                fn = Optimizer._egress_cost
            else:
                fn = Optimizer._egress_time
            cost_or_time = fn(src_cloud, dst_cloud, nbytes)

            if cost_or_time > 0:
                if parent.name == _DUMMY_SOURCE_NAME:
                    egress = [
                        f'{child.get_inputs()} ({src_cloud})',
                        f'{child} ({dst_cloud})'
                    ]
                else:
                    egress = [
                        f'{parent} ({src_cloud})', f'{child} ({dst_cloud})'
                    ]
                message_data.append((*egress, nbytes, cost_or_time))

        if message_data:
            metric = 'COST ($)' if minimize_cost else 'TIME (s)'
            table = _create_table(['SOURCE', 'TARGET', 'SIZE (GB)', metric])
            table.add_rows(reversed(message_data))
            logger.info(f'Egress plan:\n{table}\n')

    @staticmethod
    def print_optimized_plan(
        graph,
        topo_order: List[task_lib.Task],
        best_plan: Dict[task_lib.Task, resources_lib.Resources],
        total_time: float,
        total_cost: float,
        node_to_cost_map: _TaskToCostMap,
        minimize_cost: bool,
    ):
        ordered_node_to_cost_map = collections.OrderedDict()
        ordered_best_plan = collections.OrderedDict()
        for node in topo_order:
            if node.name not in (_DUMMY_SOURCE_NAME, _DUMMY_SINK_NAME):
                ordered_node_to_cost_map[node] = node_to_cost_map[node]
                ordered_best_plan[node] = best_plan[node]

        is_trivial = all(len(v) == 1 for v in node_to_cost_map.values())
        if not is_trivial and not env_options.Options.MINIMIZE_LOGGING.get():
            metric_str = 'cost' if minimize_cost else 'run time'
            logger.info(
                f'{colorama.Style.BRIGHT}Target:{colorama.Style.RESET_ALL}'
                f' minimizing {metric_str}')

        print_hourly_cost = False
        if len(ordered_node_to_cost_map) == 1:
            node = list(ordered_node_to_cost_map.keys())[0]
            if (node.time_estimator_func is None and
                    node.get_inputs() is None and node.get_outputs() is None):
                print_hourly_cost = True

        if not env_options.Options.MINIMIZE_LOGGING.get():
            if print_hourly_cost:
                logger.info(
                    f'{colorama.Style.BRIGHT}Estimated cost: '
                    f'{colorama.Style.RESET_ALL}${total_cost:.1f} / hour\n')
            else:
                logger.info(
                    f'{colorama.Style.BRIGHT}Estimated total runtime: '
                    f'{colorama.Style.RESET_ALL}{total_time / 3600:.1f} '
                    'hours\n'
                    f'{colorama.Style.BRIGHT}Estimated total cost: '
                    f'{colorama.Style.RESET_ALL}${total_cost:.1f}\n')

        def _get_resources_element_list(
                resources: 'resources_lib.Resources') -> List[str]:
            accelerators = resources.get_accelerators_str()
            spot = resources.get_spot_str()
            cloud = resources.cloud
            vcpus, mem = cloud.get_vcpus_mem_from_instance_type(
                resources.instance_type)

            def format_number(x):
                if x is None:
                    return '-'
                elif x.is_integer():
                    return str(int(x))
                else:
                    return f'{x:.1f}'

            vcpus = format_number(vcpus)
            mem = format_number(mem)

            if resources.zone is None:
                region_or_zone = resources.region
            else:
                region_or_zone = resources.zone
            return [
                str(cloud),
                resources.instance_type + spot,
                vcpus,
                mem,
                str(accelerators),
                str(region_or_zone),
            ]

        Row = collections.namedtuple('Row', [
            'cloud', 'instance', 'vcpus', 'mem', 'accelerators',
            'region_or_zone', 'cost_str', 'chosen_str'
        ])

        def _get_resources_named_tuple(resources: 'resources_lib.Resources',
                                       cost_str: str, chosen: bool) -> Row:

            accelerators = resources.get_accelerators_str()
            spot = resources.get_spot_str()
            cloud = resources.cloud
            vcpus, mem = cloud.get_vcpus_mem_from_instance_type(
                resources.instance_type)

            def format_number(x):
                if x is None:
                    return '-'
                elif x.is_integer():
                    return str(int(x))
                else:
                    return f'{x:.1f}'

            vcpus = format_number(vcpus)
            mem = format_number(mem)

            if resources.zone is None:
                region_or_zone = resources.region
            else:
                region_or_zone = resources.zone

            chosen_str = ''
            if chosen:
                chosen_str = (colorama.Fore.GREEN + '   ' + '\u2714' +
                              colorama.Style.RESET_ALL)
            row = Row(cloud, resources.instance_type + spot, vcpus, mem,
                      str(accelerators), str(region_or_zone), cost_str,
                      chosen_str)

            return row

        def _get_resource_group_hash(resources: 'resources_lib.Resources'):
            resource_key_dict = {
                'cloud': f'{resources.cloud}',
                'accelerators': f'{resources.accelerators}',
                'use_spot': resources.use_spot
            }
            if isinstance(resources.cloud, clouds.Kubernetes):
                # Region for Kubernetes is the context name, i.e. different
                # Kubernetes clusters. We add region to the key to show all the
                # Kubernetes clusters in the optimizer table for better UX.
                resource_key_dict['region'] = resources.region
            return json.dumps(resource_key_dict, sort_keys=True)

        # Print the list of resouces that the optimizer considered.
        resource_fields = [
            'CLOUD', 'INSTANCE', 'vCPUs', 'Mem(GB)', 'ACCELERATORS',
            'REGION/ZONE'
        ]
        if len(ordered_best_plan) > 1:
            best_plan_rows = []
            for t, r in ordered_best_plan.items():
                assert t.name is not None, t
                best_plan_rows.append([t.name, str(t.num_nodes)] +
                                      _get_resources_element_list(r))
            logger.info(
                f'{colorama.Style.BRIGHT}Best plan: {colorama.Style.RESET_ALL}')
            best_plan_table = _create_table(['TASK', '#NODES'] +
                                            resource_fields)
            best_plan_table.add_rows(best_plan_rows)
            logger.info(f'{best_plan_table}')

        # Print the egress plan if any data egress is scheduled.
        Optimizer._print_egress_plan(graph, best_plan, minimize_cost)

        metric = 'COST ($)' if minimize_cost else 'TIME (hr)'
        field_names = resource_fields + [metric, 'CHOSEN']

        num_tasks = len(ordered_node_to_cost_map)
        for task, v in ordered_node_to_cost_map.items():
            # Hack: convert the dictionary values
            # (resources) to their yaml config
            # For dictionary comparison later.
            v_yaml = {
                json.dumps(resource.to_yaml_config()): cost
                for resource, cost in v.items()
            }
            task_str = (f'for task {task.name!r} ' if num_tasks > 1 else '')
            plural = 's' if task.num_nodes > 1 else ''
            if num_tasks > 1:
                # Add a new line for better readability, when there are multiple
                # tasks.
                logger.info('')
            logger.info(
                f'{colorama.Style.BRIGHT}Considered resources {task_str}'
                f'({task.num_nodes} node{plural}):'
                f'{colorama.Style.RESET_ALL}')

            # Only print 1 row per cloud.
            # The following code is to generate the table
            # of optimizer table for display purpose.
            best_per_resource_group: Dict[str, Tuple[resources_lib.Resources,
                                                     float]] = {}
            for resources, cost in v.items():
                resource_table_key = _get_resource_group_hash(resources)
                if resource_table_key in best_per_resource_group:
                    if cost < best_per_resource_group[resource_table_key][1]:
                        best_per_resource_group[resource_table_key] = (
                            resources, cost)
                else:
                    best_per_resource_group[resource_table_key] = (resources,
                                                                   cost)

            # If the DAG has multiple tasks, the chosen resources may not be
            # the best resources for the task.
            chosen_resources = best_plan[task]
            resource_table_key = _get_resource_group_hash(chosen_resources)
            best_per_resource_group[resource_table_key] = (
                chosen_resources,
                v_yaml[json.dumps(chosen_resources.to_yaml_config())])
            rows = []
            for resources, cost in best_per_resource_group.values():
                if minimize_cost:
                    cost_str = f'{cost:.2f}'
                else:
                    cost_str = f'{cost / 3600:.2f}'

                row = _get_resources_named_tuple(resources, cost_str,
                                                 resources == best_plan[task])
                rows.append(row)

            # NOTE: we've converted the cost to a string above, so we should
            # convert it back to float for sorting.
            if isinstance(task.resources, list):
                accelerator_spot_list = [
                    r.get_accelerators_str() + r.get_spot_str()
                    for r in list(task.resources)
                ]

                def sort_key(row, accelerator_spot_list=accelerator_spot_list):
                    accelerator_index = accelerator_spot_list.index(
                        row.accelerators +
                        ('[Spot]' if '[Spot]' in row.instance else ''))
                    cost = float(row.cost_str)
                    return (accelerator_index, cost)

                rows = sorted(rows, key=sort_key)
            else:
                rows = sorted(rows, key=lambda row: float(row.cost_str))

            row_list = []
            for row in rows:
                row_in_list = []
                if row.chosen_str != '':
                    for _, cell in enumerate(row):
                        row_in_list.append((f'{colorama.Style.BRIGHT}{cell}'
                                            f'{colorama.Style.RESET_ALL}'))
                else:
                    row_in_list = list(row)
                row_list.append(row_in_list)

            table = _create_table(field_names)
            table.add_rows(rows)
            logger.info(f'{table}')

            # Warning message for using disk_tier=ultra
            # TODO(yi): Consider price of disks in optimizer and
            # move this warning there.
            if chosen_resources.disk_tier == resources_utils.DiskTier.ULTRA:
                logger.warning(
                    'Using disk_tier=ultra will utilize more advanced disks '
                    '(io2 Block Express on AWS and extreme persistent disk on '
                    'GCP), which can lead to significant higher costs (~$2/h).')

    @staticmethod
    def _print_candidates(node_to_candidate_map: _TaskToPerCloudCandidates):
        for node, candidate_set in node_to_candidate_map.items():
            if node.best_resources:
                accelerator = node.best_resources.accelerators
            else:
                accelerator = list(node.resources)[0].accelerators
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
                            f'among:\n{instance_list}.')
            if is_multi_instances:
                logger.info(
                    f'To list more details, run: sky show-gpus {acc_name}\n')

    @staticmethod
    def _optimize_dag(
        dag: 'dag_lib.Dag',
        minimize_cost: bool = True,
        blocked_resources: Optional[Iterable[resources_lib.Resources]] = None,
        quiet: bool = False,
    ) -> Dict[task_lib.Task, resources_lib.Resources]:
        """Finds the optimal task-resource mapping for the entire DAG.

        The optimal mapping should consider the egress cost/time so that
        the total estimated cost/time of the DAG becomes the minimum.
        """

        # TODO: The output of this function is useful. Should generate a
        # text plan and print to both console and a log file.

        def ordinal_number(n):
            if 10 <= n % 100 <= 20:
                suffix = 'th'
            else:
                suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
            return str(n) + suffix

        has_resources_ordered = _is_dag_resources_ordered(dag)
        if has_resources_ordered:
            # Honor the user's choice.
            # The actual task dag can store dummy tasks.
            for task_id, task in enumerate(dag.tasks):
                if isinstance(task.resources, list):
                    resources_list = task.resources
                    accelerators_str = ', '.join(
                        [f'{r}' for r in resources_list])
                    task_id_str = ordinal_number(task_id + 1)
                    if len(dag.tasks) > 3:
                        # User-provided dag has more than one task.
                        # Comparing with 3,
                        # as there are two dummy tasks added by the optimizer.
                        logger.info(f'{colorama.Fore.YELLOW}{task_id_str} '
                                    'task is using user-specified '
                                    'accelerators list '
                                    f'{colorama.Style.RESET_ALL}'
                                    '(will be tried in the listed order): '
                                    f'{accelerators_str}')
                    else:
                        logger.info(
                            f'{colorama.Fore.YELLOW}Using user-specified '
                            'accelerators list '
                            f'{colorama.Style.RESET_ALL}'
                            '(will be tried in the listed order): '
                            f'{accelerators_str}')

        graph = dag.get_graph()
        local_dag = copy.deepcopy(dag) if has_resources_ordered else dag
        for task_id in range(len(dag.tasks)):
            task = dag.tasks[task_id]
            if isinstance(task.resources, list):
                # For ordered resources, we try the resources in the order
                # specified by the user.
                local_task = local_dag.tasks[task_id]
                for resources in task.resources:
                    # Check if there exists launchable resources
                    local_task.set_resources(resources)
                    launchable_resources_map, _, _ = (
                        _fill_in_launchable_resources(
                            task=local_task,
                            blocked_resources=blocked_resources,
                            quiet=False))
                    if launchable_resources_map.get(resources, []):
                        break

        local_graph = local_dag.get_graph()
        local_topo_order = list(nx.topological_sort(local_graph))
        local_node_to_cost_map, local_node_to_candidate_map = (
            Optimizer._estimate_nodes_cost_or_time(local_topo_order,
                                                   minimize_cost,
                                                   blocked_resources))
        if local_dag.is_chain():
            local_best_plan, best_total_objective = Optimizer._optimize_by_dp(
                local_topo_order, local_node_to_cost_map, minimize_cost)
        else:
            local_best_plan, best_total_objective = Optimizer._optimize_by_ilp(
                local_graph, local_topo_order, local_node_to_cost_map,
                minimize_cost)

        if minimize_cost:
            total_time = Optimizer._compute_total_time(local_graph,
                                                       local_topo_order,
                                                       local_best_plan)
            total_cost = best_total_objective
        else:
            total_time = best_total_objective
            total_cost = Optimizer._compute_total_cost(local_graph,
                                                       local_topo_order,
                                                       local_best_plan)

        if local_best_plan is None:
            error_msg = (f'No launchable resource found for task {task}. '
                         'To fix: relax its resource requirements.\n'
                         'Hint: \'sky show-gpus --all\' '
                         'to list available accelerators.\n'
                         '      \'sky check\' to check the enabled clouds.')
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(error_msg)

        if has_resources_ordered:
            best_plan = {}
            # We have to manually set the best_resources for the tasks in the
            # original dag, to pass the optimization results to the caller, as
            # we deep copied the dag when the dag has nodes with ordered
            # resources.
            for task, resources in local_best_plan.items():
                task_idx = local_dag.tasks.index(task)
                dag.tasks[task_idx].best_resources = resources
                best_plan[dag.tasks[task_idx]] = resources

            topo_order = list(nx.topological_sort(graph))
            # Get the cost of each specified resources for display purpose.
            node_to_cost_map, _ = Optimizer._estimate_nodes_cost_or_time(
                topo_order=topo_order,
                minimize_cost=minimize_cost,
                blocked_resources=blocked_resources,
                quiet=True)
        else:
            best_plan = local_best_plan
            topo_order = local_topo_order
            node_to_cost_map = local_node_to_cost_map

        if not quiet:
            Optimizer.print_optimized_plan(graph, topo_order, best_plan,
                                           total_time, total_cost,
                                           node_to_cost_map, minimize_cost)
            Optimizer._print_candidates(local_node_to_candidate_map)
        return best_plan


class DummyResources(resources_lib.Resources):
    """A dummy Resources that has zero egress cost from/to."""

    _REPR = 'DummyResources'

    def __repr__(self) -> str:
        return DummyResources._REPR

    def get_cost(self, seconds):
        return 0


class DummyCloud(clouds.Cloud):
    """A dummy Cloud that has zero egress cost from/to."""
    pass


def _make_launchables_for_valid_region_zones(
    launchable_resources: resources_lib.Resources
) -> List[resources_lib.Resources]:
    assert launchable_resources.is_launchable()
    # In principle, all provisioning requests should be made at the granularity
    # of a single zone. However, for on-demand instances, we batch the requests
    # to the zones in the same region in order to leverage the region-level
    # provisioning APIs of AWS and Azure. This way, we can reduce the number of
    # API calls, and thus the overall failover time. Note that this optimization
    # does not affect the user cost since the clouds charge the same prices for
    # on-demand instances in the same region regardless of the zones. On the
    # other hand, for spot instances, we do not batch the requests because the
    # "AWS" spot prices may vary across zones.
    # For GCP, we do not batch the requests because GCP reservation system is
    # zone based. Therefore, price estimation is potentially different across
    # zones.

    # NOTE(woosuk): GCP does not support region-level provisioning APIs. Thus,
    # while we return per-region resources here, the provisioner will still
    # issue the request for one zone at a time.
    # NOTE(woosuk): If we support Azure spot instances, we should batch the
    # requests since Azure spot prices are region-level.
    # TODO(woosuk): Batch the per-zone AWS spot instance requests if they are
    # in the same region and have the same price.
    # TODO(woosuk): A better design is to implement batching at a higher level
    # (e.g., in provisioner or optimizer), not here.
    launchables = []
    regions = launchable_resources.get_valid_regions_for_launchable()
    for region in regions:
        if (launchable_resources.use_spot and region.zones is not None or
                launchable_resources.cloud.optimize_by_zone()):
            # Spot instances.
            # Do not batch the per-zone requests.
            for zone in region.zones:
                launchables.append(
                    launchable_resources.copy(region=region.name,
                                              zone=zone.name))
        else:
            # On-demand instances.
            # Batch the requests at the granularity of a single region.
            launchables.append(launchable_resources.copy(region=region.name))
    return launchables


def _filter_out_blocked_launchable_resources(
        launchable_resources: Iterable[resources_lib.Resources],
        blocked_resources: Iterable[resources_lib.Resources]):
    """Whether the resources are blocked."""
    available_resources = []
    for resources in launchable_resources:
        for blocked in blocked_resources:
            if resources.should_be_blocked_by(blocked):
                break
        else:  # non-blocked launchable resources. (no break)
            available_resources.append(resources)
    return available_resources


def _check_specified_clouds(dag: 'dag_lib.Dag') -> None:
    """Check if specified clouds are enabled in cache and refresh if needed.

    Our enabled cloud list is cached in a local database, and if a user
    specified a cloud that is not enabled, we should refresh the cache for that
    cloud in case the cloud access has been enabled since the last cache update.

    Args:
        dag: The DAG specified by a user.
    """
    enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
        raise_if_no_cloud_access=True)

    global_disabled_clouds: Set[str] = set()
    for task in dag.tasks:
        # Recheck the enabled clouds if the task's requested resources are on a
        # cloud that is not enabled in the cached enabled_clouds.
        all_clouds_specified: Set[str] = set()
        clouds_need_recheck: Set[str] = set()
        for resources in task.resources:
            cloud_str = str(resources.cloud)
            if (resources.cloud is not None and not clouds.cloud_in_iterable(
                    resources.cloud, enabled_clouds)):
                # Explicitly check again to update the enabled cloud list.
                clouds_need_recheck.add(cloud_str)
            all_clouds_specified.add(cloud_str)

        # Explicitly check again to update the enabled cloud list.
        sky_check.check(quiet=True,
                        clouds=list(clouds_need_recheck -
                                    global_disabled_clouds))
        enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
            raise_if_no_cloud_access=True)
        disabled_clouds = (clouds_need_recheck -
                           {str(c) for c in enabled_clouds})
        global_disabled_clouds.update(disabled_clouds)
        if disabled_clouds:
            is_or_are = 'is' if len(disabled_clouds) == 1 else 'are'
            task_name = f' {task.name!r}' if task.name is not None else ''
            msg = (f'Task{task_name} requires {", ".join(disabled_clouds)} '
                   f'which {is_or_are} not enabled. To enable access, change '
                   f'the task cloud requirement or run: {colorama.Style.BRIGHT}'
                   f'sky check {" ".join(c.lower() for c in disabled_clouds)}'
                   f'{colorama.Style.RESET_ALL}')
            if all_clouds_specified == disabled_clouds:
                # If all resources are specified with a disabled cloud, we
                # should raise an error as no resource can satisfy the
                # requirement. Otherwise, we should just skip the resource.
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesUnavailableError(msg)
            logger.warning(
                f'{colorama.Fore.YELLOW}{msg}{colorama.Style.RESET_ALL}')


def _fill_in_launchable_resources(
    task: task_lib.Task,
    blocked_resources: Optional[Iterable[resources_lib.Resources]],
    quiet: bool = False
) -> Tuple[Dict[resources_lib.Resources, List[resources_lib.Resources]],
           _PerCloudCandidates, List[str]]:
    """Fills in the launchable resources for the task.

    Returns:
      A tuple of:
        Dict mapping the task's requested Resources to a list of launchable
          Resources,
        Dict mapping Cloud to a list of feasible Resources (for printing),
        Sorted list of fuzzy candidates (alternative GPU names).
    Raises:
      ResourcesUnavailableError: if all resources required by the task are on
        a cloud that is not enabled.
    """
    enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
        raise_if_no_cloud_access=True)

    launchable: Dict[resources_lib.Resources, List[resources_lib.Resources]] = (
        collections.defaultdict(list))
    all_fuzzy_candidates = set()
    cloud_candidates: _PerCloudCandidates = collections.defaultdict(
        List[resources_lib.Resources])
    if blocked_resources is None:
        blocked_resources = []
    for resources in task.resources:
        if (resources.cloud is not None and
                not clouds.cloud_in_iterable(resources.cloud, enabled_clouds)):
            # Skip the resources that are on a cloud that is not enabled. The
            # hint has been printed in _check_specified_clouds.
            launchable[resources] = []
            continue
        clouds_list = ([resources.cloud]
                       if resources.cloud is not None else enabled_clouds)
        # If clouds provide hints, store them for later printing.
        hints: Dict[clouds.Cloud, str] = {}
        for cloud in clouds_list:
            feasible_resources = cloud.get_feasible_launchable_resources(
                resources, num_nodes=task.num_nodes)
            if feasible_resources.hint is not None:
                hints[cloud] = feasible_resources.hint
            if feasible_resources.resources_list:
                # Assume feasible_resources is sorted by prices. Guaranteed by
                # the implementation of get_feasible_launchable_resources and
                # the underlying service_catalog filtering
                cheapest = feasible_resources.resources_list[0]
                # Generate region/zone-specified resources.
                launchable[resources].extend(
                    _make_launchables_for_valid_region_zones(cheapest))
                cloud_candidates[cloud] = feasible_resources.resources_list
            else:
                all_fuzzy_candidates.update(
                    feasible_resources.fuzzy_candidate_list)
        if not launchable[resources]:
            clouds_str = str(clouds_list) if len(clouds_list) > 1 else str(
                clouds_list[0])
            num_node_str = ''
            if task.num_nodes > 1:
                num_node_str = f'{task.num_nodes}x '
            if not quiet:
                logger.info(
                    f'No resource satisfying {num_node_str}'
                    f'{resources.repr_with_region_zone} on {clouds_str}.')
                if all_fuzzy_candidates:
                    logger.info('Did you mean: '
                                f'{colorama.Fore.CYAN}'
                                f'{sorted(all_fuzzy_candidates)}'
                                f'{colorama.Style.RESET_ALL}')
                for cloud, hint in hints.items():
                    logger.info(f'{repr(cloud)}: {hint}')
            else:
                if resources.cpus is not None:
                    logger.info('Try specifying a different CPU count, '
                                'or add "+" to the end of the CPU count '
                                'to allow for larger instances.')
                if resources.memory is not None:
                    logger.info('Try specifying a different memory size, '
                                'or add "+" to the end of the memory size '
                                'to allow for larger instances.')

        launchable[resources] = _filter_out_blocked_launchable_resources(
            launchable[resources], blocked_resources)
    return launchable, cloud_candidates, list(sorted(all_fuzzy_candidates))
