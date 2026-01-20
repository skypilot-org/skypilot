"""Unit tests for sky.optimizer - JobGroup optimization logic."""
import collections
from typing import Dict, List, Optional, Set, Tuple
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import clouds
from sky import dag as dag_lib
from sky import exceptions
from sky import optimizer
from sky import resources as resources_lib
from sky import task as task_lib
from sky.utils import common


class TestJobGroupOptimizer:
    """Tests for JobGroup optimization in sky.optimizer.

    These tests verify the optimization logic for JobGroups with different
    placement constraints (SAME_INFRA vs independent).
    """

    @pytest.fixture
    def mock_aws_cloud(self):
        """Create a mock AWS cloud."""
        cloud = MagicMock(spec=clouds.AWS)
        cloud.__str__ = MagicMock(return_value='AWS')
        cloud.__repr__ = MagicMock(return_value='AWS')
        cloud.__hash__ = MagicMock(return_value=hash('AWS'))
        cloud.__eq__ = lambda self, other: str(other) == 'AWS'
        return cloud

    @pytest.fixture
    def mock_gcp_cloud(self):
        """Create a mock GCP cloud."""
        cloud = MagicMock(spec=clouds.GCP)
        cloud.__str__ = MagicMock(return_value='GCP')
        cloud.__repr__ = MagicMock(return_value='GCP')
        cloud.__hash__ = MagicMock(return_value=hash('GCP'))
        cloud.__eq__ = lambda self, other: str(other) == 'GCP'
        return cloud

    @pytest.fixture
    def mock_resources_aws_us_east_1(self, mock_aws_cloud):
        """Create mock AWS resources in us-east-1."""
        resources = MagicMock(spec=resources_lib.Resources)
        resources.cloud = mock_aws_cloud
        resources.region = 'us-east-1'
        resources.get_cost = MagicMock(return_value=1.0)
        return resources

    @pytest.fixture
    def mock_resources_aws_us_west_2(self, mock_aws_cloud):
        """Create mock AWS resources in us-west-2."""
        resources = MagicMock(spec=resources_lib.Resources)
        resources.cloud = mock_aws_cloud
        resources.region = 'us-west-2'
        resources.get_cost = MagicMock(return_value=1.5)
        return resources

    @pytest.fixture
    def mock_resources_gcp_us_central1(self, mock_gcp_cloud):
        """Create mock GCP resources in us-central1."""
        resources = MagicMock(spec=resources_lib.Resources)
        resources.cloud = mock_gcp_cloud
        resources.region = 'us-central1'
        resources.get_cost = MagicMock(return_value=0.8)
        return resources

    def test_find_common_infras_single_task(self, mock_aws_cloud,
                                            mock_resources_aws_us_east_1):
        """Test _find_common_infras with a single task."""
        task = MagicMock(spec=task_lib.Task)
        task_candidates = {
            task: {
                mock_aws_cloud: [mock_resources_aws_us_east_1]
            }
        }

        result = optimizer.Optimizer._find_common_infras(task_candidates)

        assert len(result) == 1
        cloud, region = result[0]
        assert str(cloud) == 'AWS'
        assert region == 'us-east-1'

    def test_find_common_infras_two_tasks_same_region(
            self, mock_aws_cloud, mock_resources_aws_us_east_1):
        """Test _find_common_infras with two tasks in same region."""
        task1 = MagicMock(spec=task_lib.Task)
        task2 = MagicMock(spec=task_lib.Task)

        task_candidates = {
            task1: {
                mock_aws_cloud: [mock_resources_aws_us_east_1]
            },
            task2: {
                mock_aws_cloud: [mock_resources_aws_us_east_1]
            }
        }

        result = optimizer.Optimizer._find_common_infras(task_candidates)

        assert len(result) == 1
        cloud, region = result[0]
        assert str(cloud) == 'AWS'
        assert region == 'us-east-1'

    def test_find_common_infras_no_common_region(self, mock_aws_cloud,
                                                 mock_resources_aws_us_east_1,
                                                 mock_resources_aws_us_west_2):
        """Test _find_common_infras with no common region."""
        task1 = MagicMock(spec=task_lib.Task)
        task2 = MagicMock(spec=task_lib.Task)

        task_candidates = {
            task1: {
                mock_aws_cloud: [mock_resources_aws_us_east_1]
            },
            task2: {
                mock_aws_cloud: [mock_resources_aws_us_west_2]
            }
        }

        result = optimizer.Optimizer._find_common_infras(task_candidates)

        # No common region between us-east-1 and us-west-2
        assert len(result) == 0

    def test_find_common_infras_multiple_common_regions(
            self, mock_aws_cloud, mock_resources_aws_us_east_1,
            mock_resources_aws_us_west_2):
        """Test _find_common_infras with multiple common regions."""
        task1 = MagicMock(spec=task_lib.Task)
        task2 = MagicMock(spec=task_lib.Task)

        # Both tasks can run in both regions
        task_candidates = {
            task1: {
                mock_aws_cloud: [
                    mock_resources_aws_us_east_1, mock_resources_aws_us_west_2
                ]
            },
            task2: {
                mock_aws_cloud: [
                    mock_resources_aws_us_east_1, mock_resources_aws_us_west_2
                ]
            }
        }

        result = optimizer.Optimizer._find_common_infras(task_candidates)

        # Both regions should be common
        assert len(result) == 2
        regions = {r for _, r in result}
        assert regions == {'us-east-1', 'us-west-2'}

    def test_find_common_infras_empty_candidates(self):
        """Test _find_common_infras with empty candidates."""
        result = optimizer.Optimizer._find_common_infras({})
        assert result == []

    def test_select_best_infra_single_option(self, mock_aws_cloud,
                                             mock_resources_aws_us_east_1):
        """Test _select_best_infra with single option."""
        task = MagicMock(spec=task_lib.Task)
        task.estimate_runtime = MagicMock(return_value=3600)
        task.num_nodes = 1

        common_infras = [(mock_aws_cloud, 'us-east-1')]
        task_candidates = {
            task: {
                mock_aws_cloud: [mock_resources_aws_us_east_1]
            }
        }

        result = optimizer.Optimizer._select_best_infra(common_infras,
                                                        task_candidates, [task],
                                                        minimize_cost=True)

        cloud, region = result
        assert str(cloud) == 'AWS'
        assert region == 'us-east-1'

    def test_select_best_infra_minimize_cost(self, mock_aws_cloud,
                                             mock_resources_aws_us_east_1,
                                             mock_resources_aws_us_west_2):
        """Test _select_best_infra selects cheapest option."""
        task = MagicMock(spec=task_lib.Task)
        task.estimate_runtime = MagicMock(return_value=3600)
        task.time_estimator_func = MagicMock()
        task.num_nodes = 1

        # us-east-1 costs 1.0, us-west-2 costs 1.5
        common_infras = [(mock_aws_cloud, 'us-east-1'),
                         (mock_aws_cloud, 'us-west-2')]
        task_candidates = {
            task: {
                mock_aws_cloud: [
                    mock_resources_aws_us_east_1, mock_resources_aws_us_west_2
                ]
            }
        }

        result = optimizer.Optimizer._select_best_infra(common_infras,
                                                        task_candidates, [task],
                                                        minimize_cost=True)

        cloud, region = result
        assert str(cloud) == 'AWS'
        # Should select us-east-1 (cheaper)
        assert region == 'us-east-1'

    def test_select_best_infra_multiple_tasks(self, mock_aws_cloud,
                                              mock_resources_aws_us_east_1,
                                              mock_resources_aws_us_west_2):
        """Test _select_best_infra considers all tasks."""
        task1 = MagicMock(spec=task_lib.Task)
        task1.estimate_runtime = MagicMock(return_value=3600)
        task1.time_estimator_func = MagicMock()
        task1.num_nodes = 1

        task2 = MagicMock(spec=task_lib.Task)
        task2.estimate_runtime = MagicMock(return_value=7200)
        task2.time_estimator_func = MagicMock()
        task2.num_nodes = 2

        common_infras = [(mock_aws_cloud, 'us-east-1'),
                         (mock_aws_cloud, 'us-west-2')]
        task_candidates = {
            task1: {
                mock_aws_cloud: [
                    mock_resources_aws_us_east_1, mock_resources_aws_us_west_2
                ]
            },
            task2: {
                mock_aws_cloud: [
                    mock_resources_aws_us_east_1, mock_resources_aws_us_west_2
                ]
            }
        }

        result = optimizer.Optimizer._select_best_infra(common_infras,
                                                        task_candidates,
                                                        [task1, task2],
                                                        minimize_cost=True)

        # Should return a valid infra
        cloud, region = result
        assert str(cloud) == 'AWS'
        assert region in ['us-east-1', 'us-west-2']


class TestOptimizeJobGroup:
    """Tests for the main optimize_job_group function."""

    @pytest.fixture
    def mock_dag_non_job_group(self):
        """Create a mock DAG that is NOT a JobGroup."""
        dag = MagicMock(spec=dag_lib.Dag)
        dag.is_job_group = MagicMock(return_value=False)
        dag.tasks = []
        return dag

    @pytest.fixture
    def mock_dag_job_group_same_infra(self):
        """Create a mock JobGroup DAG with SAME_INFRA placement."""
        dag = MagicMock(spec=dag_lib.Dag)
        dag.is_job_group = MagicMock(return_value=True)
        dag.placement = dag_lib.DagPlacement.SAME_INFRA
        dag.name = 'test-job-group'

        task1 = MagicMock(spec=task_lib.Task)
        task1.name = 'task-1'
        task2 = MagicMock(spec=task_lib.Task)
        task2.name = 'task-2'
        dag.tasks = [task1, task2]

        return dag

    @pytest.fixture
    def mock_dag_job_group_independent(self):
        """Create a mock JobGroup DAG with independent placement."""
        dag = MagicMock(spec=dag_lib.Dag)
        dag.is_job_group = MagicMock(return_value=True)
        dag.placement = None  # Default placement
        dag.name = 'test-job-group'

        task1 = MagicMock(spec=task_lib.Task)
        task1.name = 'task-1'
        task2 = MagicMock(spec=task_lib.Task)
        task2.name = 'task-2'
        dag.tasks = [task1, task2]

        return dag

    def test_optimize_job_group_falls_back_for_non_job_group(
            self, mock_dag_non_job_group):
        """Test that non-JobGroup DAGs fall back to regular optimization."""
        with patch.object(optimizer.Optimizer, 'optimize') as mock_optimize:
            mock_optimize.return_value = mock_dag_non_job_group

            result = optimizer.Optimizer.optimize_job_group(
                mock_dag_non_job_group)

            mock_optimize.assert_called_once()
            assert result == mock_dag_non_job_group

    def test_optimize_job_group_same_infra_calls_optimize_same_infra(
            self, mock_dag_job_group_same_infra):
        """Test SAME_INFRA placement calls _optimize_same_infra."""
        with patch.object(optimizer.Optimizer,
                          '_optimize_same_infra') as mock_same_infra:
            mock_same_infra.return_value = mock_dag_job_group_same_infra

            result = optimizer.Optimizer.optimize_job_group(
                mock_dag_job_group_same_infra, quiet=True)

            mock_same_infra.assert_called_once()

    def test_optimize_job_group_independent_calls_optimize_independent(
            self, mock_dag_job_group_independent):
        """Test default placement calls _optimize_independent."""
        with patch.object(optimizer.Optimizer,
                          '_optimize_independent') as mock_independent:
            mock_independent.return_value = mock_dag_job_group_independent

            result = optimizer.Optimizer.optimize_job_group(
                mock_dag_job_group_independent, quiet=True)

            mock_independent.assert_called_once()


class TestOptimizeIndependent:
    """Tests for _optimize_independent method."""

    def test_optimize_independent_creates_temp_dag_per_task(self):
        """Test that _optimize_independent creates temp DAG for each task."""
        dag = MagicMock(spec=dag_lib.Dag)
        task1 = MagicMock(spec=task_lib.Task)
        task1.name = 'task-1'
        task2 = MagicMock(spec=task_lib.Task)
        task2.name = 'task-2'
        dag.tasks = [task1, task2]

        optimize_call_count = 0

        def mock_optimize(temp_dag, minimize, blocked_resources, quiet):
            nonlocal optimize_call_count
            optimize_call_count += 1
            return temp_dag

        with patch.object(optimizer.Optimizer,
                          'optimize',
                          side_effect=mock_optimize):
            result = optimizer.Optimizer._optimize_independent(
                dag,
                minimize=common.OptimizeTarget.COST,
                blocked_resources=None,
                quiet=True)

            # Should call optimize once per task
            assert optimize_call_count == 2
            assert result == dag


class TestOptimizeSameInfra:
    """Tests for _optimize_same_infra method."""

    @pytest.fixture
    def mock_aws_cloud(self):
        """Create a mock AWS cloud."""
        cloud = MagicMock(spec=clouds.AWS)
        cloud.__str__ = MagicMock(return_value='AWS')
        cloud.__repr__ = MagicMock(return_value='AWS')
        cloud.__hash__ = MagicMock(return_value=hash('AWS'))
        cloud.__eq__ = lambda self, other: str(other) == 'AWS'
        return cloud

    def test_optimize_same_infra_no_resources_raises_error(self):
        """Test that missing resources raises ResourcesUnavailableError."""
        dag = MagicMock(spec=dag_lib.Dag)
        dag.name = 'test-job-group'
        task = MagicMock(spec=task_lib.Task)
        task.name = 'task-1'
        dag.tasks = [task]

        # Mock _fill_in_launchable_resources to return empty resources
        with patch('sky.optimizer._fill_in_launchable_resources') as mock_fill:
            mock_fill.return_value = ({}, None, None, None)

            with pytest.raises(
                    exceptions.ResourcesUnavailableError) as exc_info:
                optimizer.Optimizer._optimize_same_infra(
                    dag,
                    minimize=common.OptimizeTarget.COST,
                    blocked_resources=None,
                    quiet=True)

            assert 'No resources available' in str(exc_info.value)
            assert 'task-1' in str(exc_info.value)

    def test_optimize_same_infra_fallback_when_no_common_infra(
            self, mock_aws_cloud):
        """Test fallback to independent optimization when no common infra."""
        dag = MagicMock(spec=dag_lib.Dag)
        dag.name = 'test-job-group'

        task1 = MagicMock(spec=task_lib.Task)
        task1.name = 'task-1'
        task2 = MagicMock(spec=task_lib.Task)
        task2.name = 'task-2'
        dag.tasks = [task1, task2]

        # Create resources in different regions (no overlap)
        resources1 = MagicMock(spec=resources_lib.Resources)
        resources1.cloud = mock_aws_cloud
        resources1.region = 'us-east-1'

        resources2 = MagicMock(spec=resources_lib.Resources)
        resources2.cloud = mock_aws_cloud
        resources2.region = 'us-west-2'

        call_count = [0]

        def mock_fill(task, blocked_resources, quiet):
            call_count[0] += 1
            if task == task1:
                return ({resources1: [resources1]}, None, None, None)
            else:
                return ({resources2: [resources2]}, None, None, None)

        with patch('sky.optimizer._fill_in_launchable_resources',
                   side_effect=mock_fill):
            with patch.object(optimizer.Optimizer,
                              '_optimize_independent') as mock_independent:
                mock_independent.return_value = dag

                result = optimizer.Optimizer._optimize_same_infra(
                    dag,
                    minimize=common.OptimizeTarget.COST,
                    blocked_resources=None,
                    quiet=True)

                # Should fallback to independent optimization
                mock_independent.assert_called_once()

    def test_optimize_same_infra_sets_best_resources(self, mock_aws_cloud):
        """Test that _optimize_same_infra sets best_resources on tasks."""
        dag = MagicMock(spec=dag_lib.Dag)
        dag.name = 'test-job-group'

        task1 = MagicMock(spec=task_lib.Task)
        task1.name = 'task-1'
        task1.estimate_runtime = MagicMock(return_value=3600)
        task1.num_nodes = 1

        task2 = MagicMock(spec=task_lib.Task)
        task2.name = 'task-2'
        task2.estimate_runtime = MagicMock(return_value=3600)
        task2.num_nodes = 1

        dag.tasks = [task1, task2]

        # Create resources in the same region
        resources = MagicMock(spec=resources_lib.Resources)
        resources.cloud = mock_aws_cloud
        resources.region = 'us-east-1'
        resources.get_cost = MagicMock(return_value=1.0)

        def mock_fill(task, blocked_resources, quiet):
            return ({resources: [resources]}, None, None, None)

        with patch('sky.optimizer._fill_in_launchable_resources',
                   side_effect=mock_fill):
            result = optimizer.Optimizer._optimize_same_infra(
                dag,
                minimize=common.OptimizeTarget.COST,
                blocked_resources=None,
                quiet=True)

            # Both tasks should have best_resources set
            assert task1.best_resources == resources
            assert task2.best_resources == resources


class TestModuleLevelOptimizeJobGroup:
    """Tests for the module-level optimize_job_group function."""

    def test_module_level_function_calls_optimizer_method(self):
        """Test module-level function delegates to Optimizer class."""
        dag = MagicMock(spec=dag_lib.Dag)
        dag.is_job_group = MagicMock(return_value=True)

        with patch.object(optimizer.Optimizer,
                          'optimize_job_group') as mock_method:
            mock_method.return_value = dag

            result = optimizer.optimize_job_group(dag, quiet=True)

            mock_method.assert_called_once_with(dag, common.OptimizeTarget.COST,
                                                None, True)
            assert result == dag
