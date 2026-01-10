"""Unit tests for gcloud storage migration from gsutil.

Tests the migration from gsutil to gcloud storage commands as part of
GitHub issue #8457.

These tests verify:
1. The new get_gcloud_storage_command() function works correctly
2. Backward compatibility with get_gsutil_command() is maintained
3. Command string generation follows the expected patterns
"""
import pytest

from sky.data import data_utils


class TestGetGcloudStorageCommand:
    """Tests for get_gcloud_storage_command function."""

    def test_returns_correct_command(self):
        """Test that the function returns 'gcloud storage'."""
        result = data_utils.get_gcloud_storage_command()
        assert result == 'gcloud storage'

    def test_return_type_is_string(self):
        """Test return type is string (unlike gsutil which returns tuple)."""
        result = data_utils.get_gcloud_storage_command()
        assert isinstance(result, str)

    def test_no_parallel_flag_needed(self):
        """gcloud storage has automatic parallelism, no -m flag needed."""
        result = data_utils.get_gcloud_storage_command()
        assert '-m' not in result


class TestGsutilCommandBackwardCompatibility:
    """Ensure backward compatibility with gsutil command."""

    def test_gsutil_command_still_works(self):
        """Test that get_gsutil_command still returns expected format."""
        alias, alias_gen = data_utils.get_gsutil_command()
        assert alias == 'skypilot_gsutil'
        assert isinstance(alias_gen, str)
        assert 'gsutil' in alias_gen
        assert '-m' in alias_gen  # gsutil needs -m for parallel


class TestCommandPatterns:
    """Test that command patterns match expected gcloud storage format."""

    def test_rsync_command_pattern(self):
        """Test rsync command can be constructed correctly."""
        cmd = data_utils.get_gcloud_storage_command()
        rsync_cmd = f'{cmd} rsync --recursive /src gs://bucket/dest'
        assert 'gcloud storage rsync --recursive' in rsync_cmd

    def test_cp_command_pattern(self):
        """Test cp command can be constructed correctly."""
        cmd = data_utils.get_gcloud_storage_command()
        cp_cmd = f'{cmd} cp gs://bucket/file /local'
        assert 'gcloud storage cp' in cp_cmd

    def test_rm_command_pattern(self):
        """Test rm command can be constructed correctly."""
        cmd = data_utils.get_gcloud_storage_command()
        rm_cmd = f'{cmd} rm --recursive gs://bucket'
        assert 'gcloud storage rm --recursive' in rm_cmd

    def test_exclude_pattern_format(self):
        """Test exclude pattern can be added to rsync command."""
        cmd = data_utils.get_gcloud_storage_command()
        pattern = r'^\.git/.*$'
        rsync_cmd = f"{cmd} rsync --recursive --exclude='{pattern}' /src gs://bucket"
        assert '--exclude=' in rsync_cmd
        assert pattern in rsync_cmd
