"""Unit tests for CloudVmRayBackend task configuration redaction functionality."""

from sky import task


class TestCloudVmRayBackendTaskUserSpecified:
    """Tests for CloudVmRayBackend usage of user specified task configs."""

    def test_cloud_vm_ray_backend_user_specified_usage_pattern(self):
        """Test the exact usage pattern from the CloudVmRayBackend code."""
        import tempfile

        import yaml

        # Create a task with sensitive secret variables and regular environment variables
        user_yaml_config = {
            'run': 'echo hello',
            'envs': {
                'DEBUG': 'true',
                'PORT': '8080'
            },
            'secrets': {
                'API_KEY': 'sk-very-secret-key-123',
                'DATABASE_PASSWORD': 'super-secret-password',
                'JWT_SECRET': 'jwt-signing-secret-456'
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            test_task = task.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        # Test the exact call pattern used in cloud_vm_ray_backend.py
        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

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

        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # When no user YAML exists, should return empty dict
        assert task_config == {}

    def test_backend_task_config_empty_secrets(self):
        """Test task config generation with empty secrets dict."""
        test_task = task.Task(run='python train.py',
                              envs={'PYTHONPATH': '/app'},
                              secrets={})

        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # When no user YAML exists, should return empty dict
        assert task_config == {}

    def test_backend_user_specified_yaml_with_mixed_secret_types(self):
        """Test that user specified YAML properly redacts all secret types."""
        import tempfile

        import yaml

        user_yaml_config = {
            'run': 'echo hello',
            'secrets': {
                'STRING_SECRET': 'actual-secret',
                'NUMERIC_PORT': 5432,
                'BOOLEAN_FLAG': True,
                'NULL_VALUE': None
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            test_task = task.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

        # All secret values should be redacted when using user specified YAML
        assert task_config['secrets']['STRING_SECRET'] == '<redacted>'
        assert task_config['secrets']['NUMERIC_PORT'] == '<redacted>'
        assert task_config['secrets']['BOOLEAN_FLAG'] == '<redacted>'
        assert task_config['secrets']['NULL_VALUE'] == '<redacted>'

    def test_backend_supports_both_config_modes(self):
        """Test that backend can use both user specified and default configs."""
        import tempfile

        import yaml

        user_yaml_config = {
            'run': 'echo hello',
            'secrets': {
                'API_KEY': 'secret-key-123'
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            test_task = task.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        # Test user specified mode (for logging/display)
        user_specified_config = test_task.to_yaml_config(
            use_user_specified_yaml=True)
        assert user_specified_config['secrets']['API_KEY'] == '<redacted>'

        # Test default mode (for execution)
        full_config = test_task.to_yaml_config()
        assert full_config['secrets']['API_KEY'] == 'secret-key-123'

    def test_backend_mixed_envs_and_secrets(self):
        """Test backend behavior with both envs and secrets containing sensitive data."""
        import tempfile

        import yaml

        user_yaml_config = {
            'run': 'echo hello',
            'envs': {
                'PUBLIC_API_URL': 'https://api.example.com',
                'DEBUG_MODE': 'true',
                'ENVIRONMENT': 'production'
            },
            'secrets': {
                'PRIVATE_API_KEY': 'sk-secret-key-123',
                'DATABASE_URL': 'postgresql://user:pass@host:5432/db',
                'OAUTH_CLIENT_SECRET': 'oauth-secret-456'
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            test_task = task.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        task_config = test_task.to_yaml_config(use_user_specified_yaml=True)

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
        """Test that user specified configs are safe for serialization/logging."""
        import json
        import tempfile

        import yaml

        user_yaml_config = {
            'run': 'echo hello',
            'envs': {
                'PUBLIC_VAR': 'public-value'
            },
            'secrets': {
                'PRIVATE_KEY': 'very-sensitive-key-data'
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            test_task = task.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        redacted_config = test_task.to_yaml_config(use_user_specified_yaml=True)

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

    def test_user_specified_config_contains_no_sensitive_data(self):
        """Test that user specified task config doesn't contain sensitive secret data."""
        import tempfile

        import yaml

        # Create a task with sensitive secret variables and regular environment variables
        user_yaml_config = {
            'run': 'echo hello',
            'envs': {
                'DEBUG': 'true',
                'PORT': 8080,
                'PUBLIC_VAR': 'public-value'
            },
            'secrets': {
                'API_KEY': 'secret-api-key-123',
                'DATABASE_PASSWORD': 'super-secret-password',
                'AWS_SECRET_ACCESS_KEY': 'aws-secret-key',
                'STRIPE_SECRET_KEY': 'sk_live_sensitive_key',
                'JWT_SECRET': 'jwt-signing-secret',
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            test_task = task.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        # Get the user specified config as the backend would
        redacted_config = test_task.to_yaml_config(use_user_specified_yaml=True)

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

    def test_default_config_contains_actual_values(self):
        """Test that default config contains actual secret values."""
        import tempfile

        import yaml

        # Create a task with environment variables and secrets
        user_yaml_config = {
            'run': 'echo hello',
            'envs': {
                'DEBUG': 'true',
                'PORT': 8080
            },
            'secrets': {
                'API_KEY': 'actual-api-key',
                'JWT_SECRET': 'actual-jwt-secret'
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            test_task = task.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        # Get the default config
        default_config = test_task.to_yaml_config()

        # Verify actual values are present in both envs and secrets
        assert default_config['envs']['DEBUG'] == 'true'
        assert default_config['envs']['PORT'] == 8080
        assert default_config['secrets']['API_KEY'] == 'actual-api-key'
        assert default_config['secrets']['JWT_SECRET'] == 'actual-jwt-secret'

        # Test user specified behavior (should redact secrets for display)
        user_specified_config = test_task.to_yaml_config(
            use_user_specified_yaml=True)
        assert user_specified_config['envs']['DEBUG'] == 'true'
        assert user_specified_config['envs']['PORT'] == 8080
        assert user_specified_config['secrets']['API_KEY'] == '<redacted>'
        assert user_specified_config['secrets']['JWT_SECRET'] == '<redacted>'

    def test_backend_user_specified_with_no_secrets(self):
        """Test backend behavior when task has no secret variables."""
        import tempfile

        import yaml

        # Create a task with only environment variables, no secrets
        user_yaml_config = {'run': 'echo hello', 'envs': {'DEBUG': 'true'}}

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            test_task = task.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        # Get user specified config
        user_specified_config = test_task.to_yaml_config(
            use_user_specified_yaml=True)

        # Should not have secrets key at all
        assert 'secrets' not in user_specified_config

        # Should have envs key with actual values (not redacted)
        assert 'envs' in user_specified_config
        assert user_specified_config['envs']['DEBUG'] == 'true'

        # Should still have other task properties
        assert user_specified_config['run'] == 'echo hello'

    def test_backend_user_specified_preserves_task_structure(self):
        """Test that user specified config preserves all non-secret task configuration."""
        import tempfile

        import yaml

        from sky import resources

        # Create a comprehensive task
        user_yaml_config = {
            'run': 'python train.py',
            'envs': {
                'DEBUG': 'true',
                'PORT': 8080
            },
            'secrets': {
                'API_KEY': 'secret-value',
                'DB_PASSWORD': 'secret-password'
            },
            'workdir': '/app',
            'name': 'training-task'
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                         delete=False) as f:
            yaml.dump(user_yaml_config, f)
            f.flush()
            test_task = task.Task.from_yaml(f.name)

        import os
        os.unlink(f.name)

        # Set resources using the proper method
        test_task.set_resources(resources.Resources(cpus=4, memory=8))

        # Get both configs
        default_config = test_task.to_yaml_config()
        user_specified_config = test_task.to_yaml_config(
            use_user_specified_yaml=True)

        # All non-secret fields should be preserved in user specified config
        assert user_specified_config['envs']['DEBUG'] == 'true'
        assert user_specified_config['envs']['PORT'] == 8080
        assert user_specified_config['run'] == 'python train.py'
        assert user_specified_config['workdir'] == '/app'
        assert user_specified_config['name'] == 'training-task'

        # Secret handling should be different
        assert default_config['secrets']['API_KEY'] == 'secret-value'
        assert user_specified_config['secrets']['API_KEY'] == '<redacted>'
        assert default_config['secrets']['DB_PASSWORD'] == 'secret-password'
        assert user_specified_config['secrets']['DB_PASSWORD'] == '<redacted>'
