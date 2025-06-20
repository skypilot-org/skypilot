"""Tests for CLI secrets functionality."""

import os
import tempfile
from unittest.mock import patch

import pytest

from sky.client.cli import command
from sky.client.cli import flags


def test_parse_secret_var_with_equals():
    """Test parsing secret vars with KEY=VALUE format."""
    result = flags._parse_secret_var('API_KEY=secret123')
    assert result == ('API_KEY', 'secret123')

    result = flags._parse_secret_var(
        'DATABASE_URL=postgresql://user:pass@host:5432/db')
    assert result == ('DATABASE_URL', 'postgresql://user:pass@host:5432/db')

    # Test with multiple equals signs (only split on first one)
    result = flags._parse_secret_var('JWT_SECRET=secret=with=equals')
    assert result == ('JWT_SECRET', 'secret=with=equals')

    # Test with empty value
    result = flags._parse_secret_var('EMPTY_SECRET=')
    assert result == ('EMPTY_SECRET', '')


def test_parse_secret_var_from_environment():
    """Test parsing secret vars that reference local environment."""
    # Test with environment variable that exists
    with patch.dict(os.environ, {'TEST_SECRET': 'env_secret_value'}):
        result = flags._parse_secret_var('TEST_SECRET')
        assert result == ('TEST_SECRET', 'env_secret_value')

    # Test with environment variable that doesn't exist
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(Exception,
                           match='TEST_SECRET is not set in local environment'):
            flags._parse_secret_var('TEST_SECRET')


def test_parse_secret_var_error_cases():
    """Test error cases for secret variable parsing."""
    # Test with no equals and no environment variable
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(Exception,
                           match='NONEXISTENT is not set in local environment'):
            flags._parse_secret_var('NONEXISTENT')


def test_make_task_with_secrets_override():
    """Test _make_task_or_dag_from_entrypoint_with_overrides with secrets."""
    # Create a simple task YAML file for testing
    task_yaml_content = """
run: echo hello
envs:
  DEBUG: true
  LOG_LEVEL: info
secrets:
  API_KEY: yaml_secret
  DB_PASSWORD: yaml_password
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        f.write(task_yaml_content)
        yaml_file = f.name

    try:
        # Test with secrets override
        secrets = [('API_KEY', 'overridden_secret'),
                   ('NEW_SECRET', 'new_value')]
        envs = [('PUBLIC_VAR', 'public_value')]

        result = command._make_task_or_dag_from_entrypoint_with_overrides(
            entrypoint=(yaml_file,), env=envs, secret=secrets)

        # Should be a Task (single task in YAML)
        assert hasattr(result, 'secrets')
        assert hasattr(result, 'envs')

        # Check that secrets were overridden/added
        task_secrets = result.secrets
        assert task_secrets['API_KEY'] == 'overridden_secret'  # Overridden
        assert task_secrets['NEW_SECRET'] == 'new_value'  # Added
        assert task_secrets['DB_PASSWORD'] == 'yaml_password'  # From YAML

        # Check that envs were added
        task_envs = result.envs
        assert task_envs['PUBLIC_VAR'] == 'public_value'  # Added
        assert task_envs['DEBUG'] == 'True'  # From YAML (converted to string)
        assert task_envs['LOG_LEVEL'] == 'info'  # From YAML

    finally:
        os.unlink(yaml_file)


def test_make_task_with_empty_secrets():
    """Test task creation with empty secrets list."""
    result = command._make_task_or_dag_from_entrypoint_with_overrides(
        entrypoint=('echo hello',), env=[], secret=[])

    assert hasattr(result, 'secrets')
    assert result.secrets == {}


def test_make_task_with_none_secrets():
    """Test task creation with None secrets."""
    result = command._make_task_or_dag_from_entrypoint_with_overrides(
        entrypoint=('echo hello',), env=[], secret=None)

    assert hasattr(result, 'secrets')
    assert result.secrets == {}


def test_secrets_in_command_line_task():
    """Test secrets handling for command line tasks (no YAML)."""
    secrets = [('API_KEY', 'secret123'), ('DB_PASSWORD', 'password456')]
    envs = [('DEBUG', 'true')]

    result = command._make_task_or_dag_from_entrypoint_with_overrides(
        entrypoint=('python', 'train.py'), env=envs, secret=secrets)

    # Check task was created correctly
    assert result.run == 'python train.py'
    assert result.secrets == {
        'API_KEY': 'secret123',
        'DB_PASSWORD': 'password456'
    }
    assert result.envs == {'DEBUG': 'true'}


def test_secrets_integration_with_resources():
    """Test that secrets work properly with resource settings."""
    secrets = [('DOCKER_PASSWORD', 'docker_secret')]

    result = command._make_task_or_dag_from_entrypoint_with_overrides(
        entrypoint=('echo hello',), secret=secrets, gpus='V100:1')

    # Check that secrets are set
    assert result.secrets == {'DOCKER_PASSWORD': 'docker_secret'}

    # Check that resources are set
    assert len(result.resources) > 0


def test_secrets_override_priority():
    """Test that CLI secrets override YAML secrets correctly."""
    task_yaml_content = """
run: echo hello
secrets:
  API_KEY: yaml_value
  YAML_ONLY: yaml_secret
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        f.write(task_yaml_content)
        yaml_file = f.name

    try:
        # CLI secrets should override YAML secrets
        secrets = [('API_KEY', 'cli_value'), ('CLI_ONLY', 'cli_secret')]

        result = command._make_task_or_dag_from_entrypoint_with_overrides(
            entrypoint=(yaml_file,), secret=secrets)

        task_secrets = result.secrets
        assert task_secrets['API_KEY'] == 'cli_value'  # CLI overrides YAML
        assert task_secrets['CLI_ONLY'] == 'cli_secret'  # CLI adds new
        assert task_secrets['YAML_ONLY'] == 'yaml_secret'  # YAML preserved

    finally:
        os.unlink(yaml_file)


def test_env_and_secrets_separate_handling():
    """Test that envs and secrets are handled separately in CLI."""
    task_yaml_content = """
run: echo hello
envs:
  SHARED_VAR: env_value
secrets:
  SHARED_VAR: secret_value
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        f.write(task_yaml_content)
        yaml_file = f.name

    try:
        # Both envs and secrets can have same key name
        envs = [('SHARED_VAR', 'cli_env_value'), ('ENV_ONLY', 'env_val')]
        secrets = [('SHARED_VAR', 'cli_secret_value'),
                   ('SECRET_ONLY', 'secret_val')]

        result = command._make_task_or_dag_from_entrypoint_with_overrides(
            entrypoint=(yaml_file,), env=envs, secret=secrets)

        # Check both dictionaries separately
        assert result.envs['SHARED_VAR'] == 'cli_env_value'
        assert result.envs['ENV_ONLY'] == 'env_val'

        assert result.secrets['SHARED_VAR'] == 'cli_secret_value'
        assert result.secrets['SECRET_ONLY'] == 'secret_val'

        # Check combined behavior (secrets should override envs)
        combined = result.envs_and_secrets
        assert combined['SHARED_VAR'] == 'cli_secret_value'

    finally:
        os.unlink(yaml_file)


def test_null_secrets_override_with_cli():
    """Test that null secrets in YAML can be overridden with CLI --secret."""
    import os
    import tempfile

    import yaml

    from sky.client.cli.command import (
        _make_task_or_dag_from_entrypoint_with_overrides)

    # Create YAML with null secrets
    yaml_content = {
        'name': 'test-null-secrets',
        'run': 'echo "Testing null secrets"',
        'envs': {
            'PUBLIC_VAR': 'public-value'
        },
        'secrets': {
            'API_KEY': None,  # null value
            'SECRET_TOKEN': None  # another null value
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        yaml.dump(yaml_content, f)
        yaml_file = f.name

    try:
        # Test: CLI override of null secrets
        cli_secrets = [('API_KEY', 'cli-api-key'),
                       ('SECRET_TOKEN', 'cli-token')]

        task = _make_task_or_dag_from_entrypoint_with_overrides(
            entrypoint=(yaml_file,), secret=cli_secrets)

        expected_secrets = {
            'API_KEY': 'cli-api-key',
            'SECRET_TOKEN': 'cli-token'
        }
        assert task.secrets == expected_secrets

        # Environment variables should be preserved
        assert task.envs == {'PUBLIC_VAR': 'public-value'}

    finally:
        os.unlink(yaml_file)


def test_null_secrets_without_cli_override_fails():
    """Test that null secrets without CLI override fail appropriately."""
    import os
    import tempfile

    import pytest
    import yaml

    from sky.client.cli.command import (
        _make_task_or_dag_from_entrypoint_with_overrides)

    # Create YAML with null secrets
    yaml_content = {
        'name': 'test-null-fail',
        'run': 'echo "Should fail"',
        'secrets': {
            'API_KEY': None
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        yaml.dump(yaml_content, f)
        yaml_file = f.name

    try:
        # Should fail without CLI override
        with pytest.raises(ValueError,
                           match="Secret variable 'API_KEY' is None"):
            _make_task_or_dag_from_entrypoint_with_overrides(
                entrypoint=(yaml_file,), secret=None)
    finally:
        os.unlink(yaml_file)


def test_mixed_null_and_non_null_secrets():
    """Test mixing null and non-null secrets with CLI override."""
    import os
    import tempfile

    import yaml

    from sky.client.cli.command import (
        _make_task_or_dag_from_entrypoint_with_overrides)

    # Create YAML with mixed secrets
    yaml_content = {
        'name': 'test-mixed-secrets',
        'run': 'echo "Testing mixed"',
        'secrets': {
            'YAML_SECRET': 'yaml-value',  # Non-null value
            'CLI_SECRET': None  # Null value to override
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        yaml.dump(yaml_content, f)
        yaml_file = f.name

    try:
        # CLI override only the null secret
        cli_secrets = [('CLI_SECRET', 'cli-override-value')]

        task = _make_task_or_dag_from_entrypoint_with_overrides(
            entrypoint=(yaml_file,), secret=cli_secrets)

        expected_secrets = {
            'YAML_SECRET': 'yaml-value',
            'CLI_SECRET': 'cli-override-value'
        }
        assert task.secrets == expected_secrets

    finally:
        os.unlink(yaml_file)


def test_cli_override_non_null_secrets():
    """Test that CLI can override non-null secrets too."""
    import os
    import tempfile

    import yaml

    from sky.client.cli.command import (
        _make_task_or_dag_from_entrypoint_with_overrides)

    # Create YAML with non-null secrets
    yaml_content = {
        'name': 'test-override-non-null',
        'run': 'echo "Testing override"',
        'secrets': {
            'EXISTING_SECRET': 'original-value'
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        yaml.dump(yaml_content, f)
        yaml_file = f.name

    try:
        # CLI override the non-null secret
        cli_secrets = [('EXISTING_SECRET', 'overridden-value')]

        task = _make_task_or_dag_from_entrypoint_with_overrides(
            entrypoint=(yaml_file,), secret=cli_secrets)

        expected_secrets = {'EXISTING_SECRET': 'overridden-value'}
        assert task.secrets == expected_secrets

    finally:
        os.unlink(yaml_file)
