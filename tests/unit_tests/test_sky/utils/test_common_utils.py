import asyncio
import contextvars
import json
import os
import tempfile
from unittest import mock

import pytest

from sky import exceptions
from sky import models
from sky.utils import common_utils
from sky.utils import context

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


@pytest.mark.asyncio
async def test_set_request_context_coroutine_is_context_safe():
    original_user = common_utils.get_current_user()

    async def run_in_coroutine():
        context.initialize()
        common_utils.set_request_context(client_entrypoint='entry',
                                         client_command='cmd',
                                         using_remote_api_server=True,
                                         user=models.User(id='request-user',
                                                          name='request-user'),
                                         request_id='dummy')
        return common_utils.get_current_user()

    user = await asyncio.create_task(run_in_coroutine())
    assert user.id == 'request-user'
    assert user.name == 'request-user'
    # Process-scope var should not be unchanged
    assert common_utils.get_current_user().name == original_user.name
    assert common_utils.get_current_user().id == original_user.id


class TestNormalizeServerUrlForHash:

    def test_full_url_with_scheme(self):
        """Test URL with http scheme."""
        result = common_utils._normalize_server_url_for_hash(
            'http://example.com')
        assert result == 'http://example.com'

    def test_full_url_with_https_scheme(self):
        """Test URL with https scheme."""
        result = common_utils._normalize_server_url_for_hash(
            'https://api.example.com')
        assert result == 'https://api.example.com'

    def test_url_with_trailing_slash(self):
        """Test URL with trailing slash is stripped."""
        result = common_utils._normalize_server_url_for_hash(
            'http://example.com/')
        assert result == 'http://example.com'

    def test_url_with_path(self):
        """Test URL with path - only netloc is used."""
        result = common_utils._normalize_server_url_for_hash(
            'http://example.com/api/v1')
        assert result == 'http://example.com'

    def test_url_without_scheme(self):
        """Test URL without scheme defaults to http."""
        result = common_utils._normalize_server_url_for_hash('example.com')
        assert result == 'http://example.com'

    def test_url_with_port(self):
        """Test URL with port number."""
        result = common_utils._normalize_server_url_for_hash(
            'http://example.com:8080')
        assert result == 'http://example.com:8080'

    def test_url_with_path_only(self):
        """Test URL with path but no netloc uses path as netloc."""
        result = common_utils._normalize_server_url_for_hash('/some/path')
        assert result == 'http:///some/path'

    def test_none_input(self):
        """Test None input returns None."""
        assert common_utils._normalize_server_url_for_hash(None) is None

    def test_empty_string(self):
        """Test empty string returns None."""
        assert common_utils._normalize_server_url_for_hash('') is None

    @mock.patch('sky.utils.common_utils.urllib.parse.urlparse')
    def test_urlparse_exception_handled(self, mock_urlparse):
        """Test that exceptions from urlparse are caught and handled."""
        # Make urlparse raise an exception to test the exception handler (line 169)
        mock_urlparse.side_effect = Exception('URL parse error')
        result = common_utils._normalize_server_url_for_hash(
            'http://example.com')
        assert result is None


class TestReadServerUserHashMapping:

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('os.path.exists')
    def test_read_valid_mapping(self, mock_exists, mock_open):
        """Test reading valid JSON mapping."""
        mock_exists.return_value = True
        mock_open.return_value.__enter__().read.return_value = (
            '{"http://server1.com": "hash1", "http://server2.com": "hash2"}')
        result = common_utils._read_server_user_hash_mapping()
        assert result == {
            'http://server1.com': 'hash1',
            'http://server2.com': 'hash2'
        }

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('os.path.exists')
    def test_read_mapping_with_numeric_values(self, mock_exists, mock_open):
        """Test reading mapping with numeric values converted to strings."""
        mock_exists.return_value = True
        mock_open.return_value.__enter__().read.return_value = (
            '{"http://server1.com": 12345}')
        result = common_utils._read_server_user_hash_mapping()
        assert result == {'http://server1.com': '12345'}

    @mock.patch('builtins.open', side_effect=FileNotFoundError)
    def test_file_not_found_returns_empty_dict(self, mock_open):
        """Test file not found returns empty dict."""
        result = common_utils._read_server_user_hash_mapping()
        assert result == {}

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('os.path.exists')
    def test_invalid_json_returns_empty_dict(self, mock_exists, mock_open):
        """Test invalid JSON returns empty dict."""
        mock_exists.return_value = True
        mock_open.return_value.__enter__().read.return_value = 'invalid json'
        result = common_utils._read_server_user_hash_mapping()
        assert result == {}

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('os.path.exists')
    def test_non_dict_json_returns_empty_dict(self, mock_exists, mock_open):
        """Test non-dict JSON returns empty dict."""
        mock_exists.return_value = True
        mock_open.return_value.__enter__(
        ).read.return_value = '["not", "a", "dict"]'
        result = common_utils._read_server_user_hash_mapping()
        assert result == {}

    @mock.patch('builtins.open', side_effect=OSError('Permission denied'))
    def test_os_error_returns_empty_dict(self, mock_open):
        """Test OSError returns empty dict."""
        result = common_utils._read_server_user_hash_mapping()
        assert result == {}


class TestWriteAtomic:

    def test_write_atomic_creates_file(self):
        """Test atomic write creates file successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, 'test_file.txt')
            content = 'test content'
            common_utils._write_atomic(file_path, content)

            assert os.path.exists(file_path)
            with open(file_path, 'r', encoding='utf-8') as f:
                assert f.read() == content

    def test_write_atomic_creates_directory(self):
        """Test atomic write creates parent directory if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, 'subdir', 'test_file.txt')
            content = 'test content'
            common_utils._write_atomic(file_path, content)

            assert os.path.exists(file_path)
            with open(file_path, 'r', encoding='utf-8') as f:
                assert f.read() == content

    def test_write_atomic_overwrites_existing(self):
        """Test atomic write overwrites existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, 'test_file.txt')
            # Create initial file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('old content')

            common_utils._write_atomic(file_path, 'new content')

            with open(file_path, 'r', encoding='utf-8') as f:
                assert f.read() == 'new content'


class TestWriteServerUserHashMapping:

    @mock.patch('sky.utils.common_utils._write_atomic')
    def test_write_mapping_calls_write_atomic(self, mock_write_atomic):
        """Test write mapping calls _write_atomic with JSON."""
        mapping = {'http://server1.com': 'hash1', 'http://server2.com': 'hash2'}
        common_utils._write_server_user_hash_mapping(mapping)

        mock_write_atomic.assert_called_once()
        call_args = mock_write_atomic.call_args
        assert call_args[0][0] == common_utils.SERVER_USER_HASH_FILE
        written_data = json.loads(call_args[0][1])
        assert written_data == mapping


class TestGetServerUserHash:

    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_get_cached_hash(self, mock_normalize, mock_read):
        """Test getting cached hash for server URL."""
        mock_normalize.return_value = 'http://server.com'
        mock_read.return_value = {'http://server.com': 'cached_hash'}
        result = common_utils.get_server_user_hash('http://server.com')
        assert result == 'cached_hash'

    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_get_hash_not_found(self, mock_normalize, mock_read):
        """Test getting hash when not in cache."""
        mock_normalize.return_value = 'http://server.com'
        mock_read.return_value = {}
        result = common_utils.get_server_user_hash('http://server.com')
        assert result is None

    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_get_hash_with_none_url(self, mock_normalize, mock_read):
        """Test getting hash with None URL."""
        mock_normalize.return_value = None
        result = common_utils.get_server_user_hash(None)
        assert result is None
        mock_read.assert_not_called()

    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_get_hash_url_normalization(self, mock_normalize, mock_read):
        """Test that URL is normalized before lookup."""
        mock_normalize.return_value = 'http://server.com'
        mock_read.return_value = {'http://server.com': 'hash1'}
        result = common_utils.get_server_user_hash('http://server.com/')
        assert result == 'hash1'
        mock_normalize.assert_called_once_with('http://server.com/')


class TestSaveServerUserHash:

    @mock.patch('sky.utils.common_utils._write_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_save_new_hash(self, mock_normalize, mock_read, mock_write):
        """Test saving new hash for server URL."""
        mock_normalize.return_value = 'http://server.com'
        mock_read.return_value = {}
        # Use valid hash format (alphanumeric with hyphens, no underscores)
        result = common_utils.save_server_user_hash('http://server.com',
                                                    'newhash123')
        assert result == 'newhash123'
        mock_write.assert_called_once()
        call_args = mock_write.call_args[0][0]
        assert call_args == {'http://server.com': 'newhash123'}

    @mock.patch('sky.utils.common_utils._write_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_save_hash_does_not_overwrite(self, mock_normalize, mock_read,
                                          mock_write):
        """Test saving hash does not overwrite existing."""
        mock_normalize.return_value = 'http://server.com'
        mock_read.return_value = {'http://server.com': 'existinghash123'}
        # Use valid hash format (alphanumeric with hyphens, no underscores)
        result = common_utils.save_server_user_hash('http://server.com',
                                                    'newhash456')
        assert result == 'existinghash123'
        # Should not write since hash already exists
        mock_write.assert_not_called()

    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_save_invalid_hash_returns_none(self, mock_normalize, mock_read):
        """Test saving invalid hash returns None."""
        mock_normalize.return_value = 'http://server.com'
        result = common_utils.save_server_user_hash('http://server.com', None)
        assert result is None
        result = common_utils.save_server_user_hash('http://server.com', '')
        assert result is None
        result = common_utils.save_server_user_hash('http://server.com',
                                                    '@invalid')
        assert result is None

    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_save_hash_with_invalid_url(self, mock_normalize, mock_read):
        """Test saving hash with invalid URL returns None."""
        mock_normalize.return_value = None
        result = common_utils.save_server_user_hash('invalid', 'validhash123')
        assert result is None
        # Verify normalization was called
        mock_normalize.assert_called_once_with('invalid')
        # Verify read was not called since normalization returned None
        mock_read.assert_not_called()

    def test_save_hash_with_url_that_normalizes_to_none(self):
        """Test saving hash with URL that actually normalizes to None."""
        # Use an empty string which will normalize to None
        result = common_utils.save_server_user_hash('', 'validhash123')
        assert result is None

    @mock.patch('sky.utils.common_utils._write_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_save_hash_preserves_existing_mappings(self, mock_normalize,
                                                   mock_read, mock_write):
        """Test saving hash preserves existing mappings."""
        mock_normalize.return_value = 'http://server2.com'
        mock_read.return_value = {'http://server1.com': 'hash1'}
        result = common_utils.save_server_user_hash('http://server2.com',
                                                    'hash2')
        assert result == 'hash2'
        call_args = mock_write.call_args[0][0]
        assert call_args == {
            'http://server1.com': 'hash1',
            'http://server2.com': 'hash2'
        }

    @mock.patch('sky.utils.common_utils._write_server_user_hash_mapping',
                side_effect=OSError('Write failed'))
    @mock.patch('sky.utils.common_utils._read_server_user_hash_mapping')
    @mock.patch('sky.utils.common_utils._normalize_server_url_for_hash')
    def test_save_hash_handles_write_error(self, mock_normalize, mock_read,
                                           mock_write):
        """Test saving hash handles write errors gracefully."""
        mock_normalize.return_value = 'http://server.com'
        mock_read.return_value = {}
        # Should not raise exception
        result = common_utils.save_server_user_hash('http://server.com',
                                                    'hash1')
        assert result == 'hash1'


class TestGetUsageUserId:

    @mock.patch('sky.utils.common_utils.get_user_hash')
    @mock.patch('sky.utils.common_utils.get_server_user_hash')
    def test_with_both_hashes(self, mock_get_server_hash, mock_get_user_hash):
        """Test combining client and server hashes."""
        mock_get_user_hash.return_value = 'client_hash'
        mock_get_server_hash.return_value = 'server_hash'
        result = common_utils.get_usage_user_id(server_url='http://server.com')
        assert result == 'client_hash-server_hash'

    @mock.patch('sky.utils.common_utils.get_user_hash')
    @mock.patch('sky.utils.common_utils.get_server_user_hash')
    def test_with_explicit_hashes(self, mock_get_server_hash,
                                  mock_get_user_hash):
        """Test with explicitly provided hashes."""
        mock_get_user_hash.return_value = 'fallback-client'
        # Use valid hash format (alphanumeric with hyphens, no underscores)
        result = common_utils.get_usage_user_id(
            client_user_hash='explicit-client',
            server_user_hash='explicit-server')
        assert result == 'explicit-client-explicit-server'
        # Should not call get_user_hash or get_server_user_hash
        mock_get_user_hash.assert_not_called()
        mock_get_server_hash.assert_not_called()

    @mock.patch('sky.utils.common_utils.get_user_hash')
    @mock.patch('sky.utils.common_utils.get_server_user_hash')
    def test_without_server_hash(self, mock_get_server_hash,
                                 mock_get_user_hash):
        """Test without server hash returns only client hash."""
        mock_get_user_hash.return_value = 'client_hash'
        mock_get_server_hash.return_value = None
        result = common_utils.get_usage_user_id(server_url='http://server.com')
        assert result == 'client_hash'

    @mock.patch('sky.utils.common_utils.get_user_hash')
    def test_without_server_url(self, mock_get_user_hash):
        """Test without server URL uses only client hash."""
        mock_get_user_hash.return_value = 'client_hash'
        result = common_utils.get_usage_user_id()
        assert result == 'client_hash'

    @mock.patch('sky.utils.common_utils.get_user_hash')
    @mock.patch('sky.utils.common_utils.get_server_user_hash')
    def test_invalid_client_hash_fallback(self, mock_get_server_hash,
                                          mock_get_user_hash):
        """Test invalid client hash falls back to get_user_hash."""
        mock_get_user_hash.return_value = 'valid_client'
        mock_get_server_hash.return_value = None
        result = common_utils.get_usage_user_id(client_user_hash='@invalid')
        assert result == 'valid_client'
        mock_get_user_hash.assert_called_once()

    @mock.patch('sky.utils.common_utils.get_user_hash')
    @mock.patch('sky.utils.common_utils.get_server_user_hash')
    def test_invalid_server_hash_fallback(self, mock_get_server_hash,
                                          mock_get_user_hash):
        """Test invalid server hash falls back to lookup."""
        mock_get_user_hash.return_value = 'client_hash'
        mock_get_server_hash.return_value = 'valid_server'
        result = common_utils.get_usage_user_id(server_user_hash='@invalid',
                                                server_url='http://server.com')
        assert result == 'client_hash-valid_server'
        mock_get_server_hash.assert_called_once_with('http://server.com')

    @mock.patch('sky.utils.common_utils.get_user_hash')
    @mock.patch('sky.utils.common_utils.get_server_user_hash')
    def test_none_client_hash_fallback(self, mock_get_server_hash,
                                       mock_get_user_hash):
        """Test None client hash falls back to get_user_hash."""
        mock_get_user_hash.return_value = 'fallback_client'
        mock_get_server_hash.return_value = None
        result = common_utils.get_usage_user_id(client_user_hash=None)
        assert result == 'fallback_client'
        mock_get_user_hash.assert_called_once()

    @mock.patch('sky.utils.common_utils.get_user_hash')
    @mock.patch('sky.utils.common_utils.get_server_user_hash')
    def test_none_server_hash_fallback(self, mock_get_server_hash,
                                       mock_get_user_hash):
        """Test None server hash falls back to lookup."""
        mock_get_user_hash.return_value = 'client_hash'
        mock_get_server_hash.return_value = 'lookup_server'
        result = common_utils.get_usage_user_id(server_user_hash=None,
                                                server_url='http://server.com')
        assert result == 'client_hash-lookup_server'
        mock_get_server_hash.assert_called_once_with('http://server.com')

    @mock.patch('sky.utils.common_utils.get_user_hash')
    @mock.patch('sky.utils.common_utils.get_server_user_hash')
    def test_service_account_format_hash(self, mock_get_server_hash,
                                         mock_get_user_hash):
        """Test with service account format hash."""
        mock_get_user_hash.return_value = 'sa-abc123-token-xyz'
        mock_get_server_hash.return_value = 'sa-def456-token-uvw'
        result = common_utils.get_usage_user_id(server_url='http://server.com')
        assert result == 'sa-abc123-token-xyz-sa-def456-token-uvw'
