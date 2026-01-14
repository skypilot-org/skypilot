"""Unit tests for sky.provision.slurm.utils."""

import pytest

from sky.provision.slurm import utils


class TestFormatSlurmDuration:
    """Test format_slurm_duration()."""

    def test_format_slurm_duration_10000(self):
        """Test format_slurm_duration with 10000 seconds."""
        result = utils.format_slurm_duration(10000)
        assert result == '0-02:46:40'

    def test_format_slurm_duration_100000(self):
        """Test format_slurm_duration with 100000 seconds."""
        result = utils.format_slurm_duration(100000)
        assert result == '1-03:46:40'

    def test_format_slurm_duration_1000000(self):
        """Test format_slurm_duration with 1000000 seconds."""
        result = utils.format_slurm_duration(1000000)
        assert result == '11-13:46:40'

    def test_format_slurm_duration_none(self):
        """Test format_slurm_duration with None returns UNLIMITED."""
        result = utils.format_slurm_duration(None)
        assert result == 'UNLIMITED'
