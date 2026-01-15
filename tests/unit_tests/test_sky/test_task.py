import os
import tempfile
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import patch

from pydantic import SecretStr
import pytest

from sky import exceptions
from sky import resources as resources_lib
from sky import task
from sky.utils import git
from sky.utils import registry


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

    # Test with use_user_specified_yaml=True (should have no effect when no secrets)
    yaml_config_redacted = task_obj.to_yaml_config(use_user_specified_yaml=True)
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
        'PORT': 8080,  # Non-string value will also be redacted
        'EMPTY_SECRET': '',
        'NONE_SECRET': None  # Non-string value will also be redacted
    }

    task_obj = task.Task(run='echo hello', secrets=secrets)

    # Test with redaction enabled (default)
    yaml_config = task_obj.to_yaml_config(use_user_specified_yaml=True)
    assert 'secrets' in yaml_config

    # String values should be redacted
    assert yaml_config['secrets']['API_KEY'] == '<redacted>'
    assert yaml_config['secrets']['DATABASE_PASSWORD'] == '<redacted>'
    assert yaml_config['secrets']['JWT_SECRET'] == '<redacted>'
    assert yaml_config['secrets']['EMPTY_SECRET'] == '<redacted>'

    # All values should be redacted (including non-string values)
    assert yaml_config['secrets']['PORT'] == '<redacted>'
    assert yaml_config['secrets']['NONE_SECRET'] == '<redacted>'

    # Test with redaction disabled
    yaml_config_no_redact = task_obj.to_yaml_config(
        use_user_specified_yaml=False)
    assert yaml_config_no_redact['secrets'] == secrets


def test_to_yaml_config_envs_and_secrets():
    """Test that envs and secrets are handled separately."""
    envs = {'PUBLIC_VAR': 'public-value', 'DEBUG': 'true'}
    secrets = {
        'API_KEY': 'secret-api-key-123',
        'DATABASE_PASSWORD': 'secret-password'
    }

    task_obj = task.Task(run='echo hello', envs=envs, secrets=secrets)

    # Get both configs
    config_redact_secrets = task_obj.to_yaml_config(
        use_user_specified_yaml=True)
    config_no_redact = task_obj.to_yaml_config(use_user_specified_yaml=False)

    # Envs should always be preserved (not redacted)
    assert config_redact_secrets['envs'] == envs
    assert config_no_redact['envs'] == envs
    assert config_redact_secrets['envs']['PUBLIC_VAR'] == 'public-value'
    assert config_redact_secrets['envs']['DEBUG'] == 'true'

    # Secrets should be redacted when use_user_specified_yaml=True
    assert config_redact_secrets['secrets']['API_KEY'] == '<redacted>'
    assert config_redact_secrets['secrets']['DATABASE_PASSWORD'] == '<redacted>'

    # Secrets should be preserved when use_user_specified_yaml=False
    assert config_no_redact['secrets'] == secrets
    assert config_no_redact['secrets']['API_KEY'] == 'secret-api-key-123'
    assert config_no_redact['secrets']['DATABASE_PASSWORD'] == 'secret-password'


def test_to_yaml_config_empty_secrets():
    """Test to_yaml_config() with empty secrets dict."""
    task_obj = task.Task(run='echo hello', secrets={})

    # Empty secrets should not appear in config due to no_empty=True
    config_redact = task_obj.to_yaml_config(use_user_specified_yaml=True)
    config_no_redact = task_obj.to_yaml_config(use_user_specified_yaml=False)

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

    config_no_redact = task_obj.to_yaml_config(use_user_specified_yaml=False)
    config_redacted = task_obj.to_yaml_config(use_user_specified_yaml=True)

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
    assert task.get_plaintext_secrets(task_obj.secrets) == secrets_dict

    # Test updating with list of tuples
    more_secrets = [('JWT_SECRET', 'jwt-secret'),
                    ('REDIS_PASSWORD', 'redis-pass')]
    task_obj.update_secrets(more_secrets)
    expected = {
        'API_KEY': 'secret1',
        'DB_PASSWORD': 'secret2',
        'JWT_SECRET': 'jwt-secret',
        'REDIS_PASSWORD': 'redis-pass'
    }
    assert task.get_plaintext_secrets(task_obj.secrets) == expected

    # Test updating with None (should be no-op)
    task_obj.update_secrets(None)
    assert task.get_plaintext_secrets(task_obj.secrets) == expected


def test_update_secrets_error_handling():
    """Test error handling in update_secrets method."""
    task_obj = task.Task(run='echo hello')

    # Test duplicate keys in list input
    with pytest.raises(ValueError, match='Duplicate secret keys provided'):
        task_obj.update_secrets([('API_KEY', 'secret1'),
                                 ('API_KEY', 'secret2')])

    # Test invalid key type
    with pytest.raises(ValueError, match='Secret keys must be strings'):
        task_obj.update_secrets({123: 'secret'})

    # Test invalid environment variable name
    with pytest.raises(ValueError, match='Invalid secret key'):
        task_obj.update_secrets({'invalid-name': 'secret'})

    # Test invalid input type
    with pytest.raises(ValueError, match='secrets must be List'):
        task_obj.update_secrets("invalid_input")


def test_secrets_property():
    """Test the secrets property."""
    # Test empty secrets
    task_obj = task.Task(run='echo hello')
    assert task_obj.secrets == {}

    # Test with initial secrets
    initial_secrets = {'API_KEY': 'secret1', 'DB_PASSWORD': 'secret2'}
    task_obj = task.Task(run='echo hello', secrets=initial_secrets)
    assert task.get_plaintext_secrets(task_obj.secrets) == initial_secrets


def test_envs_and_secrets_property():
    """Test the envs_and_secrets property that combines both."""
    envs = {'PUBLIC_VAR': 'public-value', 'DEBUG': 'true'}
    secrets = {'API_KEY': 'secret-api-key', 'DB_PASSWORD': 'secret-password'}

    task_obj = task.Task(run='echo hello', envs=envs, secrets=secrets)
    combined = task_obj.envs_and_secrets
    combined = task.get_plaintext_envs_and_secrets(combined)

    # Should contain all environment variables
    assert combined['PUBLIC_VAR'] == 'public-value'
    assert combined['DEBUG'] == 'true'

    # Should contain all secrets
    assert combined['API_KEY'] == 'secret-api-key'
    assert combined['DB_PASSWORD'] == 'secret-password'

    # Total count should be envs + secrets
    assert len(combined) == len(envs) + len(secrets)


def test_secrets_override_envs_in_combined():
    """Test that secrets override envs with same name in envs_and_secrets."""
    envs = {'SHARED_VAR': 'env_value', 'ENV_ONLY': 'env_only'}
    secrets = {'SHARED_VAR': 'secret_value', 'SECRET_ONLY': 'secret_only'}

    task_obj = task.Task(run='echo hello', envs=envs, secrets=secrets)
    combined = task_obj.envs_and_secrets
    combined = task.get_plaintext_envs_and_secrets(combined)

    # Secret should override env for shared variable name
    assert combined['SHARED_VAR'] == 'secret_value'

    # Unique variables should be preserved
    assert combined['ENV_ONLY'] == 'env_only'
    assert combined['SECRET_ONLY'] == 'secret_only'


def test_from_yaml_config_with_secrets():
    """Test parsing secrets from YAML configuration."""
    config = {
        'run': 'echo hello',
        'envs': {
            'DEBUG': 'true',
            'PORT': '8080'
        },
        'secrets': {
            'API_KEY': 'secret-key-123',
            'DATABASE_PASSWORD': 'secret-password'
        }
    }

    task_obj = task.Task.from_yaml_config(config)

    # Check that secrets are parsed correctly
    assert task.get_plaintext_secrets(task_obj.secrets) == {
        'API_KEY': 'secret-key-123',
        'DATABASE_PASSWORD': 'secret-password'
    }

    # Check that envs are still parsed correctly
    assert task_obj.envs == {'DEBUG': 'true', 'PORT': '8080'}

    # Check other fields
    assert task_obj.run == 'echo hello'


def test_from_yaml_config_secrets_type_conversion():
    """Test that secrets keys and values are converted to strings."""
    config = {
        'run': 'echo hello',
        'secrets': {
            'NUMERIC_KEY': 456,  # Numeric value
            'BOOL_KEY': True,  # Boolean value
            'STRING_KEY': 'regular-string',  # String value
            'EMPTY_KEY': ''  # Empty string value
        }
    }

    task_obj = task.Task.from_yaml_config(config)

    # Non-None values should be converted to strings
    assert task.get_plaintext_secrets(task_obj.secrets)['NUMERIC_KEY'] == '456'
    assert task.get_plaintext_secrets(task_obj.secrets)['BOOL_KEY'] == 'True'
    assert task.get_plaintext_secrets(
        task_obj.secrets)['STRING_KEY'] == 'regular-string'
    assert task.get_plaintext_secrets(task_obj.secrets)['EMPTY_KEY'] == ''


def test_from_yaml_config_secrets_validation():
    """Test validation of secrets during YAML parsing."""
    # Test None secret value
    config = {'run': 'echo hello', 'secrets': {'API_KEY': None}}

    with pytest.raises(ValueError, match='Secret variable.*is None'):
        task.Task.from_yaml_config(config)


def test_task_initialization_with_secrets():
    """Test Task initialization with secrets parameter."""
    secrets = {'API_KEY': 'secret123', 'DB_PASSWORD': 'password456'}
    task_obj = task.Task(run='echo hello', secrets=secrets)

    assert task.get_plaintext_secrets(task_obj.secrets) == secrets
    assert task_obj.run == 'echo hello'


def test_secrets_in_task_repr():
    """Test that secrets don't appear in string representation."""
    envs = {'DEBUG': 'true'}
    secrets = {'API_KEY': 'secret123'}
    task_obj = task.Task(run='echo hello', envs=envs, secrets=secrets)

    repr_str = repr(task_obj)

    # Envs might be shown in repr, but secrets should never be
    assert 'secret123' not in repr_str
    assert 'API_KEY' not in repr_str or '<redacted>' in repr_str


def test_secrets_empty_initialization():
    """Test initialization with empty secrets dict."""
    task_obj = task.Task(run='echo hello', secrets={})
    assert task_obj.secrets == {}


def test_secrets_with_none_initialization():
    """Test initialization with None secrets."""
    task_obj = task.Task(run='echo hello', secrets=None)
    assert task_obj.secrets == {}


def test_from_yaml_config_null_secrets_with_override():
    """Test that null secrets in YAML can be overridden with secrets_overrides."""
    config = {
        'name': 'test-null-secrets',
        'run': 'echo hello',
        'envs': {
            'PUBLIC_VAR': 'public-value'
        },
        'secrets': {
            'API_KEY': None,  # null value
            'TOKEN': None  # another null value
        }
    }

    # Test with secrets overrides
    secrets_overrides = [('API_KEY', 'overridden-api-key'),
                         ('TOKEN', 'overridden-token')]

    task_obj = task.Task.from_yaml_config(config,
                                          secrets_overrides=secrets_overrides)

    expected_secrets = {
        'API_KEY': 'overridden-api-key',
        'TOKEN': 'overridden-token'
    }
    assert task.get_plaintext_secrets(task_obj.secrets) == expected_secrets

    # Environment variables should be preserved
    assert task_obj.envs == {'PUBLIC_VAR': 'public-value'}


def test_from_yaml_config_null_secrets_without_override_fails():
    """Test that null secrets without override fail appropriately."""
    config = {
        'name': 'test-null-fail',
        'run': 'echo hello',
        'secrets': {
            'API_KEY': None
        }
    }

    # Should fail without override
    with pytest.raises(ValueError, match="Secret variable 'API_KEY' is None"):
        task.Task.from_yaml_config(config)


def test_from_yaml_config_partial_null_secrets_override():
    """Test partial override of null secrets while preserving non-null ones."""
    config = {
        'name': 'test-partial-null',
        'run': 'echo hello',
        'secrets': {
            'YAML_SECRET': 'yaml-value',  # Non-null, should be preserved
            'NULL_SECRET': None  # Null, should be overridden
        }
    }

    secrets_overrides = [('NULL_SECRET', 'cli-override')]

    task_obj = task.Task.from_yaml_config(config,
                                          secrets_overrides=secrets_overrides)

    expected_secrets = {
        'YAML_SECRET': 'yaml-value',
        'NULL_SECRET': 'cli-override'
    }
    assert task.get_plaintext_secrets(task_obj.secrets) == expected_secrets


def test_from_yaml_config_override_non_null_secrets():
    """Test that CLI override can override non-null secrets too."""
    config = {
        'name': 'test-override-non-null',
        'run': 'echo hello',
        'secrets': {
            'EXISTING_SECRET': 'original-value'
        }
    }

    secrets_overrides = [('EXISTING_SECRET', 'overridden-value')]

    task_obj = task.Task.from_yaml_config(config,
                                          secrets_overrides=secrets_overrides)

    expected_secrets = {'EXISTING_SECRET': 'overridden-value'}
    assert task.get_plaintext_secrets(task_obj.secrets) == expected_secrets


def test_from_yaml_config_env_and_secrets_overrides_independent():
    """Test that env and secrets overrides work independently."""
    config = {
        'name': 'test-independent',
        'run': 'echo hello',
        'envs': {
            'ENV_VAR': None  # null env var
        },
        'secrets': {
            'SECRET_VAR': None  # null secret var
        }
    }

    env_overrides = [('ENV_VAR', 'env-override')]
    secrets_overrides = [('SECRET_VAR', 'secret-override')]

    task_obj = task.Task.from_yaml_config(config,
                                          env_overrides=env_overrides,
                                          secrets_overrides=secrets_overrides)

    assert task_obj.envs == {'ENV_VAR': 'env-override'}
    assert task.get_plaintext_secrets(task_obj.secrets) == {
        'SECRET_VAR': 'secret-override'
    }

    # Combined should have both
    combined = task_obj.envs_and_secrets
    combined = task.get_plaintext_envs_and_secrets(combined)
    expected_combined = {
        'ENV_VAR': 'env-override',
        'SECRET_VAR': 'secret-override'
    }
    assert combined == expected_combined


def test_docker_login_config_all_in_envs_or_secrets():
    """Test Docker login config when all variables are in envs OR all in secrets."""

    # Test 1: All in envs (should work)
    task_obj1 = task.Task(name='test-docker-all-envs',
                          run='echo hello',
                          envs={
                              'SKYPILOT_DOCKER_USERNAME': 'myuser',
                              'SKYPILOT_DOCKER_SERVER': 'registry.example.com',
                              'SKYPILOT_DOCKER_PASSWORD': 'password'
                          })

    # Verify Docker config validation passes
    resources = resources_lib.Resources(image_id='docker:nginx:latest')
    task_obj1.set_resources(resources)  # Should not raise an error

    # Test 2: All in secrets (should work)
    task_obj2 = task.Task(name='test-docker-all-secrets',
                          run='echo hello',
                          secrets={
                              'SKYPILOT_DOCKER_USERNAME': 'secretuser',
                              'SKYPILOT_DOCKER_SERVER': 'secret.registry.com',
                              'SKYPILOT_DOCKER_PASSWORD': 'secret-password'
                          })

    task_obj2.set_resources(
        resources_lib.Resources(image_id='docker:ubuntu:latest'))

    # Test 3: Split across envs and secrets should fail
    with pytest.raises(
            ValueError,
            match='Docker login variables must be specified together'):
        task_obj3 = task.Task(
            name='test-docker-split',
            run='echo hello',
            envs={
                'SKYPILOT_DOCKER_USERNAME': 'user',
                'SKYPILOT_DOCKER_SERVER': 'registry.com'
            },
            secrets={'SKYPILOT_DOCKER_PASSWORD': 'secret-password'})
        task_obj3.set_resources(resources_lib.Resources())

    # Test 4: Missing variables in envs should fail
    with pytest.raises(
            ValueError,
            match='Docker login variables must be specified together'):
        task_obj4 = task.Task(
            name='test-docker-missing-envs',
            run='echo hello',
            envs={
                'SKYPILOT_DOCKER_USERNAME': 'user',
                'SKYPILOT_DOCKER_SERVER': 'registry.com'
                # Missing SKYPILOT_DOCKER_PASSWORD
            })
        task_obj4.set_resources(resources_lib.Resources())

    # Test 5: Missing variables in secrets should fail
    with pytest.raises(
            ValueError,
            match='Docker login variables must be specified together'):
        task_obj5 = task.Task(
            name='test-docker-missing-secrets',
            run='echo hello',
            secrets={
                'SKYPILOT_DOCKER_USERNAME': 'user',
                # Missing SKYPILOT_DOCKER_PASSWORD and SKYPILOT_DOCKER_SERVER
            })
        task_obj5.set_resources(resources_lib.Resources())


def test_docker_login_config_update_methods():
    """Test Docker login config validation when using update_envs and update_secrets."""
    # Test 1: Add complete Docker config to envs all at once - should work
    task_obj = task.Task(name='test-docker-update-envs', run='echo hello')

    # Set resources first (no Docker config yet)
    task_obj.set_resources(resources_lib.Resources())

    # Add all Docker vars to envs at once - should work
    task_obj.update_envs({
        'SKYPILOT_DOCKER_USERNAME': 'user',
        'SKYPILOT_DOCKER_SERVER': 'registry.com',
        'SKYPILOT_DOCKER_PASSWORD': 'password'
    })

    # Verify all variables are present
    combined = task_obj.envs_and_secrets
    assert 'SKYPILOT_DOCKER_USERNAME' in combined
    assert 'SKYPILOT_DOCKER_SERVER' in combined
    assert 'SKYPILOT_DOCKER_PASSWORD' in combined

    # Test 2: Add complete Docker config to secrets all at once - should work
    task_obj2 = task.Task(name='test-docker-update-secrets', run='echo hello')
    task_obj2.set_resources(resources_lib.Resources())

    # Add all Docker vars to secrets at once - should work
    task_obj2.update_secrets({
        'SKYPILOT_DOCKER_USERNAME': 'secretuser',
        'SKYPILOT_DOCKER_SERVER': 'secret.registry.com',
        'SKYPILOT_DOCKER_PASSWORD': 'secretpassword'
    })

    # Test 3: Updating incomplete Docker config should fail
    task_obj3 = task.Task(name='test-incomplete', run='echo hello')
    task_obj3.set_resources(resources_lib.Resources())

    # Add only some Docker vars - this should fail
    with pytest.raises(
            ValueError,
            match='Docker login variables must be specified together'):
        task_obj3.update_envs({
            'SKYPILOT_DOCKER_USERNAME': 'user',
            'SKYPILOT_DOCKER_SERVER': 'registry.com'
            # Missing SKYPILOT_DOCKER_PASSWORD
        })


def test_docker_login_config_no_mixed_envs_secrets():
    """Test that Docker variables cannot be mixed between envs and secrets."""
    # This should fail because Docker variables are split between envs and secrets
    with pytest.raises(
            ValueError,
            match='Docker login variables must be specified together'):
        task_obj = task.Task(
            name='test-docker-mixed',
            run='echo hello',
            envs={
                'SKYPILOT_DOCKER_USERNAME': 'env-user',
                'SKYPILOT_DOCKER_SERVER': 'env-registry.com'
            },
            secrets={'SKYPILOT_DOCKER_PASSWORD': 'secret-password'})
        task_obj.set_resources(
            resources_lib.Resources(image_id='docker:ubuntu:latest'))


def make_mock_volume_config(name='vol1',
                            type='pvc',
                            cloud='aws',
                            region='us-west1',
                            zone='a',
                            name_on_cloud=None,
                            size='1',
                            config={}):
    from sky import models
    if name_on_cloud is None:
        name_on_cloud = name
    return models.VolumeConfig(name=name,
                               type=type,
                               cloud=cloud,
                               region=region,
                               zone=zone,
                               name_on_cloud=name_on_cloud,
                               size=size,
                               config=config)


def make_mock_resource(cloud=None, region=None, zone=None):

    class MockResource:

        def __init__(self, cloud, region, zone):
            self.cloud = cloud
            self.region = region
            self.zone = zone
            self.priority = 0

        def copy(self, **override):
            # Return a new instance with overridden attributes
            new = make_mock_resource(
                override.get('cloud', self.cloud),
                override.get('region', self.region),
                override.get('zone', self.zone),
            )
            return new

    return MockResource(cloud, region, zone)


def test_resolve_volumes_no_volumes():
    t = task.Task()
    t._volumes = None
    t.volume_mounts = None
    # Should not raise
    t.resolve_and_validate_volumes()
    t._volumes = {}
    t.resolve_and_validate_volumes()


def test_resolve_volumes_already_resolved():
    t = task.Task()
    t.volume_mounts = [object()]
    t._volumes = {'/mnt': 'vol1'}
    # Should not raise or do anything
    t.resolve_and_validate_volumes()


def test_resolve_volumes_single_success():
    t = task.Task()
    t._volumes = {'/mnt': 'vol1'}
    t.resources = [make_mock_resource()]
    with mock.patch('sky.global_user_state.get_volume_by_name') as get_vol:
        get_vol.return_value = {
            'handle': make_mock_volume_config(name='vol1', cloud='aws')
        }
        t.resolve_and_validate_volumes()
        # Should override resource topology
        for r in t.resources:
            assert r.cloud == registry.CLOUD_REGISTRY.from_str('aws')
            assert r.region == 'us-west1'
            assert r.zone == 'a'


def test_resolve_volumes_volume_not_found():
    t = task.Task()
    t._volumes = {'/mnt': 'vol1'}
    t.resources = [make_mock_resource()]
    with mock.patch('sky.global_user_state.get_volume_by_name') as get_vol:
        get_vol.return_value = None
        with pytest.raises(exceptions.VolumeNotFoundError):
            t.resolve_and_validate_volumes()


def test_resolve_volumes_dict_volume_success():
    t = task.Task()
    t._volumes = {'/mnt': {'name': 'vol1'}}
    t.resources = [make_mock_resource()]
    with mock.patch('sky.global_user_state.get_volume_by_name') as get_vol, \
         mock.patch('sky.utils.common_utils.validate_schema') as v_schema:
        v_schema.return_value = None
        get_vol.return_value = {
            'handle': make_mock_volume_config(name='vol1', cloud='aws')
        }
        t.resolve_and_validate_volumes()
        for r in t.resources:
            assert r.cloud == registry.CLOUD_REGISTRY.from_str('aws')


def test_resolve_volumes_topology_conflict_between_volumes():
    t = task.Task()
    t._volumes = {'/mnt1': 'vol1', '/mnt2': 'vol2'}
    t.resources = [make_mock_resource()]

    def get_vol_by_name(name):
        if name == 'vol1':
            return {'handle': make_mock_volume_config(name='vol1', cloud='aws')}
        elif name == 'vol2':
            return {'handle': make_mock_volume_config(name='vol2', cloud='gcp')}

    with mock.patch('sky.global_user_state.get_volume_by_name',
                    side_effect=get_vol_by_name):
        # vol1: cloud=aws, vol2: cloud=gcp
        with pytest.raises(exceptions.VolumeTopologyConflictError):
            t.resolve_and_validate_volumes()


def test_resolve_volumes_topology_conflict_with_resources():
    t = task.Task()
    t._volumes = {'/mnt': 'vol1'}
    # Resource requires cloud=gcp, volume requires cloud=aws
    t.resources = [make_mock_resource(cloud='gcp')]
    with mock.patch('sky.global_user_state.get_volume_by_name') as get_vol:
        get_vol.return_value = {
            'handle': make_mock_volume_config(name='vol1', cloud='aws')
        }
        with pytest.raises(exceptions.VolumeTopologyConflictError):
            t.resolve_and_validate_volumes()


def test_resolve_volumes_override_topology():
    t = task.Task()
    t._volumes = {'/mnt': 'vol1'}
    # Resource has no cloud/region/zone set, should be overridden
    t.resources = [make_mock_resource()]
    with mock.patch('sky.global_user_state.get_volume_by_name') as get_vol:
        get_vol.return_value = {
            'handle': make_mock_volume_config(name='vol1', cloud='aws')
        }
        t.resolve_and_validate_volumes()
        for r in t.resources:
            assert r.cloud == registry.CLOUD_REGISTRY.from_str('aws')
            assert r.region == 'us-west1'
            assert r.zone == 'a'


def test_resolve_volumes_ephemeral_volume_success():
    """Test resolving ephemeral volume with 'size' field."""
    t = task.Task()
    t._volumes = {'/mnt': {'size': 10}}
    t.resources = [make_mock_resource()]

    # Mock VolumeMount.resolve_ephemeral_config to return a mock volume mount
    mock_volume_mount = mock.MagicMock()
    mock_volume_mount.is_ephemeral = True
    mock_volume_mount.volume_name = 'ephemeral-vol'
    mock_volume_mount.path = '/mnt'

    with mock.patch('sky.utils.volume.VolumeMount.resolve_ephemeral_config',
                    return_value=mock_volume_mount) as mock_resolve:
        t.resolve_and_validate_volumes()
        # Verify resolve_ephemeral_config was called with correct parameters
        mock_resolve.assert_called_once_with('/mnt', {'size': 10})
        # Verify volume_mounts was populated
        assert t.volume_mounts is not None
        assert len(t.volume_mounts) == 1
        assert t.volume_mounts[0] == mock_volume_mount


def test_resolve_volumes_ephemeral_volume_with_multiple_fields():
    """Test resolving ephemeral volume with 'size' and other fields."""
    t = task.Task()
    vol_config = {'size': 20, 'type': 'pd-standard', 'labels': {'env': 'test'}}
    t._volumes = {'/data': vol_config}
    t.resources = [make_mock_resource()]

    mock_volume_mount = mock.MagicMock()
    mock_volume_mount.is_ephemeral = True

    with mock.patch('sky.utils.volume.VolumeMount.resolve_ephemeral_config',
                    return_value=mock_volume_mount) as mock_resolve:
        t.resolve_and_validate_volumes()
        mock_resolve.assert_called_once_with('/data', vol_config)


def test_resolve_volumes_dict_with_name_and_size():
    """Test dict with both 'size' and 'name' prioritizes 'size' (ephemeral)."""
    t = task.Task()
    # When both 'size' and 'name' exist, 'size' takes precedence (ephemeral)
    t._volumes = {'/mnt': {'size': 10, 'name': 'vol1'}}
    t.resources = [make_mock_resource()]

    mock_volume_mount = mock.MagicMock()
    mock_volume_mount.is_ephemeral = True

    with mock.patch('sky.utils.volume.VolumeMount.resolve_ephemeral_config',
                    return_value=mock_volume_mount) as mock_ephemeral, \
         mock.patch('sky.utils.volume.VolumeMount.resolve') as mock_resolve:
        t.resolve_and_validate_volumes()
        # Verify ephemeral config is called (size takes precedence)
        mock_ephemeral.assert_called_once()
        # Verify resolve is NOT called
        mock_resolve.assert_not_called()


def test_resolve_volumes_invalid_dict_no_size_no_name():
    """Test dict without 'size' or 'name' raises ValueError."""
    t = task.Task()
    t._volumes = {'/mnt': {'type': 'pd-standard'}}
    t.resources = [make_mock_resource()]

    with pytest.raises(ValueError,
                       match='Invalid volume config.*Either "size".*or "name"'):
        t.resolve_and_validate_volumes()


def test_resolve_volumes_invalid_dict_empty():
    """Test empty dict raises ValueError."""
    t = task.Task()
    t._volumes = {'/mnt': {}}
    t.resources = [make_mock_resource()]

    with pytest.raises(ValueError,
                       match='Invalid volume config.*Either "size".*or "name"'):
        t.resolve_and_validate_volumes()


def test_resolve_volumes_invalid_type_list():
    """Test volume config with invalid type (list) raises ValueError."""
    t = task.Task()
    t._volumes = {'/mnt': ['vol1', 'vol2']}
    t.resources = [make_mock_resource()]

    with pytest.raises(ValueError, match='Invalid volume config'):
        t.resolve_and_validate_volumes()


def test_resolve_volumes_with_envs():

    config = {
        'volumes': {
            '/mnt': '${VOLUME_NAME}'
        },
        'envs': {
            'VOLUME_NAME': 'vol1'
        }
    }
    t = task.Task.from_yaml_config(config)
    assert t._volumes == {'/mnt': 'vol1'}


def test_resolve_volumes_with_envs_two():
    config = {
        'volumes': {
            '/mnt': '${VOLUME_NAME}_${VOLUME_SUFFIX}'
        },
        'envs': {
            'VOLUME_NAME': 'vol1',
            'VOLUME_SUFFIX': 'suffix'
        }
    }
    t = task.Task.from_yaml_config(config)
    assert t._volumes == {'/mnt': 'vol1_suffix'}


def test_resolve_volumes_with_envs_none():
    config = {
        'volumes': {
            '/mnt': 'vol_test'
        },
        'envs': {
            'VOLUME_NAME': 'vol1',
            'VOLUME_SUFFIX': 'suffix'
        }
    }
    t = task.Task.from_yaml_config(config)
    assert t._volumes == {'/mnt': 'vol_test'}


def test_resolve_volumes_with_envs_dict():
    config = {
        'volumes': {
            '/mnt': {
                'name': '${VOLUME_NAME}_${VOLUME_SUFFIX}'
            }
        },
        'envs': {
            'VOLUME_NAME': 'vol1',
            'VOLUME_SUFFIX': 'suffix'
        }
    }
    t = task.Task.from_yaml_config(config)
    assert t._volumes == {'/mnt': {'name': 'vol1_suffix'}}


def test_resources_to_config():
    """Test the functionality of converting resources to
    its respective yaml config."""
    t = task.Task()
    resource1 = resources_lib.Resources(cloud='aws',
                                        region='us-west1',
                                        zone='a')
    t.resources = [resource1]
    # get the resource config
    assert t.get_resource_config() == resource1.to_yaml_config()

    t.resources = [resource1, resource1]
    # two identical resources should be combined into a single resource
    assert t.get_resource_config() == resource1.to_yaml_config()

    t.resources = {resource1, resource1}
    # two identical resources should be combined into a single resource
    assert t.get_resource_config() == resource1.to_yaml_config()

    resource2 = resources_lib.Resources(cloud='aws',
                                        region='us-west1',
                                        zone='a',
                                        memory='10GB')
    t.resources = [resource1, resource2]
    # the common config should be factored out
    common_config = resources_lib.Resources(cloud='aws',
                                            region='us-west1',
                                            zone='a').to_yaml_config()
    returned_config = t.get_resource_config()
    ordered = returned_config.pop('ordered')
    assert returned_config == common_config
    assert len(ordered) == 2
    # the first resource should be empty, because all fields
    # are the same as the common config
    assert ordered[0] == {}
    # the second resource should have the modified memory
    assert float(ordered[1]['memory']) == 10.0


def test_task_resource_config_modification():
    """Test the functionality of modifying resource config."""
    t = task.Task()
    resource1 = resources_lib.Resources(cloud='aws',
                                        region='us-west1',
                                        zone='a')
    resource2 = resource1.copy(memory='10GB')
    t.resources = [resource1, resource2]

    # get the resource config
    resource_config = t.get_resource_config()
    assert len(resource_config['ordered']) == 2

    # Modify one of the ordered resources, this should modify the resource1
    resource_config['ordered'][0]['memory'] = '20GB'
    t.set_resources(resource_config)
    # check that resource1 is modified
    assert float(t.resources[0].memory) == 20.0
    assert float(t.resources[1].memory) == 10.0

    # Modify the autostop config for all resources
    # by changing the base config.
    resource_config['autostop'] = {'idle_minutes': 10}
    t.set_resources(resource_config)
    assert t.resources[0].autostop_config.idle_minutes == 10
    assert t.resources[1].autostop_config.idle_minutes == 10


def test_secrets_not_plaintext_on_initialization():
    """Test that secrets initialized in Task are SecretStr objects, not plain strings."""
    secrets = {
        'API_KEY': 'secret-api-key-123',
        'DB_PASSWORD': 'secret-password'
    }
    task_obj = task.Task(run='echo hello', secrets=secrets)

    # Verify all secrets are SecretStr instances, not plain strings
    for key, value in task_obj.secrets.items():
        assert isinstance(
            value,
            SecretStr), f'Secret {key} should be SecretStr, got {type(value)}'
        assert not isinstance(value,
                              str), f'Secret {key} should not be a plain string'
        # Verify we can get the secret value
        assert value.get_secret_value() == secrets[key]


def test_secrets_not_plaintext_after_update_envs_and_secrets_from_workdir():
    """Test that update_envs_and_secrets_from_workdir doesn't put plaintext strings in secrets."""
    # Set up task with git workdir
    task_obj = task.Task(name='test', run='echo hello')
    task_obj.workdir = {
        'url': 'https://github.com/test/repo.git',
        'ref': 'main'
    }

    # Mock GitRepo and clone info
    mock_clone_info = MagicMock()
    mock_clone_info.url = 'https://github.com/test/repo.git'
    mock_clone_info.token = 'test_token_12345'
    mock_clone_info.ssh_key = 'test_ssh_key_content'

    mock_repo_instance = MagicMock()
    mock_repo_instance.get_repo_clone_info.return_value = mock_clone_info
    mock_repo_instance.get_ref_type.return_value = git.GitRefType.BRANCH

    with patch.dict(os.environ, {git.GIT_TOKEN_ENV_VAR: 'test_token_12345'}, clear=True), \
         patch('sky.utils.git.GitRepo', return_value=mock_repo_instance):
        task_obj.update_envs_and_secrets_from_workdir()

    # Verify secrets are SecretStr instances, not plain strings
    assert git.GIT_TOKEN_ENV_VAR in task_obj.secrets
    token_secret = task_obj.secrets[git.GIT_TOKEN_ENV_VAR]
    assert isinstance(token_secret, SecretStr), \
        f'Git token secret should be SecretStr, got {type(token_secret)}'
    assert not isinstance(token_secret, str), \
        'Git token secret should not be a plain string'
    assert token_secret.get_secret_value() == 'test_token_12345'

    assert git.GIT_SSH_KEY_ENV_VAR in task_obj.secrets
    ssh_key_secret = task_obj.secrets[git.GIT_SSH_KEY_ENV_VAR]
    assert isinstance(ssh_key_secret, SecretStr), \
        f'SSH key secret should be SecretStr, got {type(ssh_key_secret)}'
    assert not isinstance(ssh_key_secret, str), \
        'SSH key secret should not be a plain string'
    assert ssh_key_secret.get_secret_value() == 'test_ssh_key_content'


def test_secrets_not_plaintext_after_update_secrets():
    """Test that update_secrets doesn't put plaintext strings in secrets."""
    task_obj = task.Task(run='echo hello')

    # Update secrets with dict
    secrets_dict = {'API_KEY': 'secret1', 'DB_PASSWORD': 'secret2'}
    task_obj.update_secrets(secrets_dict)

    # Verify all secrets are SecretStr instances, not plain strings
    for key, value in task_obj.secrets.items():
        assert isinstance(value, SecretStr), \
            f'Secret {key} should be SecretStr, got {type(value)}'
        assert not isinstance(value, str), \
            f'Secret {key} should not be a plain string'
        assert value.get_secret_value() == secrets_dict[key]

    # Update secrets with list of tuples
    more_secrets = [('JWT_SECRET', 'jwt-secret'),
                    ('REDIS_PASSWORD', 'redis-pass')]
    task_obj.update_secrets(more_secrets)

    # Verify all secrets (including newly added ones) are SecretStr instances
    all_secrets = {
        'API_KEY': 'secret1',
        'DB_PASSWORD': 'secret2',
        'JWT_SECRET': 'jwt-secret',
        'REDIS_PASSWORD': 'redis-pass'
    }
    assert len(task_obj.secrets.keys()) == len(all_secrets.keys())
    for key, value in task_obj.secrets.items():
        assert isinstance(value, SecretStr), \
            f'Secret {key} should be SecretStr, got {type(value)}'
        assert not isinstance(value, str), \
            f'Secret {key} should not be a plain string'
        assert value.get_secret_value() == all_secrets[key]


def test_secrets_not_plaintext_from_yaml_config():
    """Test that from_yaml_config stores secrets as SecretStr objects, not plain strings."""
    config = {
        'run': 'echo hello',
        'envs': {
            'PUBLIC_VAR': 'public-value',
            'DEBUG': 'true'
        },
        'secrets': {
            'API_KEY': 'secret-api-key-123',
            'DATABASE_PASSWORD': 'secret-password-456',
            'JWT_SECRET': 'jwt-secret-token'
        }
    }

    task_obj = task.Task.from_yaml_config(config)

    # Verify all secrets are SecretStr instances, not plain strings
    expected_secrets = {
        'API_KEY': 'secret-api-key-123',
        'DATABASE_PASSWORD': 'secret-password-456',
        'JWT_SECRET': 'jwt-secret-token'
    }
    for key, value in task_obj.secrets.items():
        assert isinstance(value, SecretStr), \
            f'Secret {key} should be SecretStr, got {type(value)}'
        assert not isinstance(value, str), \
            f'Secret {key} should not be a plain string'
        assert value.get_secret_value() == expected_secrets[key]


def test_secrets_not_plaintext_from_yaml_str():
    """Test that from_yaml_str stores secrets as SecretStr objects, not plain strings."""
    yaml_str = """
run: echo hello
envs:
  PUBLIC_VAR: public-value
  DEBUG: "true"
secrets:
  API_KEY: secret-api-key-123
  DATABASE_PASSWORD: secret-password-456
  JWT_SECRET: jwt-secret-token
"""

    task_obj = task.Task.from_yaml_str(yaml_str)

    # Verify all secrets are SecretStr instances, not plain strings
    expected_secrets = {
        'API_KEY': 'secret-api-key-123',
        'DATABASE_PASSWORD': 'secret-password-456',
        'JWT_SECRET': 'jwt-secret-token'
    }
    for key, value in task_obj.secrets.items():
        assert isinstance(value, SecretStr), \
            f'Secret {key} should be SecretStr, got {type(value)}'
        assert not isinstance(value, str), \
            f'Secret {key} should not be a plain string'
        assert value.get_secret_value() == expected_secrets[key]


def test_secrets_not_plaintext_from_yaml():
    """Test that from_yaml stores secrets as SecretStr objects, not plain strings."""
    yaml_content = """
run: echo hello
envs:
  PUBLIC_VAR: public-value
  DEBUG: "true"
secrets:
  API_KEY: secret-api-key-123
  DATABASE_PASSWORD: secret-password-456
  JWT_SECRET: jwt-secret-token
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml',
                                     delete=False) as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = f.name

    try:
        task_obj = task.Task.from_yaml(yaml_path)

        # Verify all secrets are SecretStr instances, not plain strings
        expected_secrets = {
            'API_KEY': 'secret-api-key-123',
            'DATABASE_PASSWORD': 'secret-password-456',
            'JWT_SECRET': 'jwt-secret-token'
        }
        for key, value in task_obj.secrets.items():
            assert isinstance(value, SecretStr), \
                f'Secret {key} should be SecretStr, got {type(value)}'
            assert not isinstance(value, str), \
                f'Secret {key} should not be a plain string'
            assert value.get_secret_value() == expected_secrets[key]
    finally:
        os.unlink(yaml_path)
