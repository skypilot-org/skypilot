"""Unit tests for sky.optimizer module.

This module tests the optimizer logic including:
- Egress cost and time calculations
- DAG manipulation (dummy source/sink nodes)
- DummyResources class
- Resource filtering
- DP optimization algorithm
"""

from typing import Dict, List, Optional
from unittest import mock

import pytest

from sky import clouds
from sky import dag as dag_lib
from sky import exceptions
from sky import optimizer
from sky import resources as resources_lib
from sky import task as task_lib


class TestEgressCost:
    """Tests for Optimizer._egress_cost method."""

    def test_same_cloud_no_cost(self):
        """Test no egress cost when source and dest are same cloud."""
        aws1 = clouds.AWS()
        aws2 = clouds.AWS()
        cost = optimizer.Optimizer._egress_cost(aws1, aws2, gigabytes=100)
        assert cost == 0.0

    def test_different_clouds_has_cost(self):
        """Test egress cost exists between different clouds."""
        aws = clouds.AWS()
        gcp = clouds.GCP()
        cost = optimizer.Optimizer._egress_cost(aws, gcp, gigabytes=100)
        # AWS egress cost is typically $0.09/GB for first 10TB
        assert cost > 0

    def test_dummy_cloud_no_cost(self):
        """Test no egress cost from/to DummyCloud."""
        aws = clouds.AWS()
        dummy = clouds.DummyCloud()

        cost1 = optimizer.Optimizer._egress_cost(dummy, aws, gigabytes=100)
        cost2 = optimizer.Optimizer._egress_cost(aws, dummy, gigabytes=100)
        cost3 = optimizer.Optimizer._egress_cost(dummy, dummy, gigabytes=100)

        assert cost1 == 0.0
        assert cost2 == 0.0
        assert cost3 == 0.0

    def test_zero_gigabytes(self):
        """Test zero egress cost when transferring 0 GB."""
        aws = clouds.AWS()
        gcp = clouds.GCP()
        cost = optimizer.Optimizer._egress_cost(aws, gcp, gigabytes=0)
        assert cost == 0.0


class TestEgressTime:
    """Tests for Optimizer._egress_time method."""

    def test_same_cloud_no_time(self):
        """Test no egress time when source and dest are same cloud."""
        gcp1 = clouds.GCP()
        gcp2 = clouds.GCP()
        time_s = optimizer.Optimizer._egress_time(gcp1, gcp2, gigabytes=100)
        assert time_s == 0.0

    def test_different_clouds_has_time(self):
        """Test egress time exists between different clouds."""
        aws = clouds.AWS()
        gcp = clouds.GCP()
        time_s = optimizer.Optimizer._egress_time(aws, gcp, gigabytes=100)
        # 100GB at 10Gbps = 100*8/10 = 80 seconds
        assert time_s == 80.0

    def test_egress_time_formula(self):
        """Test egress time calculation formula."""
        aws = clouds.AWS()
        gcp = clouds.GCP()
        # Formula: gigabytes * 8 / 10 (assuming 10Gbps bandwidth)
        for gb in [1, 10, 100, 1000]:
            time_s = optimizer.Optimizer._egress_time(aws, gcp, gigabytes=gb)
            expected = gb * 8 / 10
            assert time_s == expected

    def test_dummy_cloud_no_time(self):
        """Test no egress time from/to DummyCloud."""
        aws = clouds.AWS()
        dummy = clouds.DummyCloud()

        time1 = optimizer.Optimizer._egress_time(dummy, aws, gigabytes=100)
        time2 = optimizer.Optimizer._egress_time(aws, dummy, gigabytes=100)
        time3 = optimizer.Optimizer._egress_time(dummy, dummy, gigabytes=100)

        assert time1 == 0.0
        assert time2 == 0.0
        assert time3 == 0.0


class TestDummyResources:
    """Tests for DummyResources class."""

    def test_repr(self):
        """Test DummyResources has correct repr."""
        dummy = optimizer.DummyResources(cloud=clouds.DummyCloud())
        assert repr(dummy) == 'DummyResources'

    def test_get_cost_is_zero(self):
        """Test DummyResources always returns 0 cost."""
        dummy = optimizer.DummyResources(cloud=clouds.DummyCloud())
        assert dummy.get_cost(seconds=0) == 0
        assert dummy.get_cost(seconds=3600) == 0
        assert dummy.get_cost(seconds=86400) == 0


class TestDummySourceSinkNodes:
    """Tests for adding/removing dummy source and sink nodes."""

    def test_add_dummy_nodes_single_task(self):
        """Test adding dummy nodes to a single-task DAG."""
        task = task_lib.Task('my-task')
        task.set_resources({resources_lib.Resources()})

        dag = dag_lib.Dag()
        dag.add(task)

        # Add dummy nodes
        optimizer.Optimizer._add_dummy_source_sink_nodes(dag)

        # Should now have 3 tasks: source, my-task, sink
        assert len(dag.tasks) == 3
        task_names = [t.name for t in dag.tasks]
        assert 'skypilot-dummy-source' in task_names
        assert 'skypilot-dummy-sink' in task_names
        assert 'my-task' in task_names

    def test_remove_dummy_nodes(self):
        """Test removing dummy nodes from DAG."""
        task = task_lib.Task('my-task')
        task.set_resources({resources_lib.Resources()})

        dag = dag_lib.Dag()
        dag.add(task)

        # Add and then remove dummy nodes
        optimizer.Optimizer._add_dummy_source_sink_nodes(dag)
        optimizer.Optimizer._remove_dummy_source_sink_nodes(dag)

        # Should be back to 1 task
        assert len(dag.tasks) == 1
        assert dag.tasks[0].name == 'my-task'

    def test_remove_dummy_nodes_idempotent(self):
        """Test removing dummy nodes is idempotent."""
        task = task_lib.Task('my-task')
        task.set_resources({resources_lib.Resources()})

        dag = dag_lib.Dag()
        dag.add(task)

        # Remove without adding - should be no-op
        optimizer.Optimizer._remove_dummy_source_sink_nodes(dag)

        assert len(dag.tasks) == 1
        assert dag.tasks[0].name == 'my-task'


class TestFilterOutBlockedLaunchableResources:
    """Tests for _filter_out_blocked_launchable_resources function."""

    def test_no_blocked_resources(self):
        """Test filtering with no blocked resources."""
        r1 = resources_lib.Resources(cloud=clouds.AWS(), region='us-east-1')
        r2 = resources_lib.Resources(cloud=clouds.AWS(), region='us-west-2')
        launchable = [r1, r2]

        result = optimizer._filter_out_blocked_launchable_resources(
            launchable, blocked_resources=[])

        assert len(result) == 2
        assert r1 in result
        assert r2 in result

    def test_filter_blocked_region(self):
        """Test filtering out blocked region."""
        r1 = resources_lib.Resources(cloud=clouds.AWS(), region='us-east-1')
        r2 = resources_lib.Resources(cloud=clouds.AWS(), region='us-west-2')
        blocked = resources_lib.Resources(cloud=clouds.AWS(),
                                          region='us-east-1')

        launchable = [r1, r2]
        result = optimizer._filter_out_blocked_launchable_resources(
            launchable, blocked_resources=[blocked])

        assert len(result) == 1
        assert r2 in result

    def test_filter_all_blocked(self):
        """Test filtering when all resources are blocked."""
        r1 = resources_lib.Resources(cloud=clouds.AWS(), region='us-east-1')
        blocked = resources_lib.Resources(cloud=clouds.AWS(),
                                          region='us-east-1')

        launchable = [r1]
        result = optimizer._filter_out_blocked_launchable_resources(
            launchable, blocked_resources=[blocked])

        assert len(result) == 0


class TestGetEgressInfo:
    """Tests for Optimizer._get_egress_info method."""

    def test_dummy_parent_with_no_inputs(self):
        """Test egress info when parent is dummy and task has no inputs."""
        dummy_task = task_lib.Task('dummy')
        dummy_resources = optimizer.DummyResources(cloud=clouds.DummyCloud())
        dummy_task.set_resources({dummy_resources})

        child_task = task_lib.Task('child')
        child_resources = resources_lib.Resources(cloud=clouds.AWS())
        child_task.set_resources({child_resources})

        src, dst, nbytes = optimizer.Optimizer._get_egress_info(
            dummy_task, dummy_resources, child_task, child_resources)

        assert src is None
        assert dst is None
        assert nbytes == 0

    def test_non_dummy_parent(self):
        """Test egress info with non-dummy parent."""
        parent_task = task_lib.Task('parent')
        parent_resources = resources_lib.Resources(cloud=clouds.AWS())
        parent_task.set_resources({parent_resources})
        parent_task.set_outputs('s3://bucket/output',
                                estimated_size_gigabytes=10)

        child_task = task_lib.Task('child')
        child_resources = resources_lib.Resources(cloud=clouds.GCP())
        child_task.set_resources({child_resources})

        src, dst, nbytes = optimizer.Optimizer._get_egress_info(
            parent_task, parent_resources, child_task, child_resources)

        assert isinstance(src, clouds.AWS)
        assert isinstance(dst, clouds.GCP)
        assert nbytes == 10


class TestOptimizeByDp:
    """Tests for Optimizer._optimize_by_dp method."""

    def test_single_task_optimization(self):
        """Test DP optimization with a single task."""
        import networkx as nx

        # Create a simple task
        task = task_lib.Task('my-task')
        # Mock resources with costs
        r1 = mock.MagicMock(spec=resources_lib.Resources)
        r1.cloud = clouds.AWS()
        r1.get_cost.return_value = 1.0

        task.set_resources({r1})
        task.num_nodes = 1

        # Create DAG with dummy nodes
        dag = dag_lib.Dag()
        dag.add(task)

        optimizer.Optimizer._add_dummy_source_sink_nodes(dag)

        # Get proper topological order from the graph
        graph = dag.get_graph()
        topo_order = list(nx.topological_sort(graph))

        # Create cost map
        source = [t for t in dag.tasks if t.name == 'skypilot-dummy-source'][0]
        sink = [t for t in dag.tasks if t.name == 'skypilot-dummy-sink'][0]

        source_resources = list(source.resources)[0]
        sink_resources = list(sink.resources)[0]

        node_to_cost_map = {
            source: {
                source_resources: 0
            },
            task: {
                r1: 1.0
            },
            sink: {
                sink_resources: 0
            },
        }

        best_plan, best_cost = optimizer.Optimizer._optimize_by_dp(
            topo_order, node_to_cost_map, minimize_cost=True)

        assert best_plan[task] == r1
        assert best_cost == 1.0

    def test_dp_chooses_cheapest(self):
        """Test DP optimization chooses cheapest resource."""
        import networkx as nx

        task = task_lib.Task('my-task')
        r1 = mock.MagicMock(spec=resources_lib.Resources)
        r1.cloud = clouds.AWS()

        r2 = mock.MagicMock(spec=resources_lib.Resources)
        r2.cloud = clouds.GCP()

        task.set_resources({r1, r2})
        task.num_nodes = 1

        dag = dag_lib.Dag()
        dag.add(task)

        optimizer.Optimizer._add_dummy_source_sink_nodes(dag)

        # Get proper topological order from the graph
        graph = dag.get_graph()
        topo_order = list(nx.topological_sort(graph))

        source = [t for t in dag.tasks if t.name == 'skypilot-dummy-source'][0]
        sink = [t for t in dag.tasks if t.name == 'skypilot-dummy-sink'][0]

        source_resources = list(source.resources)[0]
        sink_resources = list(sink.resources)[0]

        # r1 costs 5.0, r2 costs 2.0 - optimizer should choose r2
        node_to_cost_map = {
            source: {
                source_resources: 0
            },
            task: {
                r1: 5.0,
                r2: 2.0
            },
            sink: {
                sink_resources: 0
            },
        }

        best_plan, best_cost = optimizer.Optimizer._optimize_by_dp(
            topo_order, node_to_cost_map, minimize_cost=True)

        assert best_plan[task] == r2
        assert best_cost == 2.0


class TestComputeTotalCost:
    """Tests for Optimizer._compute_total_cost method."""

    def test_total_cost_single_task(self):
        """Test computing total cost for single task."""
        task = task_lib.Task('my-task')
        task.num_nodes = 1

        r = mock.MagicMock(spec=resources_lib.Resources)
        r.cloud = clouds.AWS()
        r.get_cost.return_value = 10.0
        task.set_resources({r})

        dag = dag_lib.Dag()
        dag.add(task)

        optimizer.Optimizer._add_dummy_source_sink_nodes(dag)

        graph = dag.get_graph()
        topo_order = list(dag.tasks)

        source = [t for t in dag.tasks if t.name == 'skypilot-dummy-source'][0]
        sink = [t for t in dag.tasks if t.name == 'skypilot-dummy-sink'][0]

        source_resources = list(source.resources)[0]
        sink_resources = list(sink.resources)[0]

        plan = {
            source: source_resources,
            task: r,
            sink: sink_resources,
        }

        total_cost = optimizer.Optimizer._compute_total_cost(
            graph, topo_order, plan)

        # Cost should be 10.0 (the task cost)
        assert total_cost == 10.0

    def test_total_cost_multi_node(self):
        """Test computing total cost for multi-node task."""
        task = task_lib.Task('my-task')
        task.num_nodes = 4  # 4 nodes

        r = mock.MagicMock(spec=resources_lib.Resources)
        r.cloud = clouds.AWS()
        r.get_cost.return_value = 5.0  # Cost per node
        task.set_resources({r})

        dag = dag_lib.Dag()
        dag.add(task)

        optimizer.Optimizer._add_dummy_source_sink_nodes(dag)

        graph = dag.get_graph()
        topo_order = list(dag.tasks)

        source = [t for t in dag.tasks if t.name == 'skypilot-dummy-source'][0]
        sink = [t for t in dag.tasks if t.name == 'skypilot-dummy-sink'][0]

        source_resources = list(source.resources)[0]
        sink_resources = list(sink.resources)[0]

        plan = {
            source: source_resources,
            task: r,
            sink: sink_resources,
        }

        total_cost = optimizer.Optimizer._compute_total_cost(
            graph, topo_order, plan)

        # Cost should be 5.0 * 4 = 20.0
        assert total_cost == 20.0


class TestComputeTotalTime:
    """Tests for Optimizer._compute_total_time method."""

    def test_total_time_single_task(self):
        """Test computing total time for single task."""
        task = task_lib.Task('my-task')
        task.num_nodes = 1
        # Set time estimator to return 3600 seconds (1 hour)
        task.set_time_estimator(lambda _: 3600)

        r = mock.MagicMock(spec=resources_lib.Resources)
        r.cloud = clouds.AWS()
        task.set_resources({r})

        dag = dag_lib.Dag()
        dag.add(task)

        optimizer.Optimizer._add_dummy_source_sink_nodes(dag)

        graph = dag.get_graph()
        topo_order = list(dag.tasks)

        source = [t for t in dag.tasks if t.name == 'skypilot-dummy-source'][0]
        sink = [t for t in dag.tasks if t.name == 'skypilot-dummy-sink'][0]

        source_resources = list(source.resources)[0]
        sink_resources = list(sink.resources)[0]

        plan = {
            source: source_resources,
            task: r,
            sink: sink_resources,
        }

        total_time = optimizer.Optimizer._compute_total_time(
            graph, topo_order, plan)

        # Time should be 3600 seconds
        assert total_time == 3600


class TestCreateTable:
    """Tests for _create_table helper function."""

    def test_create_table_basic(self):
        """Test creating a basic table."""
        table = optimizer._create_table(['Col1', 'Col2', 'Col3'])
        assert table is not None
        assert len(table.field_names) == 3


class TestIsDagResourcesOrdered:
    """Tests for _is_dag_resources_ordered function."""

    def test_dag_with_set_resources(self):
        """Test DAG with resources as a set is not ordered."""
        task = task_lib.Task('my-task')
        task.set_resources({resources_lib.Resources()})

        dag = dag_lib.Dag()
        dag.add(task)

        result = optimizer._is_dag_resources_ordered(dag)
        assert result is False

    def test_dag_with_list_resources(self):
        """Test DAG with resources as a list is ordered."""
        task = task_lib.Task('my-task')
        # Use set_resources with a list to maintain order
        r1 = resources_lib.Resources(cloud=clouds.AWS())
        r2 = resources_lib.Resources(cloud=clouds.GCP())
        task.set_resources([r1, r2])

        dag = dag_lib.Dag()
        dag.add(task)

        result = optimizer._is_dag_resources_ordered(dag)
        assert result is True
