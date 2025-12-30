"""Tests for JobGroup functionality."""
import os
import tempfile

import pytest

from sky import dag as dag_lib
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
                with pytest.raises(ValueError, match='must have a "name" field'):
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
        dag.set_job_group(
            dag_lib.JobGroupPlacement.SAME_INFRA,
            dag_lib.JobGroupExecution.PARALLEL
        )
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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
