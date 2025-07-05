"""Unit tests for dag_utils functions."""

import pytest

from sky import dag as dag_lib
from sky import task as task_lib
from sky.utils import dag_utils


class TestDumpChainDagToYamlStr:
    """Test dump_chain_dag_to_yaml_str function with secrets redaction."""

    def test_dump_chain_dag_with_redact_secrets_false(self):
        """Test dumping chain DAG with redact_secrets=False (default)."""
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

    def test_dump_chain_dag_with_redact_secrets_true(self):
        """Test dumping chain DAG with redact_secrets=True."""
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

        # Test with explicit redaction
        yaml_str = dag_utils.dump_chain_dag_to_yaml_str(dag,
                                                        redact_secrets=True)

        # Verify secrets are redacted
        assert 'secret-value' not in yaml_str
        assert 'api-token-123' not in yaml_str
        assert '<redacted>' in yaml_str

        # Verify envs are preserved
        assert 'public-value' in yaml_str

    def test_dump_chain_dag_with_mixed_secret_types(self):
        """Test dumping chain DAG with mixed secret value types."""
        # Create a DAG with a task containing different types of secrets
        dag = dag_lib.Dag()
        dag.name = 'test-dag'

        task = task_lib.Task(run='echo hello',
                             secrets={
                                 'STRING_SECRET': 'secret-string',
                                 'NUMERIC_SECRET': 5432,
                                 'BOOLEAN_SECRET': True,
                                 'NULL_SECRET': None
                             })
        dag.add(task)

        # Test with redaction enabled
        yaml_str_redacted = dag_utils.dump_chain_dag_to_yaml_str(
            dag, redact_secrets=True)

        # String values should be redacted
        assert 'secret-string' not in yaml_str_redacted
        assert '<redacted>' in yaml_str_redacted

        # Non-string values should be preserved
        assert '5432' in yaml_str_redacted
        assert 'true' in yaml_str_redacted.lower()
        assert 'null' in yaml_str_redacted.lower()

        # Test without redaction
        yaml_str_no_redact = dag_utils.dump_chain_dag_to_yaml_str(
            dag, redact_secrets=False)

        # All values should be preserved
        assert 'secret-string' in yaml_str_no_redact
        assert '5432' in yaml_str_no_redact
        assert 'true' in yaml_str_no_redact.lower()

    def test_dump_chain_dag_no_secrets(self):
        """Test dumping chain DAG when no secrets are present."""
        # Create a DAG with a task containing only envs
        dag = dag_lib.Dag()
        dag.name = 'test-dag'

        task = task_lib.Task(run='echo hello',
                             envs={
                                 'DEBUG': 'true',
                                 'PORT': '8080'
                             })
        dag.add(task)

        # Test both redaction modes (should be identical)
        yaml_str_redacted = dag_utils.dump_chain_dag_to_yaml_str(
            dag, redact_secrets=True)
        yaml_str_no_redact = dag_utils.dump_chain_dag_to_yaml_str(
            dag, redact_secrets=False)

        # Both should be identical since no secrets exist
        assert yaml_str_redacted == yaml_str_no_redact

        # Verify envs are preserved
        assert 'true' in yaml_str_redacted
        assert '8080' in yaml_str_redacted

        # No redaction markers should be present
        assert '<redacted>' not in yaml_str_redacted

    def test_dump_chain_dag_default_behavior_matches_task_default(self):
        """Test that DAG dumping default behavior matches Task default behavior."""
        # Create a DAG with a task containing secrets
        dag = dag_lib.Dag()
        dag.name = 'test-dag'

        task = task_lib.Task(run='echo hello',
                             secrets={'SECRET_KEY': 'secret-value'})
        dag.add(task)

        # Get default behavior from both methods
        dag_yaml_str = dag_utils.dump_chain_dag_to_yaml_str(dag)
        task_config = task.to_yaml_config()

        # Both should have the same redaction behavior (no redaction by default)
        assert 'secret-value' in dag_yaml_str
        assert task_config['secrets']['SECRET_KEY'] == 'secret-value'

        # Test explicit redaction for both
        dag_yaml_str_redacted = dag_utils.dump_chain_dag_to_yaml_str(
            dag, redact_secrets=True)
        task_config_redacted = task.to_yaml_config(redact_secrets=True)

        # Both should redact secrets
        assert 'secret-value' not in dag_yaml_str_redacted
        assert '<redacted>' in dag_yaml_str_redacted
        assert task_config_redacted['secrets']['SECRET_KEY'] == '<redacted>'

    def test_dump_chain_dag_multiple_tasks(self):
        """Test dumping chain DAG with multiple tasks having secrets."""
        # Create a chain DAG with multiple tasks
        with dag_lib.Dag() as dag:
            dag.name = 'multi-task-dag'

            task1 = task_lib.Task(
                run='echo task1',
                secrets={'TASK1_SECRET': 'task1-secret-value'})
            task2 = task_lib.Task(
                run='echo task2',
                secrets={'TASK2_SECRET': 'task2-secret-value'})

            # Add tasks in a chain relationship
            task1 >> task2

        # Test without redaction
        yaml_str_no_redact = dag_utils.dump_chain_dag_to_yaml_str(
            dag, redact_secrets=False)
        assert 'task1-secret-value' in yaml_str_no_redact
        assert 'task2-secret-value' in yaml_str_no_redact

        # Test with redaction
        yaml_str_redacted = dag_utils.dump_chain_dag_to_yaml_str(
            dag, redact_secrets=True)
        assert 'task1-secret-value' not in yaml_str_redacted
        assert 'task2-secret-value' not in yaml_str_redacted
        assert '<redacted>' in yaml_str_redacted
