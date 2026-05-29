"""Tests for Mithril cloud provider."""

import os

import pytest

from sky import clouds
from sky.clouds import mithril
from sky.provision.mithril import utils as mithril_utils


class TestMithrilCredentialsPath:
    """Test cases for get_credentials_path method."""

    def test_default_path_without_xdg(self, monkeypatch):
        """Test default path when XDG_CONFIG_HOME is not set."""
        monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)

        path = mithril.Mithril.get_credentials_path()

        assert path == '~/.config/mithril/config.yaml'

    def test_path_with_xdg_config_home(self, monkeypatch, tmp_path):
        """Test path respects XDG_CONFIG_HOME when set."""
        xdg_dir = tmp_path / 'custom_config'
        monkeypatch.setenv('XDG_CONFIG_HOME', str(xdg_dir))

        path = mithril.Mithril.get_credentials_path()

        expected = os.path.join(str(xdg_dir), 'mithril', 'config.yaml')
        assert path == expected


class TestMithrilCredentials:
    """Test cases for Mithril credential handling."""

    def test_check_credentials_missing(self, monkeypatch, tmp_path):
        """Test that missing credentials file returns invalid."""
        fake_path = tmp_path / 'config.yaml'
        monkeypatch.setattr(mithril.Mithril, 'get_credentials_path',
                            classmethod(lambda cls: str(fake_path)))
        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)

        valid, msg = mithril.Mithril.check_credentials(
            clouds.CloudCapability.COMPUTE)

        assert not valid
        assert 'Mithril credentials not found' in msg

    def test_check_credentials_from_file(self, monkeypatch, tmp_path):
        """Test that credentials are valid when config file exists."""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text('api_key: test-key')
        monkeypatch.setattr(mithril.Mithril, 'get_credentials_path',
                            classmethod(lambda cls: str(cred_path)))
        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)

        valid, msg = mithril.Mithril.check_credentials(
            clouds.CloudCapability.COMPUTE)

        assert valid
        assert msg is None

    def test_check_credentials_from_env_vars(self, monkeypatch, tmp_path):
        """Test that credentials are valid when env vars are set."""
        # Ensure no config file exists
        fake_path = tmp_path / 'config.yaml'
        monkeypatch.setattr(mithril.Mithril, 'get_credentials_path',
                            classmethod(lambda cls: str(fake_path)))
        # Set environment variables
        monkeypatch.setenv('MITHRIL_API_KEY', 'test-api-key')
        monkeypatch.setenv('MITHRIL_PROJECT', 'test-project')

        valid, msg = mithril.Mithril.check_credentials(
            clouds.CloudCapability.COMPUTE)

        assert valid
        assert msg is None

    def test_check_credentials_partial_env_vars_invalid(self, monkeypatch,
                                                        tmp_path):
        """Test that only one env var set is not sufficient."""
        fake_path = tmp_path / 'config.yaml'
        monkeypatch.setattr(mithril.Mithril, 'get_credentials_path',
                            classmethod(lambda cls: str(fake_path)))
        # Set only API key, not project
        monkeypatch.setenv('MITHRIL_API_KEY', 'test-api-key')
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)

        valid, msg = mithril.Mithril.check_credentials(
            clouds.CloudCapability.COMPUTE)

        assert not valid
        assert 'Mithril credentials not found' in msg

    def test_credential_file_mounts_when_file_exists(self, monkeypatch,
                                                     tmp_path):
        """Test get_credential_file_mounts returns correct mapping."""
        # Create the credential file in tmp_path simulating ~/.config/mithril/
        cred_file = tmp_path / '.config' / 'mithril' / 'config.yaml'
        cred_file.parent.mkdir(parents=True)
        cred_file.touch()

        # Use a path with ~ that will be expanded
        unexpanded_path = '~/.config/mithril/config.yaml'
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(mithril.Mithril, 'get_credentials_path',
                            classmethod(lambda cls: unexpanded_path))

        mounts = mithril.Mithril.get_credential_file_mounts()

        # The method returns {remote_path: local_path}
        # Key should be the unexpanded remote path, value should be
        # the local expanded path
        expected_expanded = str(cred_file)
        assert unexpanded_path in mounts
        assert mounts[unexpanded_path] == expected_expanded

    def test_credential_file_mounts_when_file_missing(self, monkeypatch,
                                                      tmp_path):
        """Test get_credential_file_mounts returns empty dict when no file."""
        fake_path = tmp_path / 'config.yaml'
        monkeypatch.setattr(mithril.Mithril, 'get_credentials_path',
                            classmethod(lambda cls: str(fake_path)))

        mounts = mithril.Mithril.get_credential_file_mounts()

        assert not mounts


class TestMithrilValidation:
    """Test cases for Mithril validation logic."""

    def test_region_zone_validation_disallows_zones(self):
        """Test that Mithril raises ValueError when zone is specified."""
        cloud = mithril.Mithril()
        with pytest.raises(ValueError, match='does not support zones'):
            cloud.validate_region_zone('some-region', 'zone-1')


class TestGetConfig:
    """Test cases for get_config function with profile-based schema."""

    def test_config_from_profile_file(self, monkeypatch, tmp_path):
        """Test config is loaded from profile-based config file."""
        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)
        monkeypatch.delenv('MITHRIL_API_URL', raising=False)
        monkeypatch.delenv('MITHRIL_PROFILE', raising=False)

        config_content = """\
current_profile: default
profiles:
  default:
    api_key: file-api-key
    project_id: file-project-id
    api_url: https://custom.api.mithril.ai
"""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text(config_content)

        monkeypatch.setattr(mithril_utils, 'get_credentials_path',
                            lambda: str(cred_path))

        config = mithril_utils.resolve_current_config()

        assert config['api_key'] == 'file-api-key'
        assert config['project_id'] == 'file-project-id'
        assert config['api_url'] == 'https://custom.api.mithril.ai'

    def test_env_vars_override_profile(self, monkeypatch, tmp_path):
        """Test environment variables override profile config."""
        config_content = """\
current_profile: default
profiles:
  default:
    api_key: file-api-key
    project_id: file-project-id
    api_url: https://file.api.mithril.ai
"""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text(config_content)

        monkeypatch.setattr(mithril_utils, 'get_credentials_path',
                            lambda: str(cred_path))

        # Set env vars to override
        monkeypatch.setenv('MITHRIL_API_KEY', 'env-api-key')
        monkeypatch.setenv('MITHRIL_PROJECT', 'env-project-id')
        monkeypatch.setenv('MITHRIL_API_URL', 'https://env.api.mithril.ai')
        monkeypatch.delenv('MITHRIL_PROFILE', raising=False)

        config = mithril_utils.resolve_current_config()

        assert config['api_key'] == 'env-api-key'
        assert config['project_id'] == 'env-project-id'
        assert config['api_url'] == 'https://env.api.mithril.ai'

    def test_partial_env_vars_with_profile(self, monkeypatch, tmp_path):
        """Test partial env vars combine with profile config."""
        config_content = """\
current_profile: default
profiles:
  default:
    api_key: file-api-key
    project_id: file-project-id
"""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text(config_content)

        monkeypatch.setattr(mithril_utils, 'get_credentials_path',
                            lambda: str(cred_path))

        # Only override API key, keep project_id from file
        monkeypatch.setenv('MITHRIL_API_KEY', 'env-api-key')
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)
        monkeypatch.delenv('MITHRIL_API_URL', raising=False)
        monkeypatch.delenv('MITHRIL_PROFILE', raising=False)

        config = mithril_utils.resolve_current_config()

        assert config['api_key'] == 'env-api-key'
        assert config['project_id'] == 'file-project-id'
        # Default API URL when not specified
        assert config['api_url'] == 'https://api.mithril.ai'

    def test_mithril_profile_env_selects_profile(self, monkeypatch, tmp_path):
        """Test MITHRIL_PROFILE env var selects different profile."""
        config_content = """\
current_profile: default
profiles:
  default:
    api_key: default-key
    project_id: default-proj
  staging:
    api_key: staging-key
    project_id: staging-proj
"""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text(config_content)

        monkeypatch.setattr(mithril_utils, 'get_credentials_path',
                            lambda: str(cred_path))

        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)
        monkeypatch.delenv('MITHRIL_API_URL', raising=False)
        monkeypatch.setenv('MITHRIL_PROFILE', 'staging')

        config = mithril_utils.resolve_current_config()

        assert config['api_key'] == 'staging-key'
        assert config['project_id'] == 'staging-proj'

    def test_missing_api_key_raises_error(self, monkeypatch, tmp_path):
        """Test error is raised when API key is not found."""
        config_content = """\
current_profile: default
profiles:
  default:
    project_id: file-project-id
"""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text(config_content)

        monkeypatch.setattr(mithril_utils, 'get_credentials_path',
                            lambda: str(cred_path))
        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)
        monkeypatch.delenv('MITHRIL_PROFILE', raising=False)

        with pytest.raises(mithril_utils.MithrilError,
                           match='API key not found'):
            mithril_utils.resolve_current_config()

    def test_missing_project_id_raises_error(self, monkeypatch, tmp_path):
        """Test error is raised when project ID is not found."""
        config_content = """\
current_profile: default
profiles:
  default:
    api_key: file-api-key
"""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text(config_content)

        monkeypatch.setattr(mithril_utils, 'get_credentials_path',
                            lambda: str(cred_path))
        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)
        monkeypatch.delenv('MITHRIL_PROFILE', raising=False)

        with pytest.raises(mithril_utils.MithrilError,
                           match='project ID not found'):
            mithril_utils.resolve_current_config()

    def test_config_only_from_env_vars_no_file(self, monkeypatch, tmp_path):
        """Test config works with only env vars when no config file exists."""
        fake_path = tmp_path / 'nonexistent.yaml'
        monkeypatch.setattr(mithril_utils, 'get_credentials_path',
                            lambda: str(fake_path))

        monkeypatch.setenv('MITHRIL_API_KEY', 'env-api-key')
        monkeypatch.setenv('MITHRIL_PROJECT', 'env-project-id')
        monkeypatch.delenv('MITHRIL_API_URL', raising=False)
        monkeypatch.delenv('MITHRIL_PROFILE', raising=False)

        config = mithril_utils.resolve_current_config()

        assert config['api_key'] == 'env-api-key'
        assert config['project_id'] == 'env-project-id'
        assert config['api_url'] == 'https://api.mithril.ai'

    def test_missing_current_profile_no_env_raises_error(
            self, monkeypatch, tmp_path):
        """Test error when current_profile is missing and no env vars set.

        Without current_profile or MITHRIL_PROFILE, no profile config is
        loaded.  With no env vars either, _build_config raises because the
        API key is missing.
        """
        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)
        monkeypatch.delenv('MITHRIL_PROFILE', raising=False)

        config_content = """\
profiles:
  default:
    api_key: key
    project_id: proj
"""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text(config_content)

        monkeypatch.setattr(mithril_utils, 'get_credentials_path',
                            lambda: str(cred_path))

        with pytest.raises(mithril_utils.MithrilError,
                           match='API key not found'):
            mithril_utils.resolve_current_config()

    def test_profile_not_found_raises_error(self, monkeypatch, tmp_path):
        """Test error is raised when specified profile doesn't exist."""
        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)
        monkeypatch.delenv('MITHRIL_PROFILE', raising=False)

        config_content = """\
current_profile: nonexistent
profiles:
  default:
    api_key: key
    project_id: proj
"""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text(config_content)

        monkeypatch.setattr(mithril_utils, 'get_credentials_path',
                            lambda: str(cred_path))

        with pytest.raises(mithril_utils.MithrilError,
                           match='profile \'nonexistent\' not found'):
            mithril_utils.resolve_current_config()
