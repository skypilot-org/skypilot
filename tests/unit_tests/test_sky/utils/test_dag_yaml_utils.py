"""Unit tests for dag_utils functions."""

import os
import tempfile

import pytest
import yaml

from sky import dag as dag_lib
from sky import task as task_lib
from sky.utils import dag_utils


class TestDumpChainDagToYamlStr:
    """Test dump_chain_dag_to_yaml_str function with user specified YAML."""

    def test_dump_chain_dag_with_use_user_specified_yaml_false(self):
        """Test dumping chain DAG with use_user_specified_yaml=False (default)."""
        # Create a DAG with a task containing secrets
        dag = dag_lib.Dag()
        dag.name = 'test-dag'

        task = task_lib.Task(run='echo hello',
                             envs={'PUBLIC_VAR': 'public-value'},
                             secrets={
                                 'SECRET_KEY': 'secret-value',
                                 'API_TOKEN': 'api-token-123'
                             })
        dag.add(task)

        # Test default behavior (should not redact)
        yaml_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

        # Verify secrets are not redacted
        assert 'secret-value' in yaml_str
        assert 'api-token-123' in yaml_str
        assert '<redacted>' not in yaml_str

        # Verify envs are preserved
        assert 'public-value' in yaml_str

    def test_dump_chain_dag_with_use_user_specified_yaml_true(self):
        """Test dumping chain DAG with use_user_specified_yaml=True."""

        # Create a DAG with tasks from user specified YAML
        user_yaml_configs = [{
            'name': 'test-dag'
        }, {
            'run': 'echo hello',
            'envs': {
                'PUBLIC_VAR': 'public-value'
            },
            'secrets': {
                'SECRET_KEY': 'secret-value',
                'API_TOKEN': 'api-token-123'
            }
        }]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump_all(user_yaml_configs, f)
            f.flush()
            dag = dag_utils.load_chain_dag_from_yaml(f.name)

        os.unlink(f.name)

        # Test with explicit user specified YAML
        yaml_str = dag_utils.dump_chain_dag_to_yaml_str(
            dag, use_user_specified_yaml=True)

        # Verify secrets are redacted
        assert 'secret-value' not in yaml_str
        assert 'api-token-123' not in yaml_str
        assert '<redacted>' in yaml_str

        # Verify envs are preserved
        assert 'public-value' in yaml_str

    def test_dump_chain_dag_with_mixed_secret_types(self):
        """Test dumping chain DAG with mixed secret value types."""
        # Create a DAG with a task containing different types of secrets
        user_yaml_configs = [{
            'name': 'test-dag'
        }, {
            'run': 'echo hello',
            'secrets': {
                'STRING_SECRET': 'secret-string',
                'NUMERIC_SECRET': 5432,
                'BOOLEAN_SECRET': True,
                'NULL_SECRET': None
            }
        }]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump_all(user_yaml_configs, f)
            f.flush()
            dag = dag_utils.load_chain_dag_from_yaml(f.name)

        os.unlink(f.name)

        # Test with user specified YAML (redaction enabled)
        yaml_str_user_specified = dag_utils.dump_chain_dag_to_yaml_str(
            dag, use_user_specified_yaml=True)

        # All secret values should be redacted when using user specified YAML
        assert 'secret-string' not in yaml_str_user_specified
        assert '<redacted>' in yaml_str_user_specified

        # Test without user specified YAML (default)
        yaml_str_default = dag_utils.dump_chain_dag_to_yaml_str(dag)

        # All values should be preserved
        assert 'secret-string' in yaml_str_default
        assert '5432' in yaml_str_default
        assert 'true' in yaml_str_default.lower()

    def test_dump_chain_dag_no_secrets(self):
        """Test dumping chain DAG when no secrets are present."""
        # Create a DAG with a task containing only envs
        user_yaml_configs = [{
            'name': 'test-dag'
        }, {
            'run': 'echo hello',
            'envs': {
                'DEBUG': 'true',
                'PORT': '8080'
            }
        }]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump_all(user_yaml_configs, f)
            f.flush()
            dag = dag_utils.load_chain_dag_from_yaml(f.name)

        os.unlink(f.name)

        # Test both modes (should be similar since no secrets exist)
        yaml_str_user_specified = dag_utils.dump_chain_dag_to_yaml_str(
            dag, use_user_specified_yaml=True)
        yaml_str_default = dag_utils.dump_chain_dag_to_yaml_str(dag)

        # Verify envs are preserved in both
        assert 'true' in yaml_str_user_specified
        assert '8080' in yaml_str_user_specified
        assert 'true' in yaml_str_default
        assert '8080' in yaml_str_default

        # No redaction markers should be present
        assert '<redacted>' not in yaml_str_user_specified
        assert '<redacted>' not in yaml_str_default

    def test_dump_chain_dag_default_behavior_matches_task_default(self):
        """Test that DAG dumping default behavior matches Task default behavior."""
        # Create a DAG with a task containing secrets
        user_yaml_configs = [{
            'name': 'test-dag'
        }, {
            'run': 'echo hello',
            'secrets': {
                'SECRET_KEY': 'secret-value'
            }
        }]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump_all(user_yaml_configs, f)
            f.flush()
            dag = dag_utils.load_chain_dag_from_yaml(f.name)

        os.unlink(f.name)

        task = dag.tasks[0]

        # Get default behavior from both methods
        dag_yaml_str = dag_utils.dump_chain_dag_to_yaml_str(dag)
        task_config = task.to_yaml_config()

        # Both should have the same behavior (no redaction by default)
        assert 'secret-value' in dag_yaml_str
        assert task_config['secrets']['SECRET_KEY'] == 'secret-value'

        # Test user specified YAML for both
        dag_yaml_str_user_specified = dag_utils.dump_chain_dag_to_yaml_str(
            dag, use_user_specified_yaml=True)
        task_config_user_specified = task.to_yaml_config(
            use_user_specified_yaml=True)

        # Both should redact secrets when using user specified YAML
        assert 'secret-value' not in dag_yaml_str_user_specified
        assert '<redacted>' in dag_yaml_str_user_specified
        assert task_config_user_specified['secrets'][
            'SECRET_KEY'] == '<redacted>'

    def test_dump_chain_dag_multiple_tasks(self):
        """Test dumping chain DAG with multiple tasks having secrets."""
        # Create a chain DAG with multiple tasks
        user_yaml_configs = [{
            'name': 'multi-task-dag'
        }, {
            'run': 'echo task1',
            'secrets': {
                'TASK1_SECRET': 'task1-secret-value'
            }
        }, {
            'run': 'echo task2',
            'secrets': {
                'TASK2_SECRET': 'task2-secret-value'
            }
        }]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump_all(user_yaml_configs, f)
            f.flush()
            dag = dag_utils.load_chain_dag_from_yaml(f.name)

        os.unlink(f.name)

        # Test default behavior (no redaction)
        yaml_str_default = dag_utils.dump_chain_dag_to_yaml_str(dag)
        assert 'task1-secret-value' in yaml_str_default
        assert 'task2-secret-value' in yaml_str_default

        # Test with user specified YAML (redaction)
        yaml_str_user_specified = dag_utils.dump_chain_dag_to_yaml_str(
            dag, use_user_specified_yaml=True)
        assert 'task1-secret-value' not in yaml_str_user_specified
        assert 'task2-secret-value' not in yaml_str_user_specified
        assert '<redacted>' in yaml_str_user_specified
