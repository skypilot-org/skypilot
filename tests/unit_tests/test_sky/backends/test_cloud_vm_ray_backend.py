"""Unit tests for CloudVmRayBackend task configuration redaction functionality."""

from sky import task


class TestCloudVmRayBackendTaskRedaction:
    """Tests for CloudVmRayBackend usage of redacted task configs."""

    def test_cloud_vm_ray_backend_redaction_usage_pattern(self):
        """Test the exact usage pattern from the CloudVmRayBackend code."""
        # Create a task with sensitive secret variables and regular environment variables
        test_task = task.Task(run='echo hello',
                              envs={
                                  'DEBUG': 'true',
                                  'PORT': '8080'
                              },
                              secrets={
                                  'API_KEY': 'sk-very-secret-key-123',
                                  'DATABASE_PASSWORD': 'super-secret-password',
                                  'JWT_SECRET': 'jwt-signing-secret-456'
                              })

        # Test the exact call pattern used in cloud_vm_ray_backend.py
        task_config = test_task.to_yaml_config(redact_secrets=True)

        # Verify that environment variables are NOT redacted
        assert 'envs' in task_config
        assert task_config['envs']['DEBUG'] == 'true'
        assert task_config['envs']['PORT'] == '8080'

        # Verify that secrets ARE redacted
        assert 'secrets' in task_config
        assert task_config['secrets']['API_KEY'] == '<redacted>'
        assert task_config['secrets']['DATABASE_PASSWORD'] == '<redacted>'
        assert task_config['secrets']['JWT_SECRET'] == '<redacted>'

        # Verify other task fields are preserved
        assert task_config['run'] == 'echo hello'

    def test_backend_task_config_without_secrets(self):
        """Test task config generation when no secrets are present."""
        test_task = task.Task(run='python train.py',
                              envs={'PYTHONPATH': '/app'})

        task_config = test_task.to_yaml_config(redact_secrets=True)

        # Environment variables should be preserved
        assert task_config['envs']['PYTHONPATH'] == '/app'

        # No secrets field should be present
        assert 'secrets' not in task_config or not task_config.get('secrets')

    def test_backend_task_config_empty_secrets(self):
        """Test task config generation with empty secrets dict."""
        test_task = task.Task(run='python train.py',
                              envs={'PYTHONPATH': '/app'},
                              secrets={})

        task_config = test_task.to_yaml_config(redact_secrets=True)

        # Environment variables should be preserved
        assert task_config['envs']['PYTHONPATH'] == '/app'

        # Empty secrets should not appear in config
        assert 'secrets' not in task_config

    def test_backend_redaction_preserves_non_string_values(self):
        """Test that non-string values in secrets are preserved during redaction."""
        test_task = task.Task(run='echo hello',
                              secrets={
                                  'STRING_SECRET': 'actual-secret',
                                  'NUMERIC_PORT': 5432,
                                  'BOOLEAN_FLAG': True,
                                  'NULL_VALUE': None
                              })

        task_config = test_task.to_yaml_config(redact_secrets=True)

        # String values should be redacted
        assert task_config['secrets']['STRING_SECRET'] == '<redacted>'

        # Non-string values should be preserved
        assert task_config['secrets']['NUMERIC_PORT'] == 5432
        assert task_config['secrets']['BOOLEAN_FLAG'] is True
        assert task_config['secrets']['NULL_VALUE'] is None

    def test_backend_supports_both_redaction_modes(self):
        """Test that backend can use both redacted and non-redacted configs."""
        test_task = task.Task(run='echo hello',
                              secrets={'API_KEY': 'secret-key-123'})

        # Test redacted mode (for logging/display)
        redacted_config = test_task.to_yaml_config(redact_secrets=True)
        assert redacted_config['secrets']['API_KEY'] == '<redacted>'

        # Test non-redacted mode (for execution)
        full_config = test_task.to_yaml_config(redact_secrets=False)
        assert full_config['secrets']['API_KEY'] == 'secret-key-123'

    def test_backend_mixed_envs_and_secrets(self):
        """Test backend behavior with both envs and secrets containing sensitive data."""
        test_task = task.Task(
            run='echo hello',
            envs={
                'PUBLIC_API_URL': 'https://api.example.com',
                'DEBUG_MODE': 'true',
                'ENVIRONMENT': 'production'
            },
            secrets={
                'PRIVATE_API_KEY': 'sk-secret-key-123',
                'DATABASE_URL': 'postgresql://user:pass@host:5432/db',
                'OAUTH_CLIENT_SECRET': 'oauth-secret-456'
            })

        task_config = test_task.to_yaml_config(redact_secrets=True)

        # All environment variables should remain visible
        assert task_config['envs'][
            'PUBLIC_API_URL'] == 'https://api.example.com'
        assert task_config['envs']['DEBUG_MODE'] == 'true'
        assert task_config['envs']['ENVIRONMENT'] == 'production'

        # All secrets should be redacted
        assert task_config['secrets']['PRIVATE_API_KEY'] == '<redacted>'
        assert task_config['secrets']['DATABASE_URL'] == '<redacted>'
        assert task_config['secrets']['OAUTH_CLIENT_SECRET'] == '<redacted>'

    def test_backend_config_serialization_safety(self):
        """Test that redacted configs are safe for serialization/logging."""
        import json

        import yaml

        test_task = task.Task(
            run='echo hello',
            envs={'PUBLIC_VAR': 'public-value'},
            secrets={'PRIVATE_KEY': 'very-sensitive-key-data'})

        redacted_config = test_task.to_yaml_config(redact_secrets=True)

        # Should be serializable to JSON
        json_str = json.dumps(redacted_config)
        assert 'very-sensitive-key-data' not in json_str
        assert '<redacted>' in json_str

        # Should be serializable to YAML
        yaml_str = yaml.dump(redacted_config)
        assert 'very-sensitive-key-data' not in yaml_str
        assert '<redacted>' in yaml_str

        # Public values should still be present
        assert 'public-value' in yaml_str

    def test_redacted_config_contains_no_sensitive_data(self):
        """Test that redacted task config doesn't contain sensitive secret data."""
        # Create a task with sensitive secret variables and regular environment variables
        test_task = task.Task(run='echo hello',
                              envs={
                                  'DEBUG': 'true',
                                  'PORT': 8080,
                                  'PUBLIC_VAR': 'public-value'
                              },
                              secrets={
                                  'API_KEY': 'secret-api-key-123',
                                  'DATABASE_PASSWORD': 'super-secret-password',
                                  'AWS_SECRET_ACCESS_KEY': 'aws-secret-key',
                                  'STRIPE_SECRET_KEY': 'sk_live_sensitive_key',
                                  'JWT_SECRET': 'jwt-signing-secret',
                              })

        # Get the redacted config as the backend would
        redacted_config = test_task.to_yaml_config(redact_secrets=True)

        # Verify sensitive string values in secrets are redacted
        assert redacted_config['secrets']['API_KEY'] == '<redacted>'
        assert redacted_config['secrets']['DATABASE_PASSWORD'] == '<redacted>'
        assert redacted_config['secrets'][
            'AWS_SECRET_ACCESS_KEY'] == '<redacted>'
        assert redacted_config['secrets']['STRIPE_SECRET_KEY'] == '<redacted>'
        assert redacted_config['secrets']['JWT_SECRET'] == '<redacted>'

        # Verify envs are NOT redacted (preserved as-is)
        assert redacted_config['envs']['DEBUG'] == 'true'
        assert redacted_config['envs']['PORT'] == 8080
        assert redacted_config['envs']['PUBLIC_VAR'] == 'public-value'

        # Ensure no sensitive data appears anywhere in the config
        config_str = str(redacted_config)
        assert 'secret-api-key-123' not in config_str
        assert 'super-secret-password' not in config_str
        assert 'aws-secret-key' not in config_str
        assert 'sk_live_sensitive_key' not in config_str
        assert 'jwt-signing-secret' not in config_str

        # But public values should still be present
        assert 'public-value' in config_str

    def test_non_redacted_config_contains_actual_values(self):
        """Test that non-redacted config contains actual secret values."""
        # Create a task with environment variables and secrets
        test_task = task.Task(run='echo hello',
                              envs={
                                  'DEBUG': 'true',
                                  'PORT': 8080
                              },
                              secrets={
                                  'API_KEY': 'actual-api-key',
                                  'JWT_SECRET': 'actual-jwt-secret'
                              })

        # Get the non-redacted config
        non_redacted_config = test_task.to_yaml_config(redact_secrets=False)

        # Verify actual values are present in both envs and secrets
        assert non_redacted_config['envs']['DEBUG'] == 'true'
        assert non_redacted_config['envs']['PORT'] == 8080
        assert non_redacted_config['secrets']['API_KEY'] == 'actual-api-key'
        assert non_redacted_config['secrets'][
            'JWT_SECRET'] == 'actual-jwt-secret'

        # Also test default behavior (should NOT redact secrets by default)
        default_config = test_task.to_yaml_config()
        assert default_config['envs'] == non_redacted_config[
            'envs']  # envs same
        assert default_config['secrets'][
            'API_KEY'] == 'actual-api-key'  # secrets not redacted by default

    def test_backend_redaction_with_no_secrets(self):
        """Test backend behavior when task has no secret variables."""
        # Create a task with only environment variables, no secrets
        test_task = task.Task(run='echo hello', envs={'DEBUG': 'true'})

        # Get redacted config
        redacted_config = test_task.to_yaml_config(redact_secrets=True)

        # Should not have secrets key at all
        assert 'secrets' not in redacted_config

        # Should have envs key with actual values (not redacted)
        assert 'envs' in redacted_config
        assert redacted_config['envs']['DEBUG'] == 'true'

        # Should still have other task properties
        assert redacted_config['run'] == 'echo hello'

    def test_backend_redaction_preserves_task_structure(self):
        """Test that redaction preserves all non-secret task configuration."""
        from sky import resources

        # Create a comprehensive task
        test_task = task.Task(run='python train.py',
                              envs={
                                  'DEBUG': 'true',
                                  'PORT': 8080
                              },
                              secrets={
                                  'API_KEY': 'secret-value',
                                  'DB_PASSWORD': 'secret-password'
                              },
                              workdir='/app',
                              name='training-task')
        # Set resources using the proper method
        test_task.set_resources(resources.Resources(cpus=4, memory=8))

        # Get both configs
        original_config = test_task.to_yaml_config(redact_secrets=False)
        redacted_config = test_task.to_yaml_config(redact_secrets=True)

        # All non-secret fields should be identical
        for key in original_config:
            if key != 'secrets':
                assert original_config[key] == redacted_config[key]

        # Envs should be identical (not redacted)
        assert original_config['envs'] == redacted_config['envs']
        assert redacted_config['envs']['DEBUG'] == 'true'
        assert redacted_config['envs']['PORT'] == 8080

        # Secret handling should be different
        assert original_config['secrets']['API_KEY'] == 'secret-value'
        assert redacted_config['secrets']['API_KEY'] == '<redacted>'
        assert original_config['secrets']['DB_PASSWORD'] == 'secret-password'
        assert redacted_config['secrets']['DB_PASSWORD'] == '<redacted>'
