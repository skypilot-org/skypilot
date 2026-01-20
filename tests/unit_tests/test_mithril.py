"""Tests for Mithril cloud provider."""

from pathlib import Path
import tempfile

import pytest

from sky.clouds import mithril


class TestMithrilCredentials:
    """Test cases for Mithril credential handling."""

    def test_check_credentials_missing(self, monkeypatch, tmp_path):
        """Test that missing credentials file returns invalid."""
        fake_path = tmp_path / 'config.yaml'
        monkeypatch.setattr(mithril.Mithril, 'CREDENTIALS_PATH', str(fake_path))
        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)

        valid, msg = mithril.Mithril._check_credentials()

        assert not valid
        assert 'Mithril credentials not found' in msg

    def test_check_credentials_from_file(self, monkeypatch, tmp_path):
        """Test that credentials are valid when config file exists."""
        cred_path = tmp_path / 'config.yaml'
        cred_path.write_text('api_key: test-key')
        monkeypatch.setattr(mithril.Mithril, 'CREDENTIALS_PATH', str(cred_path))
        monkeypatch.delenv('MITHRIL_API_KEY', raising=False)
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)

        valid, msg = mithril.Mithril._check_credentials()

        assert valid
        assert msg is None

    def test_check_credentials_from_env_vars(self, monkeypatch, tmp_path):
        """Test that credentials are valid when env vars are set."""
        # Ensure no config file exists
        fake_path = tmp_path / 'config.yaml'
        monkeypatch.setattr(mithril.Mithril, 'CREDENTIALS_PATH', str(fake_path))
        # Set environment variables
        monkeypatch.setenv('MITHRIL_API_KEY', 'test-api-key')
        monkeypatch.setenv('MITHRIL_PROJECT', 'test-project')

        valid, msg = mithril.Mithril._check_credentials()

        assert valid
        assert msg is None

    def test_check_credentials_partial_env_vars_invalid(self, monkeypatch,
                                                        tmp_path):
        """Test that only one env var set is not sufficient."""
        fake_path = tmp_path / 'config.yaml'
        monkeypatch.setattr(mithril.Mithril, 'CREDENTIALS_PATH', str(fake_path))
        # Set only API key, not project
        monkeypatch.setenv('MITHRIL_API_KEY', 'test-api-key')
        monkeypatch.delenv('MITHRIL_PROJECT', raising=False)

        valid, msg = mithril.Mithril._check_credentials()

        assert not valid
        assert 'Mithril credentials not found' in msg

    def test_credential_file_mounts_when_file_exists(self):
        """Test get_credential_file_mounts returns mapping when file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cred_path = Path(tmpdir) / 'config.yaml'
            cred_path.touch()
            with pytest.MonkeyPatch.context() as m:
                m.setattr(mithril.Mithril, 'CREDENTIALS_PATH', str(cred_path))
                mounts = mithril.Mithril.get_credential_file_mounts()

                assert str(cred_path) in mounts
                assert mounts[str(cred_path)] == '~/.mithril/config.yaml'

    def test_credential_file_mounts_when_file_missing(self, monkeypatch,
                                                      tmp_path):
        """Test get_credential_file_mounts returns empty dict when no file."""
        fake_path = tmp_path / 'config.yaml'
        monkeypatch.setattr(mithril.Mithril, 'CREDENTIALS_PATH', str(fake_path))

        mounts = mithril.Mithril.get_credential_file_mounts()

        assert mounts == {}


class TestMithrilValidation:
    """Test cases for Mithril validation logic."""

    def test_region_zone_validation_disallows_zones(self):
        """Test that Mithril raises ValueError when zone is specified."""
        cloud = mithril.Mithril()
        with pytest.raises(ValueError, match='does not support zones'):
            cloud.validate_region_zone('some-region', 'zone-1')
