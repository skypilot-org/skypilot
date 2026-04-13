"""Tests for Spheron cloud provider."""

import os
from pathlib import Path
import tempfile
from unittest import mock

import pytest

from sky import clouds
from sky.clouds import spheron


class TestSpheronCloud:
    """Test basic Spheron cloud properties."""

    def test_repr(self):
        cloud = spheron.Spheron()
        assert cloud._REPR == 'Spheron'

    def test_name(self):
        cloud = spheron.Spheron()
        assert cloud.name == 'spheron'

    def test_max_cluster_name_length(self):
        assert spheron.Spheron._MAX_CLUSTER_NAME_LEN_LIMIT == 120
        assert spheron.Spheron._max_cluster_name_length() == 120

    def test_get_zone_shell_cmd_returns_none(self):
        assert spheron.Spheron.get_zone_shell_cmd() is None

    def test_get_user_identities_returns_none(self):
        assert spheron.Spheron.get_user_identities() is None


class TestSpheronUnsupportedFeatures:
    """Test that unsupported features are correctly declared."""

    def test_unsupported_features_are_strings(self):
        cloud = spheron.Spheron()
        for feature, msg in cloud._CLOUD_UNSUPPORTED_FEATURES.items():
            assert isinstance(
                msg, str), (f'Feature {feature} message should be a string')

    def test_stop_is_unsupported(self):
        assert (clouds.CloudImplementationFeatures.STOP
                in spheron.Spheron._CLOUD_UNSUPPORTED_FEATURES)

    def test_multi_node_is_unsupported(self):
        assert (clouds.CloudImplementationFeatures.MULTI_NODE
                in spheron.Spheron._CLOUD_UNSUPPORTED_FEATURES)


class TestSpheronCredentials:
    """Test Spheron credential checking."""

    def test_check_credentials_missing_file(self, tmp_path):
        """Test that missing api_key file returns invalid."""
        fake_api_key = tmp_path / 'api_key'
        with mock.patch('os.path.exists', return_value=False):
            valid, msg = spheron.Spheron._check_compute_credentials()
        assert not valid
        assert msg is not None
        assert 'api_key' in msg.lower() or 'spheron' in msg.lower()

    def test_check_credentials_file_present(self, tmp_path):
        """Test that a valid api_key file returns True."""
        api_key_file = tmp_path / 'api_key'
        api_key_file.write_text('test-api-key')

        with mock.patch('os.path.exists', return_value=True), \
             mock.patch('builtins.open',
                        mock.mock_open(read_data='test-api-key')):
            valid, msg = spheron.Spheron._check_compute_credentials()

        assert valid
        assert msg is None

    def test_check_credentials_empty_file(self, tmp_path):
        """Test that an empty api_key file returns invalid."""
        api_key_file = tmp_path / 'api_key'
        api_key_file.write_text('')

        with mock.patch('os.path.exists', return_value=True), \
             mock.patch('builtins.open', mock.mock_open(read_data='')):
            valid, msg = spheron.Spheron._check_compute_credentials()

        assert not valid
        assert msg is not None

    def test_check_credentials_with_real_file(self, tmp_path, monkeypatch):
        """Test credential check using a real temp file via expanduser patch."""
        api_key_file = tmp_path / 'api_key'
        api_key_file.write_text('my-real-api-key')

        monkeypatch.setattr(
            'os.path.expanduser', lambda x: str(api_key_file)
            if 'spheron' in x else x)

        valid, msg = spheron.Spheron._check_compute_credentials()
        assert valid
        assert msg is None


class TestSpheronCredentialFileMounts:
    """Test credential file mounts."""

    def test_credential_file_mounts_keys(self):
        """Test that credential mounts include api_key."""
        cloud = spheron.Spheron()
        mounts = cloud.get_credential_file_mounts()
        # Should contain a mapping for api_key
        assert any('api_key' in k for k in mounts)

    def test_credential_file_mounts_are_self_mapped(self):
        """Test that each credential file maps to itself (remote == local)."""
        cloud = spheron.Spheron()
        mounts = cloud.get_credential_file_mounts()
        for remote, local in mounts.items():
            assert remote == local, (
                f'Expected self-mount but got {remote} -> {local}')


class TestSpheronCatalog:
    """Test Spheron catalog functions."""

    def test_get_instance_info_empty_df_returns_defaults(self, monkeypatch):
        """Test get_instance_info falls back to defaults when catalog is empty."""
        import pandas as pd

        from sky.catalog import spheron_catalog

        # Patch the internal df getter to return an empty dataframe
        empty_df = pd.DataFrame(
            columns=['InstanceType', 'Region', 'SpheronInstanceType'])
        monkeypatch.setattr(spheron_catalog, '_get_df', lambda: empty_df)

        result = spheron_catalog.get_instance_info('nonexistent-type',
                                                   'us-east-1')
        assert result['Provider'] == ''
        assert result['SpheronInstanceType'] in ('SPOT', 'DEDICATED')
        assert result['AcceleratorCount'] == 1

    def test_get_instance_info_use_spot_default_type(self, monkeypatch):
        """Test that use_spot=True returns SPOT as default SpheronInstanceType."""
        import pandas as pd

        from sky.catalog import spheron_catalog

        empty_df = pd.DataFrame(
            columns=['InstanceType', 'Region', 'SpheronInstanceType'])
        monkeypatch.setattr(spheron_catalog, '_get_df', lambda: empty_df)

        result = spheron_catalog.get_instance_info('nonexistent-type',
                                                   'us-east-1',
                                                   use_spot=True)
        assert result['SpheronInstanceType'] == 'SPOT'

    def test_get_instance_info_on_demand_default_type(self, monkeypatch):
        """Test that use_spot=False returns DEDICATED as default type."""
        import pandas as pd

        from sky.catalog import spheron_catalog

        empty_df = pd.DataFrame(
            columns=['InstanceType', 'Region', 'SpheronInstanceType'])
        monkeypatch.setattr(spheron_catalog, '_get_df', lambda: empty_df)

        result = spheron_catalog.get_instance_info('nonexistent-type',
                                                   'us-east-1',
                                                   use_spot=False)
        assert result['SpheronInstanceType'] == 'DEDICATED'
