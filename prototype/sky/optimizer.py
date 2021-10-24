import collections
import copy
import enum
import pprint
import typing

import networkx as nx
import numpy as np

import sky
from sky import clouds


class Optimizer(object):

    # Constants: minimize what target?
    COST = 0
    TIME = 1

    @staticmethod
    def _egress_cost(src_cloud, dst_cloud, gigabytes):
        if isinstance(src_cloud, DummyCloud) or isinstance(
                dst_cloud, DummyCloud):
            return 0.0

        if not src_cloud.is_same_cloud(dst_cloud):
            egress_cost = src_cloud.get_egress_cost(num_gigabytes=gigabytes)
        else:
            egress_cost = 0.0
        if egress_cost > 0:
            print('  {} -> {} egress cost: ${} for {:.1f} GB'.format(
                src_cloud, dst_cloud, egress_cost, gigabytes))
        return egress_cost

    @staticmethod
    def _egress_time(src_cloud, dst_cloud, gigabytes):
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
            print('  {} -> {} egress time: {} s for {:.1f} GB'.format(
                src_cloud, dst_cloud, egress_time, gigabytes))
        else:
            egress_time = 0.0
        return egress_time

    @staticmethod
    def optimize(dag: sky.Dag, minimize):
        dag = copy.deepcopy(dag)
        # Optimization.
        dag = Optimizer._add_dummy_source_sink_nodes(dag)
        optimized_dag, best_plan = Optimizer._optimize_cost(
            dag, minimize_cost=minimize == Optimizer.COST)
        optimized_dag = Optimizer._remove_dummy_source_sink_nodes(optimized_dag)
        return optimized_dag

    @staticmethod
    def _add_dummy_source_sink_nodes(dag: sky.Dag):
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
            dummy = sky.Task(name)
            dummy.set_resources({DummyResources(DummyCloud(), None)})
            dummy.set_time_estimator(lambda _: 0)
            return dummy

        with dag:
            source = make_dummy('__source__')
            for real_source_node in zero_indegree_nodes:
                source >> real_source_node
            sink = make_dummy('__sink__')
            for real_sink_node in zero_outdegree_nodes:
                real_sink_node >> sink
        return dag

    @staticmethod
    def _remove_dummy_source_sink_nodes(dag: sky.Dag):
        """Removes special Source and Sink nodes."""
        source = [t for t in dag.tasks if t.name == '__source__']
        sink = [t for t in dag.tasks if t.name == '__sink__']
        assert len(source) == len(sink) == 1, dag.tasks
        dag.remove(source[0])
        dag.remove(sink[0])
        return dag

    @staticmethod
    def _optimize_cost(dag: sky.Dag, minimize_cost=True):
        # TODO: The output of this function is useful. Should generate a
        # text plan and print to both console and a log file.
        graph = dag.get_graph()
        topo_order = list(nx.topological_sort(graph))

        # FIXME: write to (node, cloud) as egress cost depends only on clouds.
        # node -> {resources -> best estimated cost}
        dp_best_cost = collections.defaultdict(dict)
        # node -> {resources -> (parent, best parent resources)}
        dp_point_backs = collections.defaultdict(dict)

        for node_i, node in enumerate(topo_order):
            # Base case: a special source node.
            if node_i == 0:
                dp_best_cost[node][list(node.get_resources())[0]] = 0
                continue
            # Don't print for the last node, Sink.
            do_print = node_i != len(topo_order) - 1

            if do_print:
                print('\n#### {} ####'.format(node))
            parents = list(graph.predecessors(node))

            assert len(parents) == 1, 'Supports single parent for now'
            parent = parents[0]

            if node_i < len(topo_order) - 1:
                # Convert partial resource labels to launchable resources.
                launchable_resources = sky.registry.fill_in_launchable_resources(
                    node)
            else:
                # Dummy sink node.
                launchable_resources = node.get_resources()
                launchable_resources = {
                    list(node.get_resources())[0]: launchable_resources
                }
            num_resources = len(node.get_resources())

            for orig_resources, launchable_list in launchable_resources.items():
                if num_resources == 1 and node.time_estimator_func is None:
                    print('Time estimator not set and only one possible '
                          'resource choice; defaulting estimated time to 1 hr.')
                    estimated_runtime = 1 * 3600
                else:
                    # We assume the time estimator takes in a partial resource
                    #    Resources('V100')
                    # and treat their launchable versions
                    #    Resources(AWS, 'p3.2xlarge'),
                    #    Resources(GCP, '...', 'V100'),
                    #    ...
                    # as having the same run time.
                    estimated_runtime = node.estimate_runtime(orig_resources)
                for resources in launchable_list:
                    # Computes dp_best_cost[node][resources]
                    #   = my estimated cost + min { pred_cost + egress_cost }
                    assert resources not in dp_best_cost[node]
                    if do_print:
                        print('resources:', resources)

                    if minimize_cost:
                        estimated_cost = resources.get_cost(estimated_runtime)
                    else:
                        # Minimize run time; overload the term 'cost'.
                        estimated_cost = estimated_runtime
                    min_pred_cost_plus_egress = np.inf
                    if do_print:
                        print(
                            '  estimated_runtime: {:.0f} s ({:.1f} hr)'.format(
                                estimated_runtime, estimated_runtime / 3600))
                        if minimize_cost:
                            print(
                                '  estimated_cost (not incl. egress): ${:.1f}'.
                                format(estimated_cost))

                    def _egress(parent, parent_resources, node, resources):
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
                            nbytes = parent.get_estimated_outputs_size_gigabytes(
                            )
                        dst_cloud = resources.cloud

                        if minimize_cost:
                            fn = Optimizer._egress_cost
                        else:
                            fn = Optimizer._egress_time
                        return fn(src_cloud, dst_cloud, nbytes)

                    for parent_resources, parent_cost in dp_best_cost[
                            parent].items():
                        egress_cost = _egress(parent, parent_resources, node,
                                              resources)
                        if parent_cost + egress_cost < min_pred_cost_plus_egress:
                            min_pred_cost_plus_egress = parent_cost + egress_cost
                            best_parent = parent_resources
                            best_egress_cost = egress_cost
                    dp_point_backs[node][resources] = (parent, best_parent,
                                                       best_egress_cost)
                    dp_best_cost[node][
                        resources] = min_pred_cost_plus_egress + estimated_cost

        print('\nOptimizer - dp_best_cost:')
        pprint.pprint(dict(dp_best_cost))

        # Dict, node -> (resources, cost).
        best_plan = Optimizer.read_optimized_plan(dp_best_cost, topo_order,
                                                  dp_point_backs, minimize_cost)
        return dag, best_plan

    @staticmethod
    def read_optimized_plan(dp_best_cost, topo_order, dp_point_backs,
                            minimize_cost):
        # FIXME: this function assumes chain.
        node = topo_order[-1]
        messages = []
        egress_cost = 0.0
        overall_best = None
        best_plan = {}
        while True:
            best_costs = dp_best_cost[node]
            h, c = None, np.inf
            for resources in best_costs:
                if best_costs[resources] < c:
                    h = resources
                    c = best_costs[resources]
            if not isinstance(h, DummyResources):
                messages.append('  {} : {}'.format(node, h))
                best_plan[node] = (h, c)
                node.best_resources = h
            elif overall_best is None:
                overall_best = c
            if node not in dp_point_backs:
                break
            egress_cost = dp_point_backs[node][h][2]
            node = dp_point_backs[node][h][0]
        if minimize_cost:
            print('\nOptimizer - plan minimizing cost (~${:.1f}):'.format(
                overall_best))
        else:
            print('\nOptimizer - plan minimizing run time (~{:.1f} hr):'.format(
                overall_best / 3600))
        for msg in reversed(messages):
            print(msg)
        return best_plan


class DummyResources(sky.Resources):
    """A dummy Resources that has zero egress cost from/to."""

    _REPR = 'DummyCloud'

    def __repr__(self) -> str:
        return DummyResources._REPR

    def get_cost(self, seconds):
        return 0


class DummyCloud(clouds.Cloud):
    """A dummy Cloud that has zero egress cost from/to."""
