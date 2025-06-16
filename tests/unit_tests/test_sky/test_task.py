import os
import tempfile

import pytest

from sky import task


def test_validate_workdir():
    curr_dir = os.getcwd()
    home_dir = os.path.expanduser('~')

    task_obj = task.Task()
    task_obj.expand_and_validate_workdir()

    task_obj = task.Task(workdir='/nonexistent/path')
    with pytest.raises(ValueError):
        task_obj.expand_and_validate_workdir()

    with tempfile.TemporaryDirectory() as d, tempfile.NamedTemporaryFile() as f:
        task_obj = task.Task(workdir=f.name)
        with pytest.raises(ValueError):
            task_obj.expand_and_validate_workdir()

        task_obj = task.Task(workdir=d)
        task_obj.expand_and_validate_workdir()

        task_obj = task.Task(workdir='~')
        task_obj.expand_and_validate_workdir()
        assert task_obj.workdir == home_dir

        task_obj = task.Task(workdir='.')
        task_obj.expand_and_validate_workdir()
        assert task_obj.workdir == curr_dir


def test_validate_file_mounts():
    curr_dir = os.getcwd()
    home_dir = os.path.expanduser('~')

    # Test empty file_mounts
    task_obj = task.Task()
    task_obj.expand_and_validate_file_mounts()
    assert task_obj.file_mounts is None

    # Test None file_mounts
    task_obj.file_mounts = None
    task_obj.expand_and_validate_file_mounts()

    with tempfile.TemporaryDirectory() as d, tempfile.NamedTemporaryFile() as f:
        # Test nonexistent local path
        task_obj = task.Task()
        task_obj.file_mounts = {'/remote': '/nonexistent/path'}
        with pytest.raises(ValueError):
            task_obj.expand_and_validate_file_mounts()

        # Test file as source
        task_obj.file_mounts = {'/remote': f.name}
        task_obj.expand_and_validate_file_mounts()

        # Test directory as source
        task_obj.file_mounts = {
            '/remote': d,
            '/remote-home': '~',
            '/remote-curr': '.'
        }
        task_obj.expand_and_validate_file_mounts()
        assert task_obj.file_mounts['/remote-home'] == home_dir
        assert task_obj.file_mounts['/remote-curr'] == curr_dir

        # Test multiple mounts
        task_obj.file_mounts = {'/remote1': f.name, '/remote2': d}
        task_obj.expand_and_validate_file_mounts()

        # Test cloud storage URLs
        task_obj.file_mounts = {
            '/remote': 's3://my-bucket/path',
            '/remote2': 'gs://another-bucket/path'
        }
        task_obj.expand_and_validate_file_mounts()


def test_to_yaml_config_without_envs():
    """Test to_yaml_config() with no environment variables."""
    task_obj = task.Task(run='echo hello')

    # Test default behavior (no redaction)
    yaml_config = task_obj.to_yaml_config()
    assert 'envs' not in yaml_config
    assert 'secrets' not in yaml_config

    # Test with redact_secrets=True (should have no effect when no secrets)
    yaml_config_redacted = task_obj.to_yaml_config(redact_secrets=True)
    assert 'envs' not in yaml_config_redacted
    assert 'secrets' not in yaml_config_redacted
    assert yaml_config == yaml_config_redacted


def test_to_yaml_config_with_envs_no_redaction():
    """Test to_yaml_config() with environment variables and no redaction."""
    envs = {
        'API_KEY': 'secret-api-key-123',
        'DATABASE_URL': 'postgresql://user:password@host:5432/db',
        'DEBUG': 'true',
        'PORT': 8080,  # Non-string value
        'EMPTY_VAR': ''
    }

    task_obj = task.Task(run='echo hello', envs=envs)

    # Test default behavior (no redaction - envs should appear as-is)
    yaml_config = task_obj.to_yaml_config()
    assert 'envs' in yaml_config
    assert yaml_config['envs'] == envs
    # Verify actual values are preserved
    assert yaml_config['envs']['API_KEY'] == 'secret-api-key-123'
    assert yaml_config['envs'][
        'DATABASE_URL'] == 'postgresql://user:password@host:5432/db'
    assert yaml_config['envs']['DEBUG'] == 'true'
    assert yaml_config['envs']['PORT'] == 8080
    assert yaml_config['envs']['EMPTY_VAR'] == ''


def test_to_yaml_config_with_secrets_redaction():
    """Test to_yaml_config() with secret variables and redaction enabled."""
    secrets = {
        'API_KEY': 'secret-api-key-123',
        'DATABASE_PASSWORD': 'postgresql://user:password@host:5432/db',
        'JWT_SECRET': 'super-secret-jwt',
        'PORT': 8080,  # Non-string value should be preserved
        'EMPTY_SECRET': '',
        'NONE_SECRET': None  # Non-string value should be preserved
    }

    task_obj = task.Task(run='echo hello', secrets=secrets)

    # Test with redaction enabled (default)
    yaml_config = task_obj.to_yaml_config(redact_secrets=True)
    assert 'secrets' in yaml_config

    # String values should be redacted
    assert yaml_config['secrets']['API_KEY'] == '<redacted>'
    assert yaml_config['secrets']['DATABASE_PASSWORD'] == '<redacted>'
    assert yaml_config['secrets']['JWT_SECRET'] == '<redacted>'
    assert yaml_config['secrets']['EMPTY_SECRET'] == '<redacted>'

    # Non-string values should be preserved
    assert yaml_config['secrets']['PORT'] == 8080
    assert yaml_config['secrets']['NONE_SECRET'] is None

    # Test with redaction disabled
    yaml_config_no_redact = task_obj.to_yaml_config(redact_secrets=False)
    assert yaml_config_no_redact['secrets'] == secrets


def test_to_yaml_config_envs_and_secrets():
    """Test that envs and secrets are handled separately."""
    envs = {
        'PUBLIC_VAR': 'public-value',
        'DEBUG': 'true'
    }
    secrets = {
        'API_KEY': 'secret-api-key-123',
        'DATABASE_PASSWORD': 'secret-password'
    }

    task_obj = task.Task(run='echo hello', envs=envs, secrets=secrets)

    # Get both configs
    config_redact_secrets = task_obj.to_yaml_config(redact_secrets=True)
    config_no_redact = task_obj.to_yaml_config(redact_secrets=False)

    # Envs should always be preserved (not redacted)
    assert config_redact_secrets['envs'] == envs
    assert config_no_redact['envs'] == envs
    assert config_redact_secrets['envs']['PUBLIC_VAR'] == 'public-value'
    assert config_redact_secrets['envs']['DEBUG'] == 'true'

    # Secrets should be redacted when redact_secrets=True
    assert config_redact_secrets['secrets']['API_KEY'] == '<redacted>'
    assert config_redact_secrets['secrets']['DATABASE_PASSWORD'] == '<redacted>'

    # Secrets should be preserved when redact_secrets=False
    assert config_no_redact['secrets'] == secrets
    assert config_no_redact['secrets']['API_KEY'] == 'secret-api-key-123'
    assert config_no_redact['secrets']['DATABASE_PASSWORD'] == 'secret-password'


def test_to_yaml_config_empty_secrets():
    """Test to_yaml_config() with empty secrets dict."""
    task_obj = task.Task(run='echo hello', secrets={})

    # Empty secrets should not appear in config due to no_empty=True
    config_redact = task_obj.to_yaml_config(redact_secrets=True)
    config_no_redact = task_obj.to_yaml_config(redact_secrets=False)

    assert 'secrets' not in config_redact
    assert 'secrets' not in config_no_redact


def test_to_yaml_config_preserves_other_fields():
    """Test that redaction doesn't affect other task fields."""
    from sky import resources

    task_obj = task.Task(run='echo hello',
                         secrets={'SECRET': 'value'},
                         workdir='/tmp/workdir',
                         name='test-task')
    # Set resources using the proper method
    task_obj.set_resources(resources.Resources(memory=4))

    config_no_redact = task_obj.to_yaml_config(redact_secrets=False)
    config_redacted = task_obj.to_yaml_config(redact_secrets=True)

    # All non-secret fields should be identical
    for key in config_no_redact:
        if key != 'secrets':
            assert config_no_redact[key] == config_redacted[key]

    # Verify specific fields are preserved
    assert config_redacted.get('run') == 'echo hello'
    assert config_redacted.get('workdir') == '/tmp/workdir'
    assert config_redacted.get('name') == 'test-task'
    assert 'resources' in config_redacted


def test_update_secrets():
    """Test the update_secrets method."""
    task_obj = task.Task(run='echo hello')
    
    # Test updating with dict
    secrets_dict = {'API_KEY': 'secret1', 'DB_PASSWORD': 'secret2'}
    task_obj.update_secrets(secrets_dict)
    assert task_obj.secrets == secrets_dict
    
    # Test updating with list of tuples
    more_secrets = [('JWT_SECRET', 'jwt-secret'), ('REDIS_PASSWORD', 'redis-pass')]
    task_obj.update_secrets(more_secrets)
    expected = {
        'API_KEY': 'secret1',
        'DB_PASSWORD': 'secret2', 
        'JWT_SECRET': 'jwt-secret',
        'REDIS_PASSWORD': 'redis-pass'
    }
    assert task_obj.secrets == expected
    
    # Test updating with None (should be no-op)
    task_obj.update_secrets(None)
    assert task_obj.secrets == expected
