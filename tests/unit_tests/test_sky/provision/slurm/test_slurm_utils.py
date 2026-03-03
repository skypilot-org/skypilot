"""Unit tests for sky.provision.slurm.utils."""

import pytest

from sky.provision.slurm import utils


class TestFormatSlurmDuration:
    """Test format_slurm_duration()."""

    @pytest.mark.parametrize('duration_seconds,expected', [
        (10000, '0-02:46:40'),
        (100000, '1-03:46:40'),
        (1000000, '11-13:46:40'),
        (None, 'UNLIMITED'),
    ])
    def test_format_slurm_duration(self, duration_seconds, expected):
        """Test format_slurm_duration with various inputs."""
        result = utils.format_slurm_duration(duration_seconds)
        assert result == expected


class TestGetIdentityFile:
    """Test get_identity_file() helper function."""

    @pytest.mark.parametrize(
        'ssh_config_dict,expected',
        [
            # Returns first file when multiple identity files are present
            ({
                'identityfile': ['/path/to/key1', '/path/to/key2']
            }, '/path/to/key1'),
            # Returns single identity file
            ({
                'identityfile': ['/home/user/.ssh/id_rsa']
            }, '/home/user/.ssh/id_rsa'),
            # Returns None when identityfile key is missing
            ({
                'hostname': 'example.com',
                'user': 'testuser'
            }, None),
            # Returns None when identityfile is an empty list
            ({
                'identityfile': []
            }, None),
            # Returns None when identityfile value is None
            ({
                'identityfile': None
            }, None),
        ])
    def test_get_identity_file(self, ssh_config_dict, expected):
        """Test get_identity_file with various SSH config inputs."""
        result = utils.get_identity_file(ssh_config_dict)
        assert result == expected
