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
    
    # Test with redact_envs=True (should have no effect when no envs)
    yaml_config_redacted = task_obj.to_yaml_config(redact_envs=True)
    assert 'envs' not in yaml_config_redacted
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
    
    # Test default behavior (no redaction)
    yaml_config = task_obj.to_yaml_config()
    assert 'envs' in yaml_config
    assert yaml_config['envs'] == envs
    # Verify actual values are preserved
    assert yaml_config['envs']['API_KEY'] == 'secret-api-key-123'
    assert yaml_config['envs']['DATABASE_URL'] == 'postgresql://user:password@host:5432/db'
    assert yaml_config['envs']['DEBUG'] == 'true'
    assert yaml_config['envs']['PORT'] == 8080
    assert yaml_config['envs']['EMPTY_VAR'] == ''


def test_to_yaml_config_with_envs_redaction():
    """Test to_yaml_config() with environment variables and redaction enabled."""
    envs = {
        'API_KEY': 'secret-api-key-123',
        'DATABASE_URL': 'postgresql://user:password@host:5432/db',
        'DEBUG': 'true',
        'PORT': 8080,  # Non-string value should be preserved
        'EMPTY_VAR': '',
        'NONE_VAR': None  # Non-string value should be preserved
    }
    
    task_obj = task.Task(run='echo hello', envs=envs)
    
    # Test with redaction enabled
    yaml_config = task_obj.to_yaml_config(redact_envs=True)
    assert 'envs' in yaml_config
    
    # String values should be redacted
    assert yaml_config['envs']['API_KEY'] == '<redacted>'
    assert yaml_config['envs']['DATABASE_URL'] == '<redacted>'
    assert yaml_config['envs']['DEBUG'] == '<redacted>'
    assert yaml_config['envs']['EMPTY_VAR'] == '<redacted>'
    
    # Non-string values should be preserved
    assert yaml_config['envs']['PORT'] == 8080
    assert yaml_config['envs']['NONE_VAR'] is None


def test_to_yaml_config_redaction_flag_consistency():
    """Test that redact_envs flag consistently affects only string env vars."""
    envs = {
        'STRING_VAR': 'sensitive-value',
        'INT_VAR': 42,
        'BOOL_VAR': True,
        'FLOAT_VAR': 3.14,
        'LIST_VAR': ['item1', 'item2'],
        'DICT_VAR': {'key': 'value'}
    }
    
    task_obj = task.Task(run='echo hello', envs=envs)
    
    # Get both configs
    config_no_redact = task_obj.to_yaml_config(redact_envs=False)
    config_redacted = task_obj.to_yaml_config(redact_envs=True)
    
    # Non-env fields should be identical
    for key in config_no_redact:
        if key != 'envs':
            assert config_no_redact[key] == config_redacted[key]
    
    # Only string env vars should differ
    assert config_no_redact['envs']['STRING_VAR'] == 'sensitive-value'
    assert config_redacted['envs']['STRING_VAR'] == '<redacted>'
    
    # Non-string env vars should be identical
    assert config_no_redact['envs']['INT_VAR'] == config_redacted['envs']['INT_VAR'] == 42
    assert config_no_redact['envs']['BOOL_VAR'] == config_redacted['envs']['BOOL_VAR'] is True
    assert config_no_redact['envs']['FLOAT_VAR'] == config_redacted['envs']['FLOAT_VAR'] == 3.14
    assert config_no_redact['envs']['LIST_VAR'] == config_redacted['envs']['LIST_VAR']
    assert config_no_redact['envs']['DICT_VAR'] == config_redacted['envs']['DICT_VAR']


def test_to_yaml_config_empty_envs():
    """Test to_yaml_config() with empty envs dict."""
    task_obj = task.Task(run='echo hello', envs={})
    
    # Empty envs should not appear in config due to no_empty=True
    config_no_redact = task_obj.to_yaml_config(redact_envs=False)
    config_redacted = task_obj.to_yaml_config(redact_envs=True)
    
    assert 'envs' not in config_no_redact
    assert 'envs' not in config_redacted


def test_to_yaml_config_preserves_other_fields():
    """Test that redaction doesn't affect other task fields."""
    from sky import resources
    
    task_obj = task.Task(
        run='echo hello',
        envs={'SECRET': 'value'},
        workdir='/tmp/workdir',
        name='test-task'
    )
    # Set resources using the proper method
    task_obj.set_resources(resources.Resources(memory=4))
    
    config_no_redact = task_obj.to_yaml_config(redact_envs=False)
    config_redacted = task_obj.to_yaml_config(redact_envs=True)
    
    # All non-env fields should be identical
    for key in config_no_redact:
        if key != 'envs':
            assert config_no_redact[key] == config_redacted[key]
    
    # Verify specific fields are preserved
    assert config_redacted.get('run') == 'echo hello'
    assert config_redacted.get('workdir') == '/tmp/workdir'
    assert config_redacted.get('name') == 'test-task'
    assert 'resources' in config_redacted
