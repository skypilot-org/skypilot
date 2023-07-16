"""Optimizer: assigns best resources to user tasks."""
import collections
import enum
import typing
from typing import Any, Dict, Iterable, List, Optional, Tuple

import colorama
import numpy as np
import prettytable

from sky import check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import resources as resources_lib
from sky import sky_logging
from sky import task as task_lib
from sky.backends import backend_utils
from sky.utils import env_options
from sky.utils import ux_utils
from sky.utils import log_utils

if typing.TYPE_CHECKING:
    import networkx as nx
    from sky import dag as dag_lib  # pylint: disable=ungrouped-imports

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


class Optimizer:
    """Optimizer: assigns best resources to user tasks."""

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
        # This function is effectful: mutates every node in 'dag' by setting
        # node.best_resources if it is None.
        Optimizer._add_dummy_source_sink_nodes(dag)
        try:
            unused_best_plan = Optimizer._optimize_objective(
                dag,
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
        if len(source) == len(sink) == 0:
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
    ) -> Tuple[Optional[clouds.Cloud], Optional[clouds.Cloud], float]:
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
        if nbytes == 0:
            return 0
        assert src_cloud is not None and dst_cloud is not None

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
                        blocked_resources
                    )
                node_to_candidate_map[node] = cloud_candidates
            else:
                # Dummy sink node.
                node_resources = node.get_resources()
                launchable_resources = {list(node_resources)[0]: node_resources}

            num_resources = len(node.get_resources())
            for orig_resources, launchable_list in launchable_resources.items():
                if not launchable_list:
                    location_hint = ''
                    if node.get_resources():
                        specified_resources = list(node.get_resources())[0]
                        if specified_resources.zone is not None:
                            location_hint = (
                                f' Zone: {specified_resources.zone}.')
                        elif specified_resources.region:
                            location_hint = (
                                f' Region: {specified_resources.region}.')

                    error_msg = (
                        'No launchable resource found for task '
                        f'{node}.{location_hint}\nThis means the '
                        'catalog does not contain any resources that '
                        'satisfy this request.\n'
                        'To fix: relax or change the resource requirements.\n'
                        'Hint: \'sky show-gpus --all\' '
                        'to list available accelerators.\n'
                        '      \'sky check\' to check the enabled clouds.')
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.ResourcesUnavailableError(error_msg)
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
                        estimated_cost_or_time = cost_per_node * node.num_nodes
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
                dp_best_objective[node][list(node.get_resources())[0]] = 0
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
            if nbytes == 0:
                continue

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
        logger.info('== Optimizer ==')
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

        if print_hourly_cost:
            logger.info(f'{colorama.Style.BRIGHT}Estimated cost: '
                        f'{colorama.Style.RESET_ALL}${total_cost:.1f} / hour\n')
        else:
            logger.info(f'{colorama.Style.BRIGHT}Estimated total runtime: '
                        f'{colorama.Style.RESET_ALL}{total_time / 3600:.1f} '
                        'hours\n'
                        f'{colorama.Style.BRIGHT}Estimated total cost: '
                        f'{colorama.Style.RESET_ALL}${total_cost:.1f}\n')

        def _get_resources_element_list(
                resources: 'resources_lib.Resources') -> List[str]:
            accelerators = resources.accelerators
            if accelerators is None:
                accelerators = '-'
            elif isinstance(accelerators, dict) and len(accelerators) == 1:
                accelerators, count = list(accelerators.items())[0]
                accelerators = f'{accelerators}:{count}'
            spot = '[Spot]' if resources.use_spot else ''
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

        # Print the list of resouces that the optimizer considered.
        resource_fields = [
            'CLOUD', 'INSTANCE', 'vCPUs', 'Mem(GB)', 'ACCELERATORS',
            'REGION/ZONE'
        ]
        # Do not print Source or Sink.
        best_plan_rows = [[t, t.num_nodes] + _get_resources_element_list(r)
                          for t, r in ordered_best_plan.items()]
        if len(best_plan_rows) > 1:
            logger.info(
                f'{colorama.Style.BRIGHT}Best plan: {colorama.Style.RESET_ALL}')
            best_plan_table = _create_table(['TASK', '#NODES'] +
                                            resource_fields)
            best_plan_table.add_rows(best_plan_rows)
            logger.info(f'{best_plan_table}\n')

        # Print the egress plan if any data egress is scheduled.
        Optimizer._print_egress_plan(graph, best_plan, minimize_cost)

        metric = 'COST ($)' if minimize_cost else 'TIME (hr)'
        field_names = resource_fields + [metric, 'CHOSEN']

        num_tasks = len(ordered_node_to_cost_map)
        for task, v in ordered_node_to_cost_map.items():
            task_str = (f'for task_lib.Task {repr(task)!r}'
                        if num_tasks > 1 else '')
            plural = 's' if task.num_nodes > 1 else ''
            logger.info(
                f'{colorama.Style.BRIGHT}Considered resources {task_str}'
                f'({task.num_nodes} node{plural}):'
                f'{colorama.Style.RESET_ALL}')

            # Only print 1 row per cloud.
            best_per_cloud: Dict[str, Tuple[resources_lib.Resources,
                                            float]] = {}
            for resources, cost in v.items():
                cloud = str(resources.cloud)
                if cloud in best_per_cloud:
                    if cost < best_per_cloud[cloud][1]:
                        best_per_cloud[cloud] = (resources, cost)
                else:
                    best_per_cloud[cloud] = (resources, cost)

            # If the DAG has multiple tasks, the chosen resources may not be
            # the best resources for the task.
            chosen_resources = best_plan[task]
            best_per_cloud[str(chosen_resources.cloud)] = (chosen_resources,
                                                           v[chosen_resources])

            rows = []
            for resources, cost in best_per_cloud.values():
                if minimize_cost:
                    cost_str = f'{cost:.2f}'
                else:
                    cost_str = f'{cost / 3600:.2f}'

                row = [*_get_resources_element_list(resources), cost_str, '']
                if resources == best_plan[task]:
                    # Use tick sign for the chosen resources.
                    row[-1] = (colorama.Fore.GREEN + '   ' + u'\u2714' +
                               colorama.Style.RESET_ALL)
                rows.append(row)

            # NOTE: we've converted the cost to a string above, so we should
            # convert it back to float for sorting.
            rows = sorted(rows, key=lambda x: float(x[-2]))
            # Highlight the chosen resources.
            for row in rows:
                if row[-1] != '':
                    for i, cell in enumerate(row):
                        row[i] = (f'{colorama.Style.BRIGHT}{cell}'
                                  f'{colorama.Style.RESET_ALL}')
                    break

            table = _create_table(field_names)
            table.add_rows(rows)
            logger.info(f'{table}\n')

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
    def _optimize_objective(
        dag: 'dag_lib.Dag',
        minimize_cost: bool = True,
        blocked_resources: Optional[Iterable[resources_lib.Resources]] = None,
        quiet: bool = False,
    ) -> Dict[task_lib.Task, resources_lib.Resources]:
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
                blocked_resources)

        if dag.is_chain():
            best_plan, best_total_objective = Optimizer._optimize_by_dp(
                topo_order, node_to_cost_map, minimize_cost)
        else:
            best_plan, best_total_objective = Optimizer._optimize_by_ilp(
                graph, topo_order, node_to_cost_map, minimize_cost)

        if minimize_cost:
            total_time = Optimizer._compute_total_time(graph, topo_order,
                                                       best_plan)
            total_cost = best_total_objective
        else:
            total_time = best_total_objective
            total_cost = Optimizer._compute_total_cost(graph, topo_order,
                                                       best_plan)

        if not quiet:
            Optimizer.print_optimized_plan(graph, topo_order, best_plan,
                                           total_time, total_cost,
                                           node_to_cost_map, minimize_cost)
            if not env_options.Options.MINIMIZE_LOGGING.get():
                Optimizer._print_candidates(node_to_candidate_map)
        return best_plan


class DummyResources(resources_lib.Resources):
    """A dummy Resources that has zero egress cost from/to."""

    def __repr__(self) -> str:
        return DummyResources._REPR

    def get_cost(self, seconds):
        return 0


class DummyCloud(clouds.Cloud):
    """A dummy Cloud that has zero egress cost from/to."""
    pass


def _cloud_in_list(cloud: clouds.Cloud, lst: Iterable[clouds.Cloud]) -> bool:
    return any(cloud.is_same_cloud(c) for c in lst)


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
        if launchable_resources.use_spot and region.zones is not None:
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


def _fill_in_launchable_resources(
    task: task_lib.Task,
    blocked_resources: Optional[Iterable[resources_lib.Resources]],
    try_fix_with_sky_check: bool = True,
) -> Tuple[Dict[resources_lib.Resources, List[resources_lib.Resources]],
           _PerCloudCandidates]:
    backend_utils.check_public_cloud_enabled()
    enabled_clouds = global_user_state.get_enabled_clouds()
    launchable = collections.defaultdict(list)
    cloud_candidates: Dict[clouds.Cloud,
                           resources_lib.Resources] = collections.defaultdict(
                               resources_lib.Resources)
    if blocked_resources is None:
        blocked_resources = []
    for resources in task.get_resources():
        if resources.cloud is not None and not _cloud_in_list(
                resources.cloud, enabled_clouds):
            if try_fix_with_sky_check:
                # Explicitly check again to update the enabled cloud list.
                check.check(quiet=True)
                return _fill_in_launchable_resources(task, blocked_resources,
                                                     False)
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    f'task_lib.Task {task} requires {resources.cloud} which is '
                    'not enabled. To enable access, run '
                    f'{colorama.Style.BRIGHT}'
                    f'sky check {colorama.Style.RESET_ALL}, or change the '
                    'cloud requirement')
        else:
            clouds_list = ([resources.cloud]
                           if resources.cloud is not None else enabled_clouds)
            # Hack: When >=2 cloud candidates, always remove local cloud from
            # possible candidates. This is so the optimizer will consider
            # public clouds, except local. Local will be included as part of
            # optimizer in a future PR.
            # TODO(mluo): Add on-prem to cloud spillover.
            if len(clouds_list) >= 2:
                clouds_list = [
                    c for c in clouds_list if not isinstance(c, clouds.Local)
                ]
            all_fuzzy_candidates = set()
            for cloud in clouds_list:
                (feasible_resources, fuzzy_candidate_list) = (
                    cloud.get_feasible_launchable_resources(resources))
                if len(feasible_resources) > 0:
                    # Assume feasible_resources is sorted by prices.
                    cheapest = feasible_resources[0]
                    # Generate region/zone-specified resources.
                    launchable[resources].extend(
                        _make_launchables_for_valid_region_zones(cheapest))
                    cloud_candidates[cloud] = feasible_resources
                else:
                    all_fuzzy_candidates.update(fuzzy_candidate_list)
            if len(launchable[resources]) == 0:
                clouds_str = str(clouds_list) if len(clouds_list) > 1 else str(
                    clouds_list[0])
                logger.info(f'No resource satisfying {resources} '
                            f'on {clouds_str}.')
                if len(all_fuzzy_candidates) > 0:
                    logger.info('Did you mean: '
                                f'{colorama.Fore.CYAN}'
                                f'{sorted(all_fuzzy_candidates)}'
                                f'{colorama.Style.RESET_ALL}')
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

    return launchable, cloud_candidates
