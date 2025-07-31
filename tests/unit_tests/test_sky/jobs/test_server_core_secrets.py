"""Unit tests to prevent regression in managed job secrets handling.

This test ensures that:
1. Managed jobs receive actual secrets for execution (not redacted)
2. Secrets are redacted only for display/logging purposes
3. Environment variables are never redacted
"""

import os
import tempfile

import pytest

from sky import dag as dag_lib
from sky import task as task_lib
from sky.utils import dag_utils


class TestManagedJobSecrets:
    """Tests to prevent regression in secrets handling for managed jobs."""

    def test_job_server_secrets_separation(self):
        """Test that job server correctly separates display vs execution secrets.
        
        This test prevents regression where managed jobs might receive 
        redacted secrets instead of actual secrets for execution.
        """
        # Create a DAG with secrets that a managed job would use
        dag = dag_lib.Dag()
        dag.name = 'test-managed-job'

        task = task_lib.Task(
            run='echo "Using secrets for job execution"',
            envs={
                'PUBLIC_API_URL': 'https://api.example.com',
                'DEBUG_MODE': 'true'
            },
            secrets={
                'API_KEY': 'sk-1234567890abcdef',
                'DATABASE_PASSWORD': 'super-secret-db-password',
                'JWT_SECRET': 'jwt-signing-secret-key',
                'OAUTH_TOKEN': 'oauth-token-for-api-access'
            })
        dag.add(task)

        # Simulate what happens in jobs/server/core.py

        # 1. For display/logging (should be redacted for security)
        user_dag_str = dag_utils.dump_chain_dag_to_yaml_str(
            dag, use_user_specified_yaml=True)

        # 2. For actual job execution (should have real secrets)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            execution_yaml_path = f.name
            dag_utils.dump_chain_dag_to_yaml(dag, execution_yaml_path)

        try:
            # Verify display YAML has redacted secrets (for security)
            assert '<redacted>' in user_dag_str
            assert 'sk-1234567890abcdef' not in user_dag_str
            assert 'super-secret-db-password' not in user_dag_str
            assert 'jwt-signing-secret-key' not in user_dag_str
            assert 'oauth-token-for-api-access' not in user_dag_str

            # Verify display YAML preserves environment variables
            assert 'https://api.example.com' in user_dag_str
            assert 'true' in user_dag_str

            # Verify execution YAML has actual secrets (for job execution)
            with open(execution_yaml_path, 'r', encoding='utf-8') as f:
                execution_yaml_content = f.read()

            assert 'sk-1234567890abcdef' in execution_yaml_content
            assert 'super-secret-db-password' in execution_yaml_content
            assert 'jwt-signing-secret-key' in execution_yaml_content
            assert 'oauth-token-for-api-access' in execution_yaml_content

            # Verify execution YAML preserves environment variables
            assert 'https://api.example.com' in execution_yaml_content
            assert 'true' in execution_yaml_content

            # Critical: execution YAML should NOT have redacted markers
            assert '<redacted>' not in execution_yaml_content

        finally:
            os.unlink(execution_yaml_path)

    def test_task_config_defaults_prevent_regression(self):
        """Test that Task.to_yaml_config() defaults prevent secret redaction regression.
        
        This ensures the default behavior doesn't accidentally redact secrets
        that jobs need for execution.
        """
        import tempfile

        import yaml

        # Create task from user YAML to test user specified behavior
        user_yaml_config = {
            'run': 'echo "Job needs actual secrets"',
            'envs': {
                'PUBLIC_VAR': 'public-value'
            },
            'secrets': {
                'CRITICAL_SECRET': 'job-execution-needs-this',
                'API_TOKEN': 'real-token-value'
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            task = task_lib.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        # Default behavior should NOT redact (for execution)
        default_config = task.to_yaml_config()

        # Explicit execution config (for execution)
        execution_config = task.to_yaml_config()

        # User specified YAML (for display/logging)
        display_config = task.to_yaml_config(use_user_specified_yaml=True)

        # Verify default behavior preserves secrets for job execution
        assert default_config['secrets'][
            'CRITICAL_SECRET'] == 'job-execution-needs-this'
        assert default_config['secrets']['API_TOKEN'] == 'real-token-value'

        # Verify explicit execution config has real secrets
        assert execution_config['secrets'][
            'CRITICAL_SECRET'] == 'job-execution-needs-this'
        assert execution_config['secrets']['API_TOKEN'] == 'real-token-value'

        # Verify display config redacts secrets for security
        assert display_config['secrets']['CRITICAL_SECRET'] == '<redacted>'
        assert display_config['secrets']['API_TOKEN'] == '<redacted>'

        # Verify environment variables are never redacted
        assert default_config['envs']['PUBLIC_VAR'] == 'public-value'
        assert execution_config['envs']['PUBLIC_VAR'] == 'public-value'
        assert display_config['envs']['PUBLIC_VAR'] == 'public-value'

    def test_dag_dumping_defaults_prevent_regression(self):
        """Test that DAG dumping defaults prevent secret redaction regression.
        
        This ensures dump_chain_dag_to_yaml_str() and dump_chain_dag_to_yaml()
        have correct default behavior for job execution vs display.
        """
        dag = dag_lib.Dag()
        dag.name = 'regression-test-dag'

        task = task_lib.Task(run='echo "Testing DAG secret handling"',
                             secrets={
                                 'EXECUTION_SECRET': 'job-needs-this-value',
                                 'SERVICE_KEY': 'service-authentication-key'
                             })
        dag.add(task)

        # Default string dumping (for execution) - should preserve secrets
        default_yaml_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

        # Explicit execution string dumping (for execution)
        execution_yaml_str = dag_utils.dump_chain_dag_to_yaml_str(dag)

        # User specified string dumping (for display/logging)
        display_yaml_str = dag_utils.dump_chain_dag_to_yaml_str(
            dag, use_user_specified_yaml=True)

        # File dumping (for execution) - should preserve secrets
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            file_yaml_path = f.name
            dag_utils.dump_chain_dag_to_yaml(dag, file_yaml_path)

        try:
            # Verify default behavior preserves secrets for execution
            assert 'job-needs-this-value' in default_yaml_str
            assert 'service-authentication-key' in default_yaml_str
            assert '<redacted>' not in default_yaml_str

            # Verify explicit execution preserves secrets
            assert 'job-needs-this-value' in execution_yaml_str
            assert 'service-authentication-key' in execution_yaml_str
            assert '<redacted>' not in execution_yaml_str

            # Verify display redacts secrets for security
            assert 'job-needs-this-value' not in display_yaml_str
            assert 'service-authentication-key' not in display_yaml_str
            assert '<redacted>' in display_yaml_str

            # Verify file dumping preserves secrets for execution
            with open(file_yaml_path, 'r', encoding='utf-8') as f:
                file_yaml_content = f.read()

            assert 'job-needs-this-value' in file_yaml_content
            assert 'service-authentication-key' in file_yaml_content
            assert '<redacted>' not in file_yaml_content

        finally:
            os.unlink(file_yaml_path)

    def test_managed_job_launch_simulation(self):
        """Test simulation of managed job launch to prevent secrets regression.
        
        This simulates the exact pattern used in sky/jobs/server/core.py
        to ensure managed jobs get real secrets while display is secure.
        """
        # Simulate a user's DAG with secrets
        dag = dag_lib.Dag()
        dag.name = 'user-job-with-secrets'

        task = task_lib.Task(
            run='python train.py --api-key=$API_KEY --db-pass=$DB_PASSWORD',
            envs={
                'MODEL_NAME': 'my-model',
                'BATCH_SIZE': '32'
            },
            secrets={
                'API_KEY': 'sk-prod-api-key-12345',
                'DB_PASSWORD': 'prod-database-secret-password',
                'WANDB_API_KEY': 'wandb-secret-key-67890'
            })
        dag.add(task)

        # Simulate what jobs/server/core.py does:

        # 1. Create user_dag_str for display/logging (redacted for security)
        user_dag_str = dag_utils.dump_chain_dag_to_yaml_str(
            dag, use_user_specified_yaml=True)

        # 2. Create execution YAML for the actual job (real secrets)
        with tempfile.NamedTemporaryFile(prefix='managed-dag-',
                                         mode='w',
                                         suffix='.yaml',
                                         delete=False) as execution_file:
            execution_yaml_path = execution_file.name
            dag_utils.dump_chain_dag_to_yaml(dag, execution_yaml_path)

        # 3. Simulate original user YAML for reference (redacted for security)
        with tempfile.NamedTemporaryFile(prefix='managed-user-dag-',
                                         mode='w',
                                         suffix='.yaml',
                                         delete=False) as user_file:
            user_yaml_path = user_file.name
            user_file.write(user_dag_str)
            user_file.flush()

        try:
            # Verify the user/display YAML is redacted for security
            with open(user_yaml_path, 'r', encoding='utf-8') as f:
                user_yaml_content = f.read()

            assert '<redacted>' in user_yaml_content
            assert 'sk-prod-api-key-12345' not in user_yaml_content
            assert 'prod-database-secret-password' not in user_yaml_content
            assert 'wandb-secret-key-67890' not in user_yaml_content

            # Verify environment variables are preserved in display
            assert 'my-model' in user_yaml_content
            assert '32' in user_yaml_content

            # Verify the execution YAML has real secrets for job execution
            with open(execution_yaml_path, 'r', encoding='utf-8') as f:
                execution_yaml_content = f.read()

            assert 'sk-prod-api-key-12345' in execution_yaml_content
            assert 'prod-database-secret-password' in execution_yaml_content
            assert 'wandb-secret-key-67890' in execution_yaml_content

            # Critical: execution YAML must NOT be redacted
            assert '<redacted>' not in execution_yaml_content

            # Verify environment variables are preserved in execution
            assert 'my-model' in execution_yaml_content
            assert '32' in execution_yaml_content

            # Simulate loading the execution YAML (what the job would see)
            loaded_dag = dag_utils.load_chain_dag_from_yaml(execution_yaml_path)
            loaded_task = loaded_dag.tasks[0]

            # The loaded task must have real secrets for execution
            assert loaded_task.secrets['API_KEY'] == 'sk-prod-api-key-12345'
            assert loaded_task.secrets[
                'DB_PASSWORD'] == 'prod-database-secret-password'
            assert loaded_task.secrets[
                'WANDB_API_KEY'] == 'wandb-secret-key-67890'

            # Environment variables should be preserved
            assert loaded_task.envs['MODEL_NAME'] == 'my-model'
            assert loaded_task.envs['BATCH_SIZE'] == '32'

        finally:
            os.unlink(user_yaml_path)
            os.unlink(execution_yaml_path)

    def test_secrets_in_multiple_tasks_chain(self):
        """Test secrets handling in multi-task chain DAGs for managed jobs.
        
        This prevents regression where some tasks in a chain might get 
        redacted secrets while others get real secrets.
        """
        dag = dag_lib.Dag()
        dag.name = 'multi-task-secrets-chain'

        # First task with secrets
        task1 = task_lib.Task(run='python preprocess.py',
                              secrets={
                                  'DATA_API_KEY': 'data-api-secret-key',
                                  'S3_SECRET': 's3-access-secret'
                              })

        # Second task with different secrets
        task2 = task_lib.Task(run='python train.py',
                              secrets={
                                  'MODEL_API_KEY': 'model-api-secret-key',
                                  'WANDB_KEY': 'wandb-logging-secret'
                              })

        # Create chain
        dag.add(task1)
        dag.add(task2)
        dag.add_edge(task1, task2)

        # Test display vs execution separation
        display_yaml = dag_utils.dump_chain_dag_to_yaml_str(
            dag, use_user_specified_yaml=True)
        execution_yaml = dag_utils.dump_chain_dag_to_yaml_str(dag)

        # Display should redact all secrets
        assert '<redacted>' in display_yaml
        assert 'data-api-secret-key' not in display_yaml
        assert 's3-access-secret' not in display_yaml
        assert 'model-api-secret-key' not in display_yaml
        assert 'wandb-logging-secret' not in display_yaml

        # Execution should preserve all secrets
        assert 'data-api-secret-key' in execution_yaml
        assert 's3-access-secret' in execution_yaml
        assert 'model-api-secret-key' in execution_yaml
        assert 'wandb-logging-secret' in execution_yaml
        assert '<redacted>' not in execution_yaml

        # Test loading execution YAML preserves all secrets
        loaded_dag = dag_utils.load_chain_dag_from_yaml_str(execution_yaml)
        loaded_tasks = loaded_dag.tasks

        assert len(loaded_tasks) == 2
        assert loaded_tasks[0].secrets['DATA_API_KEY'] == 'data-api-secret-key'
        assert loaded_tasks[0].secrets['S3_SECRET'] == 's3-access-secret'
        assert loaded_tasks[1].secrets[
            'MODEL_API_KEY'] == 'model-api-secret-key'
        assert loaded_tasks[1].secrets['WANDB_KEY'] == 'wandb-logging-secret'

    def test_mixed_envs_and_secrets_job_execution(self):
        """Test that envs and secrets are handled correctly for job execution.
        
        This prevents regression where environment variables might be 
        accidentally redacted or secrets might leak into logs.
        """
        import tempfile

        import yaml

        # Create task from user YAML to test user specified behavior
        user_yaml_config = {
            'run': 'echo "Job with mixed envs and secrets"',
            'envs': {
                'PUBLIC_MODEL_URL': 'https://models.example.com/v1',
                'LOG_LEVEL': 'INFO',
                'BATCH_SIZE': '64'
            },
            'secrets': {
                'PRIVATE_API_KEY': 'private-secret-api-key',
                'DB_CONNECTION': 'postgresql://user:secret@host/db',
                'REDIS_PASSWORD': 'redis-secret-password'
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            task = task_lib.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        # Test execution behavior (default)
        execution_config = task.to_yaml_config()

        # Test display behavior (user specified YAML)
        display_config = task.to_yaml_config(use_user_specified_yaml=True)

        # Execution config should have real secrets and envs
        assert execution_config['envs'][
            'PUBLIC_MODEL_URL'] == 'https://models.example.com/v1'
        assert execution_config['envs']['LOG_LEVEL'] == 'INFO'
        assert execution_config['envs']['BATCH_SIZE'] == '64'
        assert execution_config['secrets'][
            'PRIVATE_API_KEY'] == 'private-secret-api-key'
        assert execution_config['secrets'][
            'DB_CONNECTION'] == 'postgresql://user:secret@host/db'
        assert execution_config['secrets'][
            'REDIS_PASSWORD'] == 'redis-secret-password'

        # Display config should redact secrets but preserve envs
        assert display_config['envs'][
            'PUBLIC_MODEL_URL'] == 'https://models.example.com/v1'
        assert display_config['envs']['LOG_LEVEL'] == 'INFO'
        assert display_config['envs']['BATCH_SIZE'] == '64'
        assert display_config['secrets']['PRIVATE_API_KEY'] == '<redacted>'
        assert display_config['secrets']['DB_CONNECTION'] == '<redacted>'
        assert display_config['secrets']['REDIS_PASSWORD'] == '<redacted>'

        # Verify no actual secrets leak into display config
        display_str = str(display_config)
        assert 'private-secret-api-key' not in display_str
        assert 'postgresql://user:secret@host/db' not in display_str
        assert 'redis-secret-password' not in display_str

        # Verify envs are always accessible
        execution_str = str(execution_config)
        assert 'https://models.example.com/v1' in execution_str
        assert 'INFO' in execution_str
        assert '64' in execution_str
