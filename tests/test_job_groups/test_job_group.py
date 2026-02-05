"""Tests for JobGroup functionality."""
import os
import tempfile
from typing import Optional
from unittest import mock

import pytest

from sky import clouds
from sky import dag as dag_lib
from sky import resources as resources_lib
from sky import task as task_lib
from sky.utils import dag_utils
from sky.utils import resources_utils


class TestJobGroupYamlParsing:
    """Tests for JobGroup YAML parsing."""

    def test_is_job_group_yaml_true(self):
        """Test detection of JobGroup YAML."""
        yaml_content = """
---
name: test-group
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
                # Chain DAG has name-only header (no execution: parallel)
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
                assert dag.execution == dag_lib.DagExecution.PARALLEL
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
execution: parallel
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
execution: parallel
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


class TestPrimaryJobsParsing:
    """Tests for primary_tasks and termination_delay parsing."""

    def test_load_job_group_with_primary_tasks(self):
        """Test loading JobGroup with primary_tasks field."""
        yaml_content = """
---
name: test-rl
execution: parallel
primary_tasks: [trainer]
---
name: trainer
run: python train.py
---
name: replay-buffer
run: python replay_buffer.py
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                assert dag.primary_tasks == ['trainer']
                assert dag.termination_delay is None
            finally:
                os.unlink(f.name)

    def test_load_job_group_with_simple_termination_delay(self):
        """Test loading JobGroup with simple termination_delay string."""
        yaml_content = """
---
name: test-rl
execution: parallel
primary_tasks: [trainer]
termination_delay: 30s
---
name: trainer
run: echo trainer
---
name: replay-buffer
run: echo buffer
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                assert dag.primary_tasks == ['trainer']
                assert dag.termination_delay == '30s'
                assert dag.get_termination_delay_secs('replay-buffer') == 30
            finally:
                os.unlink(f.name)

    def test_load_job_group_with_dict_termination_delay(self):
        """Test loading JobGroup with dict termination_delay."""
        yaml_content = """
---
name: test-rl
execution: parallel
primary_tasks: [trainer]
termination_delay:
  default: 10s
  replay-buffer: 30s
  evaluator: 1m
---
name: trainer
run: echo trainer
---
name: replay-buffer
run: echo buffer
---
name: evaluator
run: echo eval
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                assert dag.primary_tasks == ['trainer']
                assert isinstance(dag.termination_delay, dict)
                assert dag.get_termination_delay_secs('replay-buffer') == 30
                assert dag.get_termination_delay_secs('evaluator') == 60
                # Unknown job gets default
                assert dag.get_termination_delay_secs('unknown') == 10
            finally:
                os.unlink(f.name)

    def test_load_job_group_invalid_primary_job_name(self):
        """Test that referencing unknown job in primary_tasks raises error."""
        yaml_content = """
---
name: test-group
execution: parallel
primary_tasks: [nonexistent]
---
name: job1
run: echo hello
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                with pytest.raises(
                        ValueError,
                        match='primary_tasks references unknown job'):
                    dag_utils.load_job_group_from_yaml(f.name)
            finally:
                os.unlink(f.name)

    def test_load_job_group_invalid_termination_delay_format(self):
        """Test that invalid termination_delay format raises error."""
        yaml_content = """
---
name: test-group
execution: parallel
primary_tasks: [job1]
termination_delay: invalid
---
name: job1
run: echo hello
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                with pytest.raises(ValueError, match='Invalid time duration'):
                    dag_utils.load_job_group_from_yaml(f.name)
            finally:
                os.unlink(f.name)

    def test_load_job_group_termination_delay_dict_unknown_job(self):
        """Test that termination_delay dict with unknown job raises error."""
        yaml_content = """
---
name: test-group
execution: parallel
primary_tasks: [job1]
termination_delay:
  unknown-job: 30s
---
name: job1
run: echo hello
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                with pytest.raises(
                        ValueError,
                        match='termination_delay references unknown job'):
                    dag_utils.load_job_group_from_yaml(f.name)
            finally:
                os.unlink(f.name)

    def test_get_termination_delay_secs_no_delay(self):
        """Test get_termination_delay_secs returns 0 when not configured."""
        dag = dag_lib.Dag()
        assert dag.get_termination_delay_secs('any-job') == 0

    def test_get_termination_delay_secs_simple_string(self):
        """Test get_termination_delay_secs with simple string."""
        dag = dag_lib.Dag()
        dag.termination_delay = '45s'
        assert dag.get_termination_delay_secs('any-job') == 45

    def test_get_termination_delay_secs_dict_with_default(self):
        """Test get_termination_delay_secs with dict and default."""
        dag = dag_lib.Dag()
        dag.termination_delay = {'default': '20s', 'special-job': '2m'}
        assert dag.get_termination_delay_secs('special-job') == 120
        assert dag.get_termination_delay_secs('other-job') == 20

    def test_get_termination_delay_secs_dict_no_default(self):
        """Test get_termination_delay_secs with dict but no default."""
        dag = dag_lib.Dag()
        dag.termination_delay = {'special-job': '30s'}
        assert dag.get_termination_delay_secs('special-job') == 30
        assert dag.get_termination_delay_secs('other-job') == 0  # No default

    def test_dump_job_group_with_primary_tasks(self):
        """Test dumping JobGroup with primary_tasks to YAML."""
        dag = dag_lib.Dag()
        dag.name = 'test-group'
        dag.set_execution(dag_lib.DagExecution.PARALLEL)
        dag.primary_tasks = ['job1']
        dag.termination_delay = '30s'

        # Add tasks
        task1 = task_lib.Task(name='job1')
        task1.set_resources(resources_lib.Resources())
        task2 = task_lib.Task(name='job2')
        task2.set_resources(resources_lib.Resources())
        dag.add(task1)
        dag.add(task2)

        yaml_str = dag_utils.dump_job_group_to_yaml_str(dag)

        # Reload and verify
        reloaded_dag = dag_utils.load_job_group_from_yaml_str(yaml_str)
        assert reloaded_dag.primary_tasks == ['job1']
        assert reloaded_dag.termination_delay == '30s'

    def test_duration_parsing_various_formats(self):
        """Test that various time formats are parsed correctly."""
        from sky.utils import resources_utils

        # Seconds
        assert resources_utils.parse_time_seconds('30') == 30
        assert resources_utils.parse_time_seconds('30s') == 30
        assert resources_utils.parse_time_seconds('30S') == 30

        # Minutes
        assert resources_utils.parse_time_seconds('5m') == 300
        assert resources_utils.parse_time_seconds('5M') == 300

        # Hours
        assert resources_utils.parse_time_seconds('1h') == 3600
        assert resources_utils.parse_time_seconds('2H') == 7200

        # Days
        assert resources_utils.parse_time_seconds('1d') == 86400

    def test_duration_parsing_invalid_format(self):
        """Test that invalid duration format raises ValueError."""
        from sky.utils import resources_utils

        with pytest.raises(ValueError, match='Invalid time format'):
            resources_utils.parse_time_seconds('invalid')

        with pytest.raises(ValueError, match='Invalid time format'):
            resources_utils.parse_time_seconds('30x')


class TestDagJobGroup:
    """Tests for DAG JobGroup functionality."""

    def test_dag_is_job_group_default(self):
        """Test that new DAG is not a JobGroup by default."""
        dag = dag_lib.Dag()
        assert dag.is_job_group() is False

    def test_set_execution(self):
        """Test setting DAG execution mode."""
        dag = dag_lib.Dag()
        dag.set_execution(dag_lib.DagExecution.PARALLEL)
        assert dag.is_job_group() is True
        assert dag.execution == dag_lib.DagExecution.PARALLEL

    def test_primary_tasks_default(self):
        """Test that primary_tasks is None by default."""
        dag = dag_lib.Dag()
        assert dag.primary_tasks is None
        assert dag.termination_delay is None


class TestJobGroupNetworking:
    """Tests for JobGroup networking utilities."""

    def test_jobgroup_name_env_var_constant(self):
        """Test that the JobGroup name env var constant is correctly defined."""
        from sky.jobs import constants as jobs_constants

        assert jobs_constants.SKYPILOT_JOBGROUP_NAME_ENV_VAR == (
            'SKYPILOT_JOBGROUP_NAME')

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
                'sky.provision.kubernetes.utils.'
                'get_kube_config_context_namespace') as mock_get_ns, \
                mock.patch.object(job_group_networking,
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
        """Test _get_k8s_namespace_from_handle returns 'default' for None."""
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
            dns_mappings, 'my-group')

        # Verify script contains required elements
        assert 'MAPPINGS=' in script
        assert 'trainer-0.ns.svc.cluster.local:trainer-0.my-group' in script
        assert 'eval-0.ns.svc.cluster.local:eval-0.my-group' in script
        assert 'getent hosts' in script
        assert '/etc/hosts' in script
        assert 'SkyPilot JobGroup K8s entries' in script
        assert 'while true' in script  # Background loop
        # Verify idempotency (grep -v for filtering old entries)
        assert 'grep -v' in script

    def test_generate_k8s_dns_updater_script_empty_mappings(self):
        """Test DNS updater returns empty for no mappings."""
        from sky.jobs import job_group_networking

        script = job_group_networking.generate_k8s_dns_updater_script(
            [], 'my-group')
        assert script == ''

    def test_generate_k8s_dns_updater_script_includes_sudo_alias(self):
        """Test DNS updater includes sudo alias for root user support.

        This tests the fix for the bug where JobGroup networking fails on
        container images without sudo installed (e.g., pytorch/pytorch)
        even when running as root (uid=0).

        The fix adds ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD which creates a
        function that makes sudo a no-op when running as root.
        """
        from sky.jobs import job_group_networking
        from sky.utils import command_runner

        dns_mappings = [('trainer-0.ns.svc.cluster.local', 'trainer-0.my-group')
                       ]

        script = job_group_networking.generate_k8s_dns_updater_script(
            dns_mappings, 'my-group')

        # Verify the sudo alias is included in the script
        assert 'function sudo()' in script, (
            'DNS updater script should include sudo alias for root user')
        assert '$(whoami)' in script or 'whoami' in script, (
            'Script should check if running as root')
        # Verify the script still uses sudo commands (which will be aliased)
        assert 'sudo grep' in script or 'sudo tee' in script, (
            'Script should still use sudo commands (to be aliased when root)')


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
        mock_task.time_estimator_func = mock.MagicMock()
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
        """Test cheapest valid infra is selected when minimize_cost=True."""
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
        """Test _find_common_infras correctly identifies region-specific infras."""
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
        # We're looking for:
        #   await context_utils.to_thread(..._download_log_and_stream...)
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
                        f'Async method {node.name} calls '
                        f'_download_log_and_stream but does not use '
                        f'to_thread - this will block the event loop!')

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


class TestPrimaryAuxiliaryLogic:
    """Tests for primary/auxiliary job logic and edge cases."""

    def test_multiple_primary_tasks(self):
        """Test JobGroup with multiple primary tasks."""
        yaml_content = """
---
name: multi-primary
execution: parallel
primary_tasks: [trainer, evaluator]
termination_delay: 30s
---
name: trainer
run: echo trainer
---
name: evaluator
run: echo evaluator
---
name: replay-buffer
run: echo buffer
---
name: data-server
run: echo data
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                assert set(dag.primary_tasks) == {'trainer', 'evaluator'}
                assert len(dag.tasks) == 4
                # Verify non-primary tasks get the delay
                assert dag.get_termination_delay_secs('replay-buffer') == 30
                assert dag.get_termination_delay_secs('data-server') == 30
                # Primary tasks also get the delay (though they won't be terminated)
                assert dag.get_termination_delay_secs('trainer') == 30
            finally:
                os.unlink(f.name)

    def test_empty_primary_tasks_list_means_all_primary(self):
        """Test that empty primary_tasks list means all jobs are primary.

        An empty list is treated as equivalent to not setting primary_tasks
        (all jobs are primary), so it's stored as None.
        """
        yaml_content = """
---
name: all-primary
execution: parallel
primary_tasks: []
---
name: job1
run: echo job1
---
name: job2
run: echo job2
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                # Empty list is treated as "all primary" (same as None)
                assert dag.primary_tasks is None
            finally:
                os.unlink(f.name)

    def test_no_primary_tasks_field_means_all_primary(self):
        """Test that omitting primary_tasks means all jobs are primary."""
        yaml_content = """
---
name: default-all-primary
execution: parallel
---
name: job1
run: echo job1
---
name: job2
run: echo job2
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                assert dag.primary_tasks is None
            finally:
                os.unlink(f.name)

    def test_termination_delay_without_primary_tasks_allowed(self):
        """Test that termination_delay without primary_tasks is allowed.

        When primary_tasks is not specified, all jobs are primary, so
        termination_delay has no effect but is still allowed.
        """
        yaml_content = """
---
name: delay-no-primary
execution: parallel
termination_delay: 30s
---
name: job1
run: echo job1
---
name: job2
run: echo job2
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                # termination_delay is set even without primary_tasks
                assert dag.termination_delay == '30s'
                # primary_tasks is None (all jobs are primary)
                assert dag.primary_tasks is None
            finally:
                os.unlink(f.name)

    def test_primary_task_can_be_single_item(self):
        """Test primary_tasks with a single item works."""
        yaml_content = """
---
name: single-primary
execution: parallel
primary_tasks: [trainer]
---
name: trainer
run: echo trainer
---
name: aux1
run: echo aux1
---
name: aux2
run: echo aux2
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                assert dag.primary_tasks == ['trainer']
            finally:
                os.unlink(f.name)

    def test_dict_termination_delay_with_partial_jobs(self):
        """Test dict termination_delay with only some jobs specified."""
        yaml_content = """
---
name: partial-delays
execution: parallel
primary_tasks: [trainer]
termination_delay:
  replay-buffer: 1m
---
name: trainer
run: echo trainer
---
name: replay-buffer
run: echo buffer
---
name: data-server
run: echo data
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                # replay-buffer has explicit delay
                assert dag.get_termination_delay_secs('replay-buffer') == 60
                # data-server has no entry and no default, gets 0
                assert dag.get_termination_delay_secs('data-server') == 0
            finally:
                os.unlink(f.name)

    def test_termination_delay_zero_is_valid(self):
        """Test that termination_delay of 0 or 0s is valid."""
        yaml_content = """
---
name: zero-delay
execution: parallel
primary_tasks: [trainer]
termination_delay: 0s
---
name: trainer
run: echo trainer
---
name: aux
run: echo aux
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                assert dag.get_termination_delay_secs('aux') == 0
            finally:
                os.unlink(f.name)

    def test_termination_delay_large_value(self):
        """Test termination_delay with large values like 1 hour."""
        yaml_content = """
---
name: large-delay
execution: parallel
primary_tasks: [trainer]
termination_delay: 1h
---
name: trainer
run: echo trainer
---
name: aux
run: echo aux
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                dag = dag_utils.load_job_group_from_yaml(f.name)
                assert dag.get_termination_delay_secs('aux') == 3600
            finally:
                os.unlink(f.name)

    def test_round_trip_serialization_with_primary_auxiliary(self):
        """Test that primary_tasks survives YAML round-trip."""
        dag = dag_lib.Dag()
        dag.name = 'round-trip-test'
        dag.set_execution(dag_lib.DagExecution.PARALLEL)
        dag.primary_tasks = ['primary1', 'primary2']
        dag.termination_delay = {'default': '30s', 'aux1': '1m'}

        # Add tasks
        for name in ['primary1', 'primary2', 'aux1', 'aux2']:
            task = task_lib.Task(name=name)
            task.set_resources(resources_lib.Resources())
            dag.add(task)

        # Serialize to YAML
        yaml_str = dag_utils.dump_job_group_to_yaml_str(dag)

        # Reload from YAML
        reloaded = dag_utils.load_job_group_from_yaml_str(yaml_str)

        assert set(reloaded.primary_tasks) == {'primary1', 'primary2'}
        assert isinstance(reloaded.termination_delay, dict)
        assert reloaded.get_termination_delay_secs('aux1') == 60
        assert reloaded.get_termination_delay_secs('aux2') == 30
        assert reloaded.get_termination_delay_secs('unknown') == 30


class TestPrimaryAuxiliaryDagMethods:
    """Tests for Dag methods related to primary/auxiliary logic."""

    def test_is_primary_task_with_primary_tasks_set(self):
        """Test is_primary_task when primary_tasks is explicitly set."""
        dag = dag_lib.Dag()
        dag.primary_tasks = ['trainer', 'evaluator']

        # Add tasks to the DAG
        for name in ['trainer', 'evaluator', 'replay-buffer']:
            task = task_lib.Task(name=name)
            task.set_resources(resources_lib.Resources())
            dag.add(task)

        assert dag.is_primary_task('trainer') is True
        assert dag.is_primary_task('evaluator') is True
        assert dag.is_primary_task('replay-buffer') is False

    def test_is_primary_task_when_all_primary(self):
        """Test is_primary_task when primary_tasks is None (all primary)."""
        dag = dag_lib.Dag()
        dag.primary_tasks = None

        for name in ['job1', 'job2']:
            task = task_lib.Task(name=name)
            task.set_resources(resources_lib.Resources())
            dag.add(task)

        # All tasks should be primary when primary_tasks is None
        assert dag.is_primary_task('job1') is True
        assert dag.is_primary_task('job2') is True

    def test_is_primary_task_empty_list(self):
        """Test is_primary_task when primary_tasks is empty list."""
        dag = dag_lib.Dag()
        dag.primary_tasks = []

        for name in ['job1', 'job2']:
            task = task_lib.Task(name=name)
            task.set_resources(resources_lib.Resources())
            dag.add(task)

        # Empty list means all tasks are primary
        assert dag.is_primary_task('job1') is True
        assert dag.is_primary_task('job2') is True

    def test_get_auxiliary_tasks(self):
        """Test getting auxiliary task names."""
        dag = dag_lib.Dag()
        dag.primary_tasks = ['trainer']

        for name in ['trainer', 'aux1', 'aux2']:
            task = task_lib.Task(name=name)
            task.set_resources(resources_lib.Resources())
            dag.add(task)

        auxiliary = dag.get_auxiliary_task_names()
        assert set(auxiliary) == {'aux1', 'aux2'}

    def test_get_auxiliary_tasks_all_primary(self):
        """Test get_auxiliary_task_names when all tasks are primary."""
        dag = dag_lib.Dag()
        dag.primary_tasks = None

        for name in ['job1', 'job2']:
            task = task_lib.Task(name=name)
            task.set_resources(resources_lib.Resources())
            dag.add(task)

        # All tasks are primary, so no auxiliary tasks
        auxiliary = dag.get_auxiliary_task_names()
        assert auxiliary == []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
