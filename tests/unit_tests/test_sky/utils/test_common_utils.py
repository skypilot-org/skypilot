import asyncio
import contextvars
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


class TestClusterNameLooksLikeFilePath:

    def test_yaml_extension(self):
        assert common_utils.cluster_name_looks_like_file_path('job.yaml')

    def test_yml_extension(self):
        assert common_utils.cluster_name_looks_like_file_path('job.yml')

    def test_json_extension(self):
        assert common_utils.cluster_name_looks_like_file_path('config.json')

    def test_yaml_case_insensitive(self):
        assert common_utils.cluster_name_looks_like_file_path('job.YAML')

    def test_normal_name(self):
        assert not common_utils.cluster_name_looks_like_file_path('mycluster')

    def test_name_with_dot(self):
        assert not common_utils.cluster_name_looks_like_file_path('my.cluster')

    def test_name_with_hyphen(self):
        assert not common_utils.cluster_name_looks_like_file_path('my-cluster')

    def test_none(self):
        assert not common_utils.cluster_name_looks_like_file_path(None)

    def test_existing_file(self, tmp_path):
        test_file = tmp_path / 'somefile'
        test_file.write_text('content')
        assert common_utils.cluster_name_looks_like_file_path(str(test_file))


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


class TestGetPrettyEntrypointCmd:
    """Test entrypoint command formatting with shlex.join.

    Entrypoint commands are displayed in the dashboard for copy-pasting.
    shlex.join() ensures args with shell metacharacters are properly quoted.
    """

    @pytest.mark.parametrize(
        'argv, expected',
        [
            # Basic sky command: basename is extracted
            (['/usr/bin/sky', 'launch', 'app.yaml'], 'sky launch app.yaml'),
            # Semicolons are quoted
            ([
                '/usr/bin/sky', 'jobs', 'launch', '-n', 'test',
                'nvidia-smi; sleep 1000'
            ], "sky jobs launch -n test 'nvidia-smi; sleep 1000'"),
            # Pipes are quoted
            (['/usr/bin/sky', 'exec', 'cluster', 'cat file | grep pattern'
             ], "sky exec cluster 'cat file | grep pattern'"),
            # Ampersands are quoted
            (['/usr/bin/sky', 'exec', 'cluster', 'cmd1 && cmd2'
             ], "sky exec cluster 'cmd1 && cmd2'"),
            # Spaces are quoted
            (['/usr/bin/sky', 'launch', '--name', 'my cluster name'
             ], "sky launch --name 'my cluster name'"),
            # Redirection chars are quoted
            (['/usr/bin/sky', 'exec', 'cluster', 'echo hello > output.txt'
             ], "sky exec cluster 'echo hello > output.txt'"),
            # Non-sky basename is preserved as-is
            (['examples/app.py', '--flag', 'value'
             ], 'examples/app.py --flag value'),
            # Secrets are redacted; <redacted> contains
            # < and > so it gets quoted
            ([
                '/usr/bin/sky', 'launch', '--secret', 'HF_TOKEN=secret123',
                'app.yaml'
            ], "sky launch --secret 'HF_TOKEN=<redacted>' app.yaml"),
        ])
    def test_entrypoint_quoting(self, argv, expected):
        with mock.patch('sys.argv', argv):
            assert common_utils.get_pretty_entrypoint_cmd() == expected


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


class TestCheckWorkspaceNameIsValid:

    @pytest.mark.parametrize('name', [
        'a',
        'dev',
        'my-workspace',
        'my_workspace',
        'team-alpha-2',
        'a1',
        'default',
        'a' * 63,
        'abc-def_ghi-123',
    ])
    def test_valid_names(self, name):
        """Valid workspace names should pass validation."""
        common_utils.check_workspace_name_is_valid(name)

    @pytest.mark.parametrize('name', [
        '',
        '1workspace',
        '-workspace',
        '_workspace',
        'MyWorkspace',
        'my.workspace',
        'workspace-',
        'workspace_',
        'a' * 64,
        'ALLCAPS',
        'has space',
        'has@symbol',
        '123',
    ])
    def test_invalid_names(self, name):
        """Invalid workspace names should raise InvalidWorkspaceNameError."""
        with pytest.raises(exceptions.InvalidWorkspaceNameError):
            common_utils.check_workspace_name_is_valid(name)

    def test_none_name(self):
        """None workspace name should pass (no-op)."""
        common_utils.check_workspace_name_is_valid(None)

    def test_too_long_error_message(self):
        """Error message for too-long names should mention the length limit."""
        with pytest.raises(exceptions.InvalidWorkspaceNameError,
                           match='too long'):
            common_utils.check_workspace_name_is_valid('a' * 64)

    def test_invalid_chars_error_message(self):
        """Error message for invalid chars should mention allowed characters."""
        with pytest.raises(exceptions.InvalidWorkspaceNameError,
                           match='lowercase'):
            common_utils.check_workspace_name_is_valid('MyWorkspace')


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


class TestAtomicWriteText:
    """Tests for ``common_utils.atomic_write_text``."""

    def test_writes_content_with_default_mode(self, tmp_path):
        target = tmp_path / 'cluster-stanza'
        common_utils.atomic_write_text(str(target), 'Host my-cluster\n')

        assert target.read_text(encoding='utf-8') == 'Host my-cluster\n'
        # Default mode is 0o644.
        assert (target.stat().st_mode & 0o777) == 0o644

    def test_writes_content_with_explicit_mode(self, tmp_path):
        target = tmp_path / 'private-key'
        common_utils.atomic_write_text(str(target),
                                       'PRIVATE KEY CONTENT\n',
                                       mode=0o600)

        assert target.read_text(encoding='utf-8') == 'PRIVATE KEY CONTENT\n'
        assert (target.stat().st_mode & 0o777) == 0o600

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / 'existing'
        target.write_text('old content', encoding='utf-8')

        common_utils.atomic_write_text(str(target), 'new content')

        assert target.read_text(encoding='utf-8') == 'new content'

    def test_unicode_content(self, tmp_path):
        target = tmp_path / 'unicode'
        # Mixed ASCII + non-ASCII, including a 4-byte emoji.
        content = 'Host α-cluster 🚀\n  HostName 1.2.3.4\n'
        common_utils.atomic_write_text(str(target), content)

        assert target.read_text(encoding='utf-8') == content
        # On disk the encoded bytes should be UTF-8.
        assert target.read_bytes() == content.encode('utf-8')

    def test_swap_to_new_inode(self, tmp_path):
        """Atomic rename swaps the directory entry to a new inode.

        This is the property that makes the write torn-read-safe on
        shared filesystems: a reader holding an open fd on the old file
        keeps reading the old content until it closes; the new content
        appears at the path only after rename succeeds.
        """
        target = tmp_path / 'swap'
        target.write_text('original', encoding='utf-8')
        original_inode = target.stat().st_ino

        common_utils.atomic_write_text(str(target), 'replaced')

        new_inode = target.stat().st_ino
        # mkstemp+rename always allocates a fresh inode for the
        # replacement, so the inode must differ.
        assert new_inode != original_inode
        assert target.read_text(encoding='utf-8') == 'replaced'

    def test_no_temp_files_left_behind_on_success(self, tmp_path):
        target = tmp_path / 'clean'
        common_utils.atomic_write_text(str(target), 'done')

        # Only the target file should exist; no leftover .tmp sidecars.
        assert sorted(p.name for p in tmp_path.iterdir()) == ['clean']

    def test_temp_file_is_dotfile(self, tmp_path, monkeypatch):
        """Temp file basename must start with '.' so glob '*' skips it.

        SkyPilot's ssh config uses ``Include ~/.sky/generated/ssh/*``;
        a non-dot tmp file would be picked up while writes are in
        flight, breaking ssh config parsing.
        """
        observed_tmp_names = []
        real_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            observed_tmp_names.append(os.path.basename(path))
            return fd, path

        monkeypatch.setattr(common_utils.tempfile, 'mkstemp', spy_mkstemp)

        target = tmp_path / 'stanza'
        common_utils.atomic_write_text(str(target), 'data')

        assert observed_tmp_names, 'mkstemp should have been called once'
        for name in observed_tmp_names:
            assert name.startswith('.'), (
                f'tmp file {name!r} must start with "." so glob "*" '
                'does not pick it up mid-write')

    def test_cleanup_temp_on_chmod_failure(self, tmp_path, monkeypatch):
        """If chmod fails (or any post-write step fails), the partially
        written tmp file must be removed so it does not linger as a
        dotfile in the destination directory.
        """

        def boom(*args, **kwargs):
            raise PermissionError('simulated chmod failure')

        monkeypatch.setattr(common_utils.os, 'chmod', boom)

        target = tmp_path / 'never-renamed'
        with pytest.raises(PermissionError, match='simulated chmod failure'):
            common_utils.atomic_write_text(str(target), 'content')

        # Target file must NOT exist (rename never happened).
        assert not target.exists()
        # And no tmp leftovers either.
        assert list(tmp_path.iterdir()) == []

    def test_cleanup_temp_on_write_failure(self, tmp_path, monkeypatch):
        """If the write itself fails, the empty tmp file from mkstemp
        must still be cleaned up.
        """

        class BoomFile:

            def __init__(self, fd):
                self._fd = fd

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                # Close the underlying fd so the tmp file is not held
                # open when the cleanup tries to remove it.
                os.close(self._fd)
                return False

            def write(self, *_args, **_kwargs):
                raise IOError('disk full')

        real_fdopen = os.fdopen

        def fdopen_spy(fd, *args, **kwargs):
            del args, kwargs
            return BoomFile(fd)

        monkeypatch.setattr(common_utils.os, 'fdopen', fdopen_spy)

        target = tmp_path / 'never-written'
        with pytest.raises(IOError, match='disk full'):
            common_utils.atomic_write_text(str(target), 'content')

        assert not target.exists()
        assert list(tmp_path.iterdir()) == []
        # Sanity: real_fdopen still callable (no global state leaked).
        assert real_fdopen is os.fdopen or callable(real_fdopen)

    def test_target_in_cwd_with_no_dirname(self, tmp_path, monkeypatch):
        """When the path has no parent dir component, fall back to '.'.

        Otherwise tempfile.mkstemp(dir='') would raise.
        """
        monkeypatch.chdir(tmp_path)

        common_utils.atomic_write_text('relative-file', 'content')

        assert (tmp_path /
                'relative-file').read_text(encoding='utf-8') == 'content'

    def test_cleanup_temp_on_keyboard_interrupt(self, tmp_path, monkeypatch):
        """``KeyboardInterrupt`` (and any ``BaseException``) during the
        write must still trigger tmp cleanup -- otherwise a Ctrl-C
        during ``atomic_write_text`` would leak a hidden ``.<rand>.tmp``
        file into the destination directory, and successive interrupts
        could pile up dotfiles.

        Implementation note: the function uses ``try/finally`` with a
        success flag (rather than ``except Exception``) precisely so
        that ``BaseException`` subclasses do trigger cleanup.
        """

        def boom(*args, **kwargs):
            raise KeyboardInterrupt('user pressed Ctrl-C')

        monkeypatch.setattr(common_utils.os, 'chmod', boom)

        target = tmp_path / 'interrupted'
        with pytest.raises(KeyboardInterrupt):
            common_utils.atomic_write_text(str(target), 'content')

        assert not target.exists()
        # Crucially: no hidden .tmp file left behind in the directory.
        assert list(tmp_path.iterdir()) == []
