"""Unit tests for sky/utils/cluster_utils.py."""
from unittest import mock

import pytest

from sky.utils import cluster_utils


class TestConvertWindowsPathToWsl:
    """Tests for _convert_windows_path_to_wsl()."""

    def test_standard_windows_path(self):
        result = cluster_utils._convert_windows_path_to_wsl('C:\\Users\\test')
        assert result == '/mnt/c/Users/test'

    def test_windows_path_with_forward_slashes(self):
        result = cluster_utils._convert_windows_path_to_wsl('C:/Users/test')
        assert result == '/mnt/c/Users/test'

    def test_drive_letter_case_insensitive(self):
        result = cluster_utils._convert_windows_path_to_wsl('D:\\Data')
        assert result == '/mnt/d/Data'

    def test_root_drive(self):
        result = cluster_utils._convert_windows_path_to_wsl('C:\\')
        assert result == '/mnt/c/'

    def test_already_wsl_path(self):
        result = cluster_utils._convert_windows_path_to_wsl('/already/wsl/path')
        assert result == '/already/wsl/path'

    def test_relative_path(self):
        result = cluster_utils._convert_windows_path_to_wsl('relative/path')
        assert result == 'relative/path'

    def test_empty_string(self):
        result = cluster_utils._convert_windows_path_to_wsl('')
        assert result == ''


class TestConvertWslPathToWindows:
    """Tests for _convert_wsl_path_to_windows()."""

    def test_standard_wsl_path(self):
        result = cluster_utils._convert_wsl_path_to_windows('/mnt/c/Users/test')
        assert result == 'C:/Users/test'

    def test_different_drive(self):
        result = cluster_utils._convert_wsl_path_to_windows('/mnt/d/Data')
        assert result == 'D:/Data'

    def test_root_drive(self):
        result = cluster_utils._convert_wsl_path_to_windows('/mnt/c/')
        assert result == 'C:/'

    def test_non_mnt_path(self):
        result = cluster_utils._convert_wsl_path_to_windows('/home/user')
        assert result == '/home/user'

    def test_short_mnt_path(self):
        result = cluster_utils._convert_wsl_path_to_windows('/mnt/')
        assert result == '/mnt/'

    def test_empty_string(self):
        result = cluster_utils._convert_wsl_path_to_windows('')
        assert result == ''


class TestConvertProxyCommandForWindows:
    """Tests for SSHConfigHelper._convert_proxy_command_for_windows()."""

    def test_simple_proxy_command(self):
        cmd = 'ssh -W %h:%p user@host'
        result = cluster_utils.SSHConfigHelper._convert_proxy_command_for_windows(
            cmd)
        assert result == "wsl.exe bash -c 'ssh -W %h:%p user@host'"

    def test_proxy_command_with_double_quotes(self):
        cmd = 'ssh -o "StrictHostKeyChecking=no" -W %h:%p user@host'
        result = cluster_utils.SSHConfigHelper._convert_proxy_command_for_windows(
            cmd)
        assert result == (
            "wsl.exe bash -c 'ssh -o \"StrictHostKeyChecking=no\" -W %h:%p user@host'"
        )

    def test_proxy_command_with_single_quotes(self):
        cmd = "ssh -o 'StrictHostKeyChecking=no' -W %h:%p user@host"
        result = cluster_utils.SSHConfigHelper._convert_proxy_command_for_windows(
            cmd)
        # Single quotes should be escaped with '"'"'
        assert result == (
            "wsl.exe bash -c 'ssh -o '\"'\"'StrictHostKeyChecking=no'\"'\"' -W %h:%p user@host'"
        )

    def test_proxy_command_with_dollar_sign(self):
        # Dollar signs should not be expanded in single-quoted strings
        cmd = 'echo $HOME'
        result = cluster_utils.SSHConfigHelper._convert_proxy_command_for_windows(
            cmd)
        assert result == "wsl.exe bash -c 'echo $HOME'"

    def test_proxy_command_with_backticks(self):
        # Backticks should not be expanded in single-quoted strings
        cmd = 'echo `hostname`'
        result = cluster_utils.SSHConfigHelper._convert_proxy_command_for_windows(
            cmd)
        assert result == "wsl.exe bash -c 'echo `hostname`'"


class TestGetWslWindowsHome:
    """Tests for get_wsl_windows_home()."""

    def test_not_wsl_returns_none(self):
        # Clear the lru_cache to ensure fresh state
        cluster_utils.get_wsl_windows_home.cache_clear()
        with mock.patch('sky.utils.common_utils.is_wsl', return_value=False):
            result = cluster_utils.get_wsl_windows_home()
            assert result is None

    def test_wsl_with_userprofile_env(self):
        cluster_utils.get_wsl_windows_home.cache_clear()
        with mock.patch('sky.utils.common_utils.is_wsl', return_value=True):
            with mock.patch.dict('os.environ',
                                 {'USERPROFILE': 'C:\\Users\\testuser'}):
                with mock.patch('os.path.isdir', return_value=True):
                    result = cluster_utils.get_wsl_windows_home()
                    assert result == '/mnt/c/Users/testuser'

    def test_wsl_without_userprofile_uses_cmd(self):
        cluster_utils.get_wsl_windows_home.cache_clear()
        with mock.patch('sky.utils.common_utils.is_wsl', return_value=True):
            with mock.patch.dict('os.environ', {}, clear=True):
                with mock.patch(
                        'sky.utils.cluster_utils._get_windows_userprofile_via_cmd',
                        return_value='D:\\Users\\cmduser'):
                    with mock.patch('os.path.isdir', return_value=True):
                        result = cluster_utils.get_wsl_windows_home()
                        assert result == '/mnt/d/Users/cmduser'

    def test_wsl_with_invalid_home_returns_none(self):
        cluster_utils.get_wsl_windows_home.cache_clear()
        with mock.patch('sky.utils.common_utils.is_wsl', return_value=True):
            with mock.patch.dict('os.environ',
                                 {'USERPROFILE': 'C:\\Users\\testuser'}):
                with mock.patch('os.path.isdir', return_value=False):
                    result = cluster_utils.get_wsl_windows_home()
                    assert result is None

    def test_caching_behavior(self):
        """Test that the function result is cached."""
        cluster_utils.get_wsl_windows_home.cache_clear()
        call_count = 0

        def mock_is_wsl():
            nonlocal call_count
            call_count += 1
            return True

        with mock.patch('sky.utils.common_utils.is_wsl',
                        side_effect=mock_is_wsl):
            with mock.patch.dict('os.environ',
                                 {'USERPROFILE': 'C:\\Users\\cached'}):
                with mock.patch('os.path.isdir', return_value=True):
                    # Call multiple times
                    result1 = cluster_utils.get_wsl_windows_home()
                    result2 = cluster_utils.get_wsl_windows_home()
                    result3 = cluster_utils.get_wsl_windows_home()

                    # Should only call is_wsl once due to caching
                    assert call_count == 1
                    assert result1 == result2 == result3 == '/mnt/c/Users/cached'


class TestGetWindowsUserprofileViaCmd:
    """Tests for _get_windows_userprofile_via_cmd()."""

    def test_successful_cmd_call(self):
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = 'C:\\Users\\testuser\n'

        with mock.patch('subprocess.run', return_value=mock_result):
            result = cluster_utils._get_windows_userprofile_via_cmd()
            assert result == 'C:\\Users\\testuser'

    def test_failed_cmd_call(self):
        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stdout = ''

        with mock.patch('subprocess.run', return_value=mock_result):
            result = cluster_utils._get_windows_userprofile_via_cmd()
            assert result is None

    def test_unexpanded_variable(self):
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = '%USERPROFILE%\n'

        with mock.patch('subprocess.run', return_value=mock_result):
            result = cluster_utils._get_windows_userprofile_via_cmd()
            assert result is None

    def test_timeout_exception(self):
        import subprocess
        with mock.patch('subprocess.run',
                        side_effect=subprocess.TimeoutExpired(cmd='cmd.exe',
                                                              timeout=5)):
            result = cluster_utils._get_windows_userprofile_via_cmd()
            assert result is None

    def test_file_not_found(self):
        with mock.patch('subprocess.run', side_effect=FileNotFoundError()):
            result = cluster_utils._get_windows_userprofile_via_cmd()
            assert result is None
