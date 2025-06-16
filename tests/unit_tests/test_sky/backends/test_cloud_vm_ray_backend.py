"""Unit tests for CloudVmRayBackend task configuration redaction functionality."""

from sky import task


class TestCloudVmRayBackendTaskRedaction:
    """Tests for CloudVmRayBackend usage of redacted task configs."""

    def test_cloud_vm_ray_backend_redaction_usage_pattern(self):
        """Test the exact usage pattern from the CloudVmRayBackend code."""
        # Create a task with sensitive environment variables
        test_task = task.Task(run='echo hello',
                              envs={
                                  'API_KEY': 'secret-api-key-123',
                                  'DATABASE_PASSWORD': 'super-secret-password',
                                  'DEBUG': 'true'
                              })

        # Test the exact pattern used in sky/backends/cloud_vm_ray_backend.py line 3199:
        # task_config = task.to_yaml_config(redact_envs=True)
        task_config = None
        if test_task is not None:
            task_config = test_task.to_yaml_config(redact_envs=True)

        # Verify the config was created and sensitive data is redacted
        assert task_config is not None
        assert 'envs' in task_config
        assert task_config['envs']['API_KEY'] == '<redacted>'
        assert task_config['envs']['DATABASE_PASSWORD'] == '<redacted>'
        assert task_config['envs']['DEBUG'] == '<redacted>'

        # Verify the run command is preserved
        assert task_config['run'] == 'echo hello'

    def test_redacted_config_contains_no_sensitive_data(self):
        """Test that redacted task config doesn't contain sensitive environment data."""
        # Create a task with sensitive environment variables
        test_task = task.Task(run='echo hello',
                              envs={
                                  'API_KEY': 'secret-api-key-123',
                                  'DATABASE_PASSWORD': 'super-secret-password',
                                  'AWS_SECRET_ACCESS_KEY': 'aws-secret-key',
                                  'STRIPE_SECRET_KEY': 'sk_live_sensitive_key',
                                  'JWT_SECRET': 'jwt-signing-secret',
                                  'DEBUG': 'true',
                                  'PORT': 8080
                              })

        # Get the redacted config as the backend would
        redacted_config = test_task.to_yaml_config(redact_envs=True)

        # Verify sensitive string values are redacted
        assert redacted_config['envs']['API_KEY'] == '<redacted>'
        assert redacted_config['envs']['DATABASE_PASSWORD'] == '<redacted>'
        assert redacted_config['envs']['AWS_SECRET_ACCESS_KEY'] == '<redacted>'
        assert redacted_config['envs']['STRIPE_SECRET_KEY'] == '<redacted>'
        assert redacted_config['envs']['JWT_SECRET'] == '<redacted>'
        assert redacted_config['envs']['DEBUG'] == '<redacted>'

        # Non-string values should be preserved
        assert redacted_config['envs']['PORT'] == 8080

        # Ensure no sensitive data appears anywhere in the config
        config_str = str(redacted_config)
        assert 'secret-api-key-123' not in config_str
        assert 'super-secret-password' not in config_str
        assert 'aws-secret-key' not in config_str
        assert 'sk_live_sensitive_key' not in config_str
        assert 'jwt-signing-secret' not in config_str

    def test_non_redacted_config_contains_actual_values(self):
        """Test that non-redacted config contains actual environment values."""
        # Create a task with environment variables
        test_task = task.Task(run='echo hello',
                              envs={
                                  'API_KEY': 'actual-api-key',
                                  'DEBUG': 'true',
                                  'PORT': 8080
                              })

        # Get the non-redacted config (default behavior)
        non_redacted_config = test_task.to_yaml_config(redact_envs=False)

        # Verify actual values are present
        assert non_redacted_config['envs']['API_KEY'] == 'actual-api-key'
        assert non_redacted_config['envs']['DEBUG'] == 'true'
        assert non_redacted_config['envs']['PORT'] == 8080

        # Also test default behavior (should be same as redact_envs=False)
        default_config = test_task.to_yaml_config()
        assert default_config == non_redacted_config

    def test_backend_redaction_with_no_envs(self):
        """Test backend behavior when task has no environment variables."""
        # Create a task without environment variables
        test_task = task.Task(run='echo hello')

        # Get redacted config
        redacted_config = test_task.to_yaml_config(redact_envs=True)

        # Should not have envs key at all
        assert 'envs' not in redacted_config

        # Should still have other task properties
        assert redacted_config['run'] == 'echo hello'

    def test_backend_redaction_preserves_task_structure(self):
        """Test that redaction preserves all non-env task configuration."""
        from sky import resources

        # Create a comprehensive task
        test_task = task.Task(run='python train.py',
                              envs={
                                  'SECRET': 'value',
                                  'PORT': 8080
                              },
                              workdir='/app',
                              name='training-task')
        # Set resources using the proper method
        test_task.set_resources(resources.Resources(cpus=4, memory=8))

        # Get both configs
        original_config = test_task.to_yaml_config(redact_envs=False)
        redacted_config = test_task.to_yaml_config(redact_envs=True)

        # All non-env fields should be identical
        for key in original_config:
            if key != 'envs':
                assert original_config[key] == redacted_config[key]

        # Env handling should be different
        assert original_config['envs']['SECRET'] == 'value'
        assert redacted_config['envs']['SECRET'] == '<redacted>'

        # Non-string env values should be the same
        assert original_config['envs']['PORT'] == redacted_config['envs'][
            'PORT'] == 8080
