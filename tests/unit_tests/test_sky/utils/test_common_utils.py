import os
from unittest import mock

import pytest

from sky import exceptions
from sky.utils import common_utils

MOCKED_USER_HASH = 'ab12cd34'


class TestTruncateLongString:

    def test_no_truncation_needed(self):
        s = "short string"
        result = common_utils.truncate_long_string(s, 15)
        assert result == s

    def test_end_truncation(self):
        s = "this is a very long string that needs truncation"
        result = common_utils.truncate_long_string(s, 20)
        assert len(result) <= 20 + 3  # +3 for '...'
        assert result.endswith('...')
        assert result.startswith('this is a very')

    def test_middle_truncation(self):
        s = "us-west-2-availability-zone-1"
        result = common_utils.truncate_long_string(s, 20, truncate_middle=True)
        assert len(result) <= 20
        assert '...' in result
        assert result.startswith('us-west')
        assert result.endswith('zone-1')

    def test_middle_truncation_odd_length(self):
        s = "us-west-2-availability-zone-1"
        result = common_utils.truncate_long_string(s, 15, truncate_middle=True)
        assert len(result) <= 15
        assert '...' in result
        assert result.startswith('us-w')
        assert result.endswith('ne-1')

    def test_middle_truncation_very_short(self):
        s = "us-west-2-availability-zone-1"
        result = common_utils.truncate_long_string(s, 3, truncate_middle=True)
        assert result == '...'

    def test_empty_string(self):
        assert common_utils.truncate_long_string('', 10) == ''

    def test_exact_length_no_truncation(self):
        assert common_utils.truncate_long_string(
            'abcde', 5, truncate_middle=True) == 'abcde'

    def test_one_less_than_length(self):
        assert common_utils.truncate_long_string('abcde',
                                                 4,
                                                 truncate_middle=True) == 'a...'

    def test_middle_truncation_even_length(self):
        assert common_utils.truncate_long_string(
            'abcdefghijklmnopqrstuvwxyz', 10,
            truncate_middle=True) == 'abcd...xyz'

    def test_middle_truncation_odd_max_length(self):
        assert common_utils.truncate_long_string(
            'abcdefghijklmnopqrstuvwxyz', 11,
            truncate_middle=True) == 'abcd...wxyz'


class TestCheckClusterNameIsValid:

    def test_check(self):
        common_utils.check_cluster_name_is_valid("lora")

    def test_check_with_hyphen(self):
        common_utils.check_cluster_name_is_valid("seed-1")

    def test_check_with_characters_to_transform(self):
        common_utils.check_cluster_name_is_valid("Cuda_11.8")

    def test_check_when_starts_with_number(self):
        with pytest.raises(exceptions.InvalidClusterNameError):
            common_utils.check_cluster_name_is_valid("11.8cuda")

    def test_check_with_invalid_characters(self):
        with pytest.raises(exceptions.InvalidClusterNameError):
            common_utils.check_cluster_name_is_valid("lor@")

    def test_check_when_none(self):
        common_utils.check_cluster_name_is_valid(None)


class TestMakeClusterNameOnCloud:

    @mock.patch('sky.utils.common_utils.get_user_hash')
    def test_make(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "lora-ab12cd34" == common_utils.make_cluster_name_on_cloud(
            "lora")

    @mock.patch('sky.utils.common_utils.get_user_hash')
    def test_make_with_hyphen(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "seed-1-ab12cd34" == common_utils.make_cluster_name_on_cloud(
            "seed-1")

    @mock.patch('sky.utils.common_utils.get_user_hash')
    def test_make_with_characters_to_transform(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "cud-73-ab12cd34" == common_utils.make_cluster_name_on_cloud(
            "Cuda_11.8")
        assert "cuda-11-8-ab12cd34" == common_utils.make_cluster_name_on_cloud(
            "Cuda_11.8", max_length=20)


class TestCgroupFunctions:
    """Test cgroup-related functions."""

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('sky.utils.common_utils._is_cgroup_v2')
    def test_get_cgroup_cpu_limit_v2(self, mock_is_v2, mock_open):
        # Test cgroup v2 CPU limit
        mock_is_v2.return_value = True
        mock_open.return_value.__enter__().read.return_value = '100000 100000'
        assert common_utils._get_cgroup_cpu_limit() == 1.0

        # Test no limit ("max")
        mock_open.return_value.__enter__().read.return_value = 'max 100000'
        assert common_utils._get_cgroup_cpu_limit() is None

        # Test partial cores
        mock_open.return_value.__enter__().read.return_value = '50000 100000'
        assert common_utils._get_cgroup_cpu_limit() == 0.5

        # Test file read error
        mock_open.side_effect = IOError
        assert common_utils._get_cgroup_cpu_limit() is None

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('sky.utils.common_utils._is_cgroup_v2')
    def test_get_cgroup_cpu_limit_v1(self, mock_is_v2, mock_open):
        # Test cgroup v1 CPU limit
        mock_is_v2.return_value = False

        # Mock both quota and period files
        mock_files = {
            '/sys/fs/cgroup/cpu/cpu.cfs_quota_us':
                mock.mock_open(read_data='100000').return_value,
            '/sys/fs/cgroup/cpu/cpu.cfs_period_us':
                mock.mock_open(read_data='100000').return_value,
        }
        mock_open.side_effect = lambda path, *args, **kwargs: mock_files[path]

        assert common_utils._get_cgroup_cpu_limit() == 1.0

        # Test partial cores
        mock_files = {
            '/sys/fs/cgroup/cpu/cpu.cfs_quota_us':
                mock.mock_open(read_data='50000').return_value,
            '/sys/fs/cgroup/cpu/cpu.cfs_period_us':
                mock.mock_open(read_data='100000').return_value,
        }
        mock_open.side_effect = lambda path, *args, **kwargs: mock_files[path]

        assert common_utils._get_cgroup_cpu_limit() == 0.5

        # Test no limit (-1)
        mock_files = {
            '/sys/fs/cgroup/cpu/cpu.cfs_quota_us': mock.mock_open(read_data='-1'
                                                                 ).return_value,
            '/sys/fs/cgroup/cpu/cpu.cfs_period_us':
                mock.mock_open(read_data='100000').return_value,
        }
        mock_open.side_effect = lambda path, *args, **kwargs: mock_files[path]

        assert common_utils._get_cgroup_cpu_limit() is None

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('sky.utils.common_utils._is_cgroup_v2')
    def test_get_cgroup_memory_limit_v2(self, mock_is_v2, mock_open):
        # Test cgroup v2 memory limit
        mock_is_v2.return_value = True

        # Test normal limit (8GB)
        mock_open.return_value.__enter__().read.return_value = str(8 * 1024**3)
        assert common_utils._get_cgroup_memory_limit() == 8 * 1024**3

        # Test no limit ("max")
        mock_open.return_value.__enter__().read.return_value = 'max'
        assert common_utils._get_cgroup_memory_limit() is None

        # Test empty value
        mock_open.return_value.__enter__().read.return_value = ''
        assert common_utils._get_cgroup_memory_limit() is None

        # Test file read error
        mock_open.side_effect = IOError
        assert common_utils._get_cgroup_memory_limit() is None

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('sky.utils.common_utils._is_cgroup_v2')
    def test_get_cgroup_memory_limit_v1(self, mock_is_v2, mock_open):
        # Test cgroup v1 memory limit
        mock_is_v2.return_value = False

        # Test normal limit (1GB)
        mock_open.return_value.__enter__().read.return_value = str(1024**3)
        assert common_utils._get_cgroup_memory_limit() == 1024**3

        # Test empty value
        mock_open.return_value.__enter__().read.return_value = ''
        assert common_utils._get_cgroup_memory_limit() is None

        # Test file read error
        mock_open.side_effect = IOError
        assert common_utils._get_cgroup_memory_limit() is None

    @mock.patch('os.path.isfile')
    def test_is_cgroup_v2(self, mock_exists):
        # Test cgroup v2 detection
        mock_exists.return_value = True
        assert common_utils._is_cgroup_v2() is True

        mock_exists.return_value = False
        assert common_utils._is_cgroup_v2() is False

    @mock.patch('psutil.cpu_count')
    @mock.patch('sky.utils.common_utils._get_cgroup_cpu_limit')
    @mock.patch('os.sched_getaffinity', create=True)
    def test_get_cpu_count(self, mock_affinity, mock_cgroup_cpu,
                           mock_cpu_count):
        # Test when no cgroup limit
        mock_cpu_count.return_value = 8
        mock_cgroup_cpu.return_value = None
        # Non-Linux platforms
        delattr(os, 'sched_getaffinity')
        assert common_utils.get_cpu_count() == 8

        # Test with CPU affinity
        setattr(os, 'sched_getaffinity', mock_affinity)
        mock_affinity.return_value = {0, 1, 2, 3}
        assert common_utils.get_cpu_count() == 4

        # Test when cgroup limit is higher
        mock_cgroup_cpu.return_value = 16.0
        assert common_utils.get_cpu_count() == 4

        # Test when cgroup limit is lower
        mock_cgroup_cpu.return_value = 2.0
        assert common_utils.get_cpu_count() == 2

        # Test with env var
        with mock.patch.dict('os.environ',
                             {'SKYPILOT_POD_CPU_CORE_LIMIT': '2'}):
            assert common_utils.get_cpu_count() == 2

    @mock.patch('psutil.virtual_memory')
    @mock.patch('sky.utils.common_utils._get_cgroup_memory_limit')
    def test_get_mem_size_gb(self, mock_cgroup_mem, mock_virtual_memory):
        # Test when no cgroup limit
        mock_virtual_memory.return_value.total = 8 * 1024**3  # 8GB
        mock_cgroup_mem.return_value = None
        assert common_utils.get_mem_size_gb() == 8.0

        # Test when cgroup limit is lower
        mock_cgroup_mem.return_value = 4 * 1024**3  # 4GB
        assert common_utils.get_mem_size_gb() == 4.0

        # Test when cgroup limit is higher
        mock_cgroup_mem.return_value = 16 * 1024**3  # 16GB
        assert common_utils.get_mem_size_gb() == 8.0

        # Test with env var
        with mock.patch.dict('os.environ',
                             {'SKYPILOT_POD_MEMORY_GB_LIMIT': '2'}):
            assert common_utils.get_mem_size_gb() == 2.0


class TestRedactSecretsValues:
    """Test secret value redaction in command lines."""

    def test_secret_separate_with_value(self):
        """Test --secret KEY=value format."""
        argv = ['sky', 'launch', '--secret', 'HF_TOKEN=secret123', 'app.yaml']
        result = common_utils._redact_secrets_values(argv)
        expected = [
            'sky', 'launch', '--secret', 'HF_TOKEN=<redacted>', 'app.yaml'
        ]
        assert result == expected

    def test_secret_separate_without_value(self):
        """Test --secret KEY format (no value to redact)."""
        argv = ['sky', 'launch', '--secret', 'HF_TOKEN', 'app.yaml']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret', 'HF_TOKEN', 'app.yaml']
        assert result == expected

    def test_secret_combined_with_value(self):
        """Test --secret=KEY=value format."""
        argv = ['sky', 'launch', '--secret=HF_TOKEN=secret123', 'app.yaml']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret=HF_TOKEN=<redacted>', 'app.yaml']
        assert result == expected

    def test_secret_combined_without_value(self):
        """Test --secret=KEY format (no value to redact)."""
        argv = ['sky', 'launch', '--secret=HF_TOKEN', 'app.yaml']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret=HF_TOKEN', 'app.yaml']
        assert result == expected

    def test_multiple_secret_args(self):
        """Test multiple --secret arguments with different formats."""
        argv = [
            'sky', 'launch', '--secret', 'KEY1=secret1', '--secret',
            'KEY2=secret2', 'app.yaml'
        ]
        result = common_utils._redact_secrets_values(argv)
        expected = [
            'sky', 'launch', '--secret', 'KEY1=<redacted>', '--secret',
            'KEY2=<redacted>', 'app.yaml'
        ]
        assert result == expected

    def test_mixed_secret_formats(self):
        """Test mixed --secret formats in one command."""
        argv = [
            'sky', 'launch', '--secret', 'KEY1=secret1',
            '--secret=KEY2=secret2', '--secret', 'KEY3', 'app.yaml'
        ]
        result = common_utils._redact_secrets_values(argv)
        expected = [
            'sky', 'launch', '--secret', 'KEY1=<redacted>',
            '--secret=KEY2=<redacted>', '--secret', 'KEY3', 'app.yaml'
        ]
        assert result == expected

    def test_no_secret_args(self):
        """Test command without --secret arguments."""
        argv = ['sky', 'launch', 'app.yaml']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', 'app.yaml']
        assert result == expected

    def test_value_with_equals(self):
        """Test secret value that contains equals signs."""
        argv = [
            'sky', 'launch', '--secret', 'KEY1=value=with=equals', 'app.yaml'
        ]
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret', 'KEY1=<redacted>', 'app.yaml']
        assert result == expected

    def test_value_with_equals_combined(self):
        """Test secret value with equals signs in combined format."""
        argv = ['sky', 'launch', '--secret=KEY2=value=with=equals', 'app.yaml']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret=KEY2=<redacted>', 'app.yaml']
        assert result == expected

    def test_empty_value(self):
        """Test secret argument with empty value."""
        argv = ['sky', 'launch', '--secret', 'KEY3=', 'app.yaml']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret', 'KEY3=<redacted>', 'app.yaml']
        assert result == expected

    def test_empty_value_combined(self):
        """Test secret argument with empty value in combined format."""
        argv = ['sky', 'launch', '--secret=KEY4=', 'app.yaml']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret=KEY4=<redacted>', 'app.yaml']
        assert result == expected

    def test_secret_at_end_without_value(self):
        """Test --secret at end of command without following argument."""
        argv = ['sky', 'launch', 'app.yaml', '--secret']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', 'app.yaml', '--secret']
        assert result == expected

    def test_env_not_redacted(self):
        """Test that --env arguments are not redacted by secrets function."""
        argv = [
            'sky', 'launch', '--env', 'PUBLIC_VAR=visible_value', 'app.yaml'
        ]
        result = common_utils._redact_secrets_values(argv)
        expected = [
            'sky', 'launch', '--env', 'PUBLIC_VAR=visible_value', 'app.yaml'
        ]
        assert result == expected

    def test_mixed_env_and_secret_args(self):
        """Test command with both --env and --secret arguments."""
        argv = [
            'sky', 'launch', '--env', 'PUBLIC_VAR=visible', '--secret',
            'SECRET_VAR=hidden', 'app.yaml'
        ]
        result = common_utils._redact_secrets_values(argv)
        expected = [
            'sky', 'launch', '--env', 'PUBLIC_VAR=visible', '--secret',
            'SECRET_VAR=<redacted>', 'app.yaml'
        ]
        assert result == expected

    def test_edge_cases(self):
        """Test edge cases that should not cause failures."""
        # Empty list
        assert common_utils._redact_secrets_values([]) == []

        # None input
        assert common_utils._redact_secrets_values(None) == []

        # Single element
        assert common_utils._redact_secrets_values(['sky']) == ['sky']

        # Non-string elements (should be preserved)
        argv = ['sky', 'launch', 123, '--secret', 'KEY=value']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', 123, '--secret', 'KEY=<redacted>']
        assert result == expected

        # Non-string after --secret
        argv = ['sky', 'launch', '--secret', 123]
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret', 123]
        assert result == expected

        # Very long strings (should not cause memory issues)
        long_value = 'x' * 10000
        argv = ['sky', 'launch', '--secret', f'KEY={long_value}']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret', 'KEY=<redacted>']
        assert result == expected

        # Unicode characters
        argv = ['sky', 'launch', '--secret', 'KEY=密码123']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret', 'KEY=<redacted>']
        assert result == expected

        # Special regex characters in key/value
        argv = ['sky', 'launch', '--secret', 'KEY[0]=value.*']
        result = common_utils._redact_secrets_values(argv)
        expected = ['sky', 'launch', '--secret', 'KEY[0]=<redacted>']
        assert result == expected

    @mock.patch('sky.utils.common_utils.re.sub')
    def test_error_handling_fallback(self, mock_re_sub):
        """Test that function returns original argv if redaction fails."""
        # Make re.sub raise an exception
        mock_re_sub.side_effect = Exception("Simulated regex error")

        argv = ['sky', 'launch', '--secret', 'KEY=value']
        result = common_utils._redact_secrets_values(argv)

        # Should return original argv when error occurs
        expected = ['sky', 'launch', '--secret', 'KEY=value']
        assert result == expected
