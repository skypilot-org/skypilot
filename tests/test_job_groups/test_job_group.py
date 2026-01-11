"""Tests for JobGroup functionality."""
import logging
import os
import tempfile
from typing import Dict, List, Optional, Tuple
from unittest import mock

import pytest

from sky import clouds
from sky import dag as dag_lib
from sky import resources as resources_lib
from sky import task as task_lib
from sky.utils import dag_utils


class TestJobGroupYamlParsing:
    """Tests for JobGroup YAML parsing."""

    def test_is_job_group_yaml_true(self):
        """Test detection of JobGroup YAML."""
        yaml_content = """
---
name: test-group
placement: SAME_INFRA
execution: parallel
---
name: job1
run: echo hello
---
name: job2
run: echo world
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                assert dag_utils.is_job_group_yaml(f.name) is True
            finally:
                os.unlink(f.name)

    def test_is_job_group_yaml_false_chain_dag(self):
        """Test that chain DAG is not detected as JobGroup."""
        yaml_content = """
---
name: chain-dag
---
name: task1
run: echo hello
---
name: task2
run: echo world
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                # Chain DAG has name-only header, no placement/execution
                assert dag_utils.is_job_group_yaml(f.name) is False
            finally:
                os.unlink(f.name)

    def test_is_job_group_yaml_false_single_task(self):
        """Test that single task YAML is not detected as JobGroup."""
        yaml_content = """
name: my-task
run: echo hello
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                assert dag_utils.is_job_group_yaml(f.name) is False
            finally:
                os.unlink(f.name)

    def test_load_job_group_from_yaml(self):
        """Test loading a JobGroup from YAML."""
        yaml_content = """
---
name: test-group
placement: SAME_INFRA
execution: parallel
---
name: job1
run: echo hello
---
name: job2
run: echo world
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)

                assert dag.is_job_group() is True
                assert dag.name == 'test-group'
                assert dag.placement == dag_lib.JobGroupPlacement.SAME_INFRA
                assert dag.execution == dag_lib.JobGroupExecution.PARALLEL
                assert len(dag.tasks) == 2
                assert dag.tasks[0].name == 'job1'
                assert dag.tasks[1].name == 'job2'
            finally:
                os.unlink(f.name)

    def test_load_job_group_missing_job_name(self):
        """Test that JobGroup loading fails if job has no name."""
        yaml_content = """
---
name: test-group
placement: SAME_INFRA
---
run: echo hello
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                with pytest.raises(ValueError,
                                   match='must have a "name" field'):
                    dag_utils.load_job_group_from_yaml(f.name)
            finally:
                os.unlink(f.name)

    def test_load_job_group_duplicate_job_names(self):
        """Test that JobGroup loading fails with duplicate job names."""
        yaml_content = """
---
name: test-group
placement: SAME_INFRA
---
name: job1
run: echo hello
---
name: job1
run: echo world
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                with pytest.raises(ValueError, match='Duplicate job name'):
                    dag_utils.load_job_group_from_yaml(f.name)
            finally:
                os.unlink(f.name)

    def test_load_job_group_invalid_placement(self):
        """Test that invalid placement mode raises error."""
        yaml_content = """
---
name: test-group
placement: INVALID_MODE
---
name: job1
run: echo hello
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                with pytest.raises(ValueError, match='Invalid placement mode'):
                    dag_utils.load_job_group_from_yaml(f.name)
            finally:
                os.unlink(f.name)


class TestDagJobGroup:
    """Tests for DAG JobGroup functionality."""

    def test_dag_is_job_group_default(self):
        """Test that new DAG is not a JobGroup by default."""
        dag = dag_lib.Dag()
        assert dag.is_job_group() is False

    def test_dag_set_job_group(self):
        """Test setting DAG as JobGroup."""
        dag = dag_lib.Dag()
        dag.set_job_group(dag_lib.JobGroupPlacement.SAME_INFRA,
                          dag_lib.JobGroupExecution.PARALLEL)
        assert dag.is_job_group() is True
        assert dag.placement == dag_lib.JobGroupPlacement.SAME_INFRA
        assert dag.execution == dag_lib.JobGroupExecution.PARALLEL


class TestJobGroupNetworking:
    """Tests for JobGroup networking utilities."""

    def test_generate_hosts_entries(self):
        """Test generation of /etc/hosts entries."""
        from sky.jobs import job_group_networking

        # This test is more of a unit test for the function logic
        # Full integration test would require actual ResourceHandles
        env_vars = job_group_networking.get_job_group_env_vars('test-group')
        assert 'SKYPILOT_JOBGROUP_NAME' in env_vars
        assert env_vars['SKYPILOT_JOBGROUP_NAME'] == 'test-group'

    def test_get_k8s_namespace_logs_on_exception(self):
        """Test that _get_k8s_namespace_from_handle logs debug message on error.

        This test verifies the fix for silent exception handling - exceptions
        should be logged at debug level instead of silently passed.
        """
        from sky.jobs import job_group_networking

        # Create a mock handle that will cause an exception
        mock_handle = mock.MagicMock()
        mock_handle.launched_resources = mock.MagicMock()
        mock_handle.launched_resources.region = 'test-context'

        # Mock the k8s_utils to raise an exception and patch the logger
        with mock.patch(
                'sky.provision.kubernetes.utils.get_kube_config_context_namespace'
        ) as mock_get_ns, mock.patch.object(job_group_networking,
                                            'logger') as mock_logger:
            mock_get_ns.side_effect = Exception('Test K8s error')

            result = job_group_networking._get_k8s_namespace_from_handle(
                mock_handle)

            # Should fall back to default
            assert result == 'default'

            # Should have logged the exception at debug level
            mock_logger.debug.assert_called_once()
            log_message = mock_logger.debug.call_args[0][0]
            assert 'Failed to get K8s namespace from handle' in log_message
            assert 'Test K8s error' in log_message

    def test_get_k8s_namespace_returns_default_for_none_handle(self):
        """Test that _get_k8s_namespace_from_handle returns 'default' for None."""
        from sky.jobs import job_group_networking

        result = job_group_networking._get_k8s_namespace_from_handle(None)
        assert result == 'default'

    def test_generate_wait_for_networking_script_with_hostnames(self):
        """Test wait script generation with multiple job names."""
        from sky.jobs import job_group_networking

        script = job_group_networking.generate_wait_for_networking_script(
            'my-job-group', ['trainer', 'evaluator'])

        # Verify script contains expected hostnames
        assert 'trainer-0.my-job-group' in script
        assert 'evaluator-0.my-job-group' in script

        # Verify script has required structure
        assert 'HOSTNAMES=' in script
        assert 'MAX_WAIT=300' in script
        assert 'getent hosts' in script
        assert '[SkyPilot]' in script

    def test_generate_wait_for_networking_script_empty(self):
        """Test wait script returns empty for no other jobs."""
        from sky.jobs import job_group_networking

        script = job_group_networking.generate_wait_for_networking_script(
            'my-job-group', [])

        assert script == ''

    def test_generate_k8s_dns_updater_script_content(self):
        """Test DNS updater script structure."""
        from sky.jobs import job_group_networking

        dns_mappings = [('trainer-0.ns.svc.cluster.local',
                         'trainer-0.my-group'),
                        ('eval-0.ns.svc.cluster.local', 'eval-0.my-group')]

        script = job_group_networking.generate_k8s_dns_updater_script(
            dns_mappings)

        # Verify script contains required elements
        assert 'MAPPINGS=' in script
        assert 'trainer-0.ns.svc.cluster.local:trainer-0.my-group' in script
        assert 'eval-0.ns.svc.cluster.local:eval-0.my-group' in script
        assert 'getent hosts' in script
        assert '/etc/hosts' in script
        assert 'SkyPilot JobGroup K8s entries' in script
        assert 'while true' in script  # Background loop

    def test_generate_k8s_dns_updater_script_empty_mappings(self):
        """Test DNS updater returns empty for no mappings."""
        from sky.jobs import job_group_networking

        script = job_group_networking.generate_k8s_dns_updater_script([])
        assert script == ''


class TestOptimizerSelectBestInfra:
    """Tests for Optimizer._select_best_infra logic.

    These tests verify the fix for the logic bug where the optimizer could
    select an infrastructure that cannot run all tasks.
    """

    def _create_mock_cloud(self, name: str) -> clouds.Cloud:
        """Create a mock cloud object."""
        mock_cloud = mock.MagicMock(spec=clouds.Cloud)
        mock_cloud.__str__ = mock.MagicMock(return_value=name)
        mock_cloud.__hash__ = mock.MagicMock(return_value=hash(name))
        mock_cloud.__eq__ = lambda self, other: str(self) == str(other)
        return mock_cloud

    def _create_mock_resources(self,
                               region: Optional[str] = None,
                               cost: float = 1.0) -> resources_lib.Resources:
        """Create a mock resources object."""
        mock_res = mock.MagicMock(spec=resources_lib.Resources)
        mock_res.region = region
        mock_res.get_cost = mock.MagicMock(return_value=cost)
        return mock_res

    def _create_mock_task(self, name: str, num_nodes: int = 1) -> task_lib.Task:
        """Create a mock task object."""
        mock_task = mock.MagicMock(spec=task_lib.Task)
        mock_task.name = name
        mock_task.num_nodes = num_nodes
        mock_task.estimate_runtime = mock.MagicMock(return_value=3600)
        return mock_task

    def test_select_best_infra_single_option(self):
        """Test that single infra option is returned directly."""
        from sky.optimizer import Optimizer

        cloud = self._create_mock_cloud('aws')
        common_infras = [(cloud, 'us-east-1')]

        result = Optimizer._select_best_infra(common_infras, {}, [], True)
        assert result == (cloud, 'us-east-1')

    def test_select_best_infra_skips_invalid_cloud(self):
        """Test that infra is skipped if task cannot run on that cloud.

        This tests the fix for the bug where the optimizer would silently
        skip tasks that couldn't run on a cloud, potentially selecting
        an invalid infrastructure.
        """
        from sky.optimizer import Optimizer

        cloud_aws = self._create_mock_cloud('aws')
        cloud_gcp = self._create_mock_cloud('gcp')

        task1 = self._create_mock_task('task1')
        task2 = self._create_mock_task('task2')

        # task1 can run on both clouds, task2 can only run on GCP
        res_aws = self._create_mock_resources('us-east-1', cost=1.0)
        res_gcp = self._create_mock_resources('us-central1', cost=2.0)

        task_candidates = {
            task1: {
                cloud_aws: [res_aws],
                cloud_gcp: [res_gcp]
            },
            task2: {
                cloud_gcp: [res_gcp]
            }  # task2 NOT available on AWS
        }

        common_infras = [
            (cloud_aws, 'us-east-1'),  # Invalid for task2
            (cloud_gcp, 'us-central1')  # Valid for both
        ]

        result = Optimizer._select_best_infra(common_infras, task_candidates,
                                              [task1, task2], True)

        # Should select GCP since AWS can't run task2
        assert result == (cloud_gcp, 'us-central1')

    def test_select_best_infra_skips_no_matching_region(self):
        """Test that infra is skipped if no resources match the region.

        This tests the fix where best_task_score remains infinity when
        no resources match the region.
        """
        from sky.optimizer import Optimizer

        cloud_aws = self._create_mock_cloud('aws')

        task1 = self._create_mock_task('task1')

        # Resource only available in us-west-2, not us-east-1
        res_west = self._create_mock_resources('us-west-2', cost=1.0)

        task_candidates = {task1: {cloud_aws: [res_west]}}

        common_infras = [
            (cloud_aws, 'us-east-1'),  # No resources for this region
            (cloud_aws, 'us-west-2')  # Resources available
        ]

        result = Optimizer._select_best_infra(common_infras, task_candidates,
                                              [task1], True)

        # Should select us-west-2 since us-east-1 has no matching resources
        assert result == (cloud_aws, 'us-west-2')

    def test_select_best_infra_fallback_to_first_when_all_invalid(self):
        """Test fallback to first infra when none are valid."""
        from sky.optimizer import Optimizer

        cloud = self._create_mock_cloud('aws')
        task1 = self._create_mock_task('task1')

        # No resources for task1 on this cloud
        task_candidates = {task1: {}}

        common_infras = [(cloud, 'us-east-1'), (cloud, 'us-west-2')]

        result = Optimizer._select_best_infra(common_infras, task_candidates,
                                              [task1], True)

        # Should fallback to first option
        assert result == common_infras[0]

    def test_select_best_infra_chooses_cheapest(self):
        """Test that cheapest valid infra is selected when minimize_cost=True."""
        from sky.optimizer import Optimizer

        cloud_aws = self._create_mock_cloud('aws')
        cloud_gcp = self._create_mock_cloud('gcp')

        task1 = self._create_mock_task('task1')

        res_aws = self._create_mock_resources('us-east-1', cost=10.0)
        res_gcp = self._create_mock_resources('us-central1', cost=5.0)

        task_candidates = {task1: {cloud_aws: [res_aws], cloud_gcp: [res_gcp]}}

        common_infras = [(cloud_aws, 'us-east-1'), (cloud_gcp, 'us-central1')]

        result = Optimizer._select_best_infra(common_infras, task_candidates,
                                              [task1], True)

        # Should select GCP as it's cheaper
        assert result == (cloud_gcp, 'us-central1')

    def test_select_best_infra_multiple_tasks_all_must_be_valid(self):
        """Test that all tasks must have valid resources on selected infra."""
        from sky.optimizer import Optimizer

        cloud = self._create_mock_cloud('aws')

        task1 = self._create_mock_task('task1')
        task2 = self._create_mock_task('task2')
        task3 = self._create_mock_task('task3')

        res_east = self._create_mock_resources('us-east-1', cost=1.0)
        res_west = self._create_mock_resources('us-west-2', cost=1.0)

        # task1 and task2 available in us-east-1, task3 only in us-west-2
        # task1 available in both regions
        task_candidates = {
            task1: {
                cloud: [res_east, res_west]
            },
            task2: {
                cloud: [res_east]
            },  # Only us-east-1
            task3: {
                cloud: [res_west]
            }  # Only us-west-2
        }

        common_infras = [(cloud, 'us-east-1'), (cloud, 'us-west-2')]

        result = Optimizer._select_best_infra(common_infras, task_candidates,
                                              [task1, task2, task3], True)

        # Neither region can run all 3 tasks, should fallback to first
        assert result == common_infras[0]


class TestOptimizeSameInfraRegion:
    """Tests for SAME_INFRA optimizer region handling.

    These tests verify that _optimize_same_infra correctly uses launchable
    resources (with regions) instead of cloud candidates (without regions).
    This is critical for multi-cluster environments like Kubernetes where
    each cluster/context is represented as a region.
    """

    def test_find_common_infras_with_regions(self):
        """Test that _find_common_infras correctly identifies region-specific infras."""
        from sky.optimizer import Optimizer

        # Create mock clouds
        k8s = mock.MagicMock(spec=clouds.Cloud)
        k8s.__str__ = mock.MagicMock(return_value='Kubernetes')
        k8s.__hash__ = mock.MagicMock(return_value=hash('Kubernetes'))
        k8s.__eq__ = lambda self, other: str(self) == str(other)

        # Create mock tasks
        task1 = mock.MagicMock(spec=task_lib.Task)
        task2 = mock.MagicMock(spec=task_lib.Task)

        # Create resources WITH specific regions (like launchable_resources)
        res_cluster1_t1 = mock.MagicMock(spec=resources_lib.Resources)
        res_cluster1_t1.region = 'kind-cluster-1'
        res_cluster2_t1 = mock.MagicMock(spec=resources_lib.Resources)
        res_cluster2_t1.region = 'kind-cluster-2'

        res_cluster1_t2 = mock.MagicMock(spec=resources_lib.Resources)
        res_cluster1_t2.region = 'kind-cluster-1'
        res_cluster2_t2 = mock.MagicMock(spec=resources_lib.Resources)
        res_cluster2_t2.region = 'kind-cluster-2'

        # Task candidates with region-specific resources
        task_candidates = {
            task1: {
                k8s: [res_cluster1_t1, res_cluster2_t1]
            },
            task2: {
                k8s: [res_cluster1_t2, res_cluster2_t2]
            }
        }

        common_infras = Optimizer._find_common_infras(task_candidates)

        # Should find both clusters as common infras
        infra_set = {(str(cloud), region) for cloud, region in common_infras}
        assert ('Kubernetes', 'kind-cluster-1') in infra_set
        assert ('Kubernetes', 'kind-cluster-2') in infra_set
        # Should NOT have None region
        assert ('Kubernetes', None) not in infra_set

    def test_find_common_infras_without_regions_returns_none_region(self):
        """Test that cloud_candidates (without regions) result in None region.

        This demonstrates the bug that was fixed: when using cloud_candidates
        directly, regions are None, leading to invalid SAME_INFRA selection.
        """
        from sky.optimizer import Optimizer

        k8s = mock.MagicMock(spec=clouds.Cloud)
        k8s.__str__ = mock.MagicMock(return_value='Kubernetes')
        k8s.__hash__ = mock.MagicMock(return_value=hash('Kubernetes'))
        k8s.__eq__ = lambda self, other: str(self) == str(other)

        task1 = mock.MagicMock(spec=task_lib.Task)

        # Resources WITHOUT regions (like cloud_candidates)
        res_no_region = mock.MagicMock(spec=resources_lib.Resources)
        res_no_region.region = None

        task_candidates = {task1: {k8s: [res_no_region]}}

        common_infras = Optimizer._find_common_infras(task_candidates)

        # With cloud_candidates (no region), we get (Kubernetes, None)
        infra_set = {(str(cloud), region) for cloud, region in common_infras}
        assert ('Kubernetes', None) in infra_set

    def test_find_common_infras_partial_overlap(self):
        """Test finding common infras when tasks have partial overlap."""
        from sky.optimizer import Optimizer

        k8s = mock.MagicMock(spec=clouds.Cloud)
        k8s.__str__ = mock.MagicMock(return_value='Kubernetes')
        k8s.__hash__ = mock.MagicMock(return_value=hash('Kubernetes'))
        k8s.__eq__ = lambda self, other: str(self) == str(other)

        task1 = mock.MagicMock(spec=task_lib.Task)
        task2 = mock.MagicMock(spec=task_lib.Task)

        # Task1 can run on cluster-1 and cluster-2
        res_cluster1_t1 = mock.MagicMock(spec=resources_lib.Resources)
        res_cluster1_t1.region = 'cluster-1'
        res_cluster2_t1 = mock.MagicMock(spec=resources_lib.Resources)
        res_cluster2_t1.region = 'cluster-2'

        # Task2 can only run on cluster-1 (e.g., due to GPU availability)
        res_cluster1_t2 = mock.MagicMock(spec=resources_lib.Resources)
        res_cluster1_t2.region = 'cluster-1'

        task_candidates = {
            task1: {
                k8s: [res_cluster1_t1, res_cluster2_t1]
            },
            task2: {
                k8s: [res_cluster1_t2]
            }
        }

        common_infras = Optimizer._find_common_infras(task_candidates)

        # Only cluster-1 should be common
        infra_set = {(str(cloud), region) for cloud, region in common_infras}
        assert ('Kubernetes', 'cluster-1') in infra_set
        assert ('Kubernetes', 'cluster-2') not in infra_set


class TestControllerAsyncPatterns:
    """Tests to verify async patterns are used correctly in controller.

    These tests verify that blocking calls are properly wrapped with
    context_utils.to_thread() to avoid blocking the event loop.
    """

    def test_download_log_uses_to_thread_in_monitor_job_group_task(self):
        """Verify _download_log_and_stream is called via to_thread.

        This test ensures the async blocking bug fix is in place by
        checking that the code structure properly awaits to_thread.
        """
        import ast
        import inspect

        from sky.jobs import controller

        # Get the source code of JobController
        source = inspect.getsource(controller.JobController)

        # Parse the source to check for the pattern
        # We're looking for: await context_utils.to_thread(..._download_log_and_stream...)
        tree = ast.parse(source)

        # Find all function definitions
        async_methods_with_download = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                # Check if this async method contains _download_log_and_stream
                method_source = ast.unparse(node)
                if '_download_log_and_stream' in method_source:
                    async_methods_with_download.append(node.name)
                    # Verify it's called via to_thread
                    assert 'to_thread' in method_source, (
                        f'Async method {node.name} calls _download_log_and_stream '
                        f'but does not use to_thread - this will block the event loop!'
                    )

        # Ensure we found the relevant methods
        assert len(async_methods_with_download) > 0, (
            'No async methods found that call _download_log_and_stream')


class TestDocstringQuality:
    """Tests for code quality issues like typos."""

    def test_no_typos_in_controller_docstrings(self):
        """Verify common typos are not present in controller module."""
        import inspect

        from sky.jobs import controller

        source = inspect.getsource(controller)

        # Check for known typos that were fixed
        typos = ['donwload', 'recieve', 'occured', 'seperate']
        for typo in typos:
            assert typo not in source.lower(), (
                f'Found typo "{typo}" in controller.py')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
