"""Tests for sky.server.versions module."""

from unittest import mock

import pytest

from sky import exceptions
from sky.server import constants
from sky.server import versions


def test_check_version_compatibility_compatible_versions():
    """Test check_version_compatibility with compatible versions."""
    headers = {
        constants.API_VERSION_HEADER: '1',
        constants.VERSION_HEADER: '1.0.0'
    }

    with mock.patch.object(constants, 'MIN_COMPATIBLE_API_VERSION', 1):
        result = versions.check_compatibility_at_server(headers)

    assert result is not None
    assert result.api_version == 1
    assert result.version == '1.0.0'
    assert result.error is None


def test_check_version_compatibility_missing_headers():
    """Test check_version_compatibility with missing headers."""
    # Test missing API version header - should return None for backward compatibility
    headers = {constants.VERSION_HEADER: '1.0.0'}
    result = versions.check_compatibility_at_server(headers)
    assert result is None

    # Test missing version header - should return None for backward compatibility
    headers = {constants.API_VERSION_HEADER: '1'}
    result = versions.check_compatibility_at_server(headers)
    assert result is None


def test_check_version_compatibility_headers_with_none_values():
    """Test check_version_compatibility with headers that contain None values."""
    # Test with headers where values are None
    headers = {
        constants.API_VERSION_HEADER: None,
        constants.VERSION_HEADER: '1.0.0'
    }
    result = versions.check_compatibility_at_server(headers)
    assert result is None

    headers = {
        constants.API_VERSION_HEADER: '1',
        constants.VERSION_HEADER: None
    }
    result = versions.check_compatibility_at_server(headers)
    assert result is None


def test_check_version_compatibility_invalid_api_version():
    """Test check_version_compatibility with invalid API version."""
    headers = {
        constants.API_VERSION_HEADER: 'invalid',
        constants.VERSION_HEADER: '1.0.0'
    }

    with pytest.raises(ValueError, match='is not a valid API version'):
        versions.check_compatibility_at_server(headers)


def test_check_version_compatibility_incompatible_client():
    """Test check_version_compatibility with incompatible client."""
    headers = {
        constants.API_VERSION_HEADER: '1',
        constants.VERSION_HEADER: '0.9.0'
    }

    with mock.patch.object(constants, 'MIN_COMPATIBLE_API_VERSION', 2), \
         mock.patch.object(constants, 'MIN_COMPATIBLE_VERSION', '1.0.0'), \
         mock.patch('sky.server.versions.get_local_readable_version',
                    return_value='1.0.0'), \
         mock.patch('sky.server.versions.install_version_command',
                    return_value='pip install skypilot==0.9.0'):

        result = versions.check_compatibility_at_server(headers)

    assert result is not None
    assert result.api_version == 1
    assert result.version == '0.9.0'
    assert result.error is not None
    assert 'client version is too old' in result.error


def test_check_version_compatibility_incompatible_server():
    """Test check_version_compatibility with incompatible server."""
    headers = {
        constants.API_VERSION_HEADER: '1',
        constants.VERSION_HEADER: '0.9.0'
    }

    with mock.patch.object(constants, 'MIN_COMPATIBLE_API_VERSION', 2), \
         mock.patch.object(constants, 'MIN_COMPATIBLE_VERSION', '1.0.0'), \
         mock.patch('sky.server.versions.get_local_readable_version',
                    return_value='1.0.0'), \
         mock.patch('sky.server.versions.parse_readable_version',
                    return_value=('0.9.0', None)), \
         mock.patch('sky.server.versions.install_version_command',
                    return_value='pip install skypilot==0.9.0'):

        result = versions.check_compatibility_at_client(headers)

    assert result is not None
    assert result.api_version == 1
    assert result.version == '0.9.0'
    assert result.error is not None
    assert 'server version is too old' in result.error


def test_get_local_readable_version_dev():
    """Test get_local_readable_version with dev version."""
    with mock.patch('sky.__version__', versions.DEV_VERSION), \
         mock.patch('sky.__commit__', 'abc123'):

        result = versions.get_local_readable_version()

    assert result == f'{versions.DEV_VERSION} (commit: abc123)'


def test_get_local_readable_version_regular():
    """Test get_local_readable_version with regular version."""
    with mock.patch('sky.__version__', '1.2.3'):
        result = versions.get_local_readable_version()

    assert result == '1.2.3'


def test_parse_readable_version_with_commit():
    """Test parse_readable_version with commit info."""
    version = '1.0.0-dev0 (commit: abc123)'
    base_version, commit = versions.parse_readable_version(version)

    assert base_version == '1.0.0-dev0'
    assert commit == 'abc123'


def test_parse_readable_version_without_commit():
    """Test parse_readable_version without commit info."""
    version = '1.2.3'
    base_version, commit = versions.parse_readable_version(version)

    assert base_version == '1.2.3'
    assert commit is None


def test_parse_readable_version_edge_cases():
    """Test parse_readable_version with edge cases."""
    # Version with special characters but no commit
    version = '1.2.3-alpha1'
    base_version, commit = versions.parse_readable_version(version)
    assert base_version == '1.2.3-alpha1'
    assert commit is None

    # Empty version
    version = ''
    base_version, commit = versions.parse_readable_version(version)
    assert base_version == ''
    assert commit is None


def test_install_version_command_dev_with_commit():
    """Test install_version_command with dev version and commit."""
    result = versions.install_version_command(versions.DEV_VERSION, 'abc123')
    expected = 'pip install git+https://github.com/skypilot-org/skypilot@abc123'
    assert result == expected


def test_install_version_command_dev_without_commit():
    """Test install_version_command with dev version but no commit."""
    result = versions.install_version_command(versions.DEV_VERSION)
    # Should fall through to regular version case since no commit provided
    expected = f'pip install -U "skypilot=={versions.DEV_VERSION}"'
    assert result == expected


def test_install_version_command_nightly():
    """Test install_version_command with nightly version."""
    result = versions.install_version_command('1.2.3-dev1')
    expected = 'pip install -U "skypilot-nightly==1.2.3-dev1"'
    assert result == expected


def test_install_version_command_regular():
    """Test install_version_command with regular version."""
    result = versions.install_version_command('1.2.3')
    expected = 'pip install -U "skypilot==1.2.3"'
    assert result == expected


def test_remind_minor_version_upgrade_should_remind():
    """Test _remind_minor_version_upgrade when upgrade is needed."""
    # Reset the global flag
    versions._reminded_for_minor_version_upgrade = False

    with mock.patch('sky.__version__', '1.0.0'), \
         mock.patch('sky.server.versions.logger') as mock_logger, \
         mock.patch('sky.server.versions.install_version_command',
                    return_value='pip install skypilot==1.1.0'):

        versions._remind_minor_version_upgrade('1.1.0')

    mock_logger.warning.assert_called_once()
    assert versions._reminded_for_minor_version_upgrade is True


def test_remind_minor_version_upgrade_skip_dev_versions():
    """Test _remind_minor_version_upgrade skips dev versions."""
    versions._reminded_for_minor_version_upgrade = False

    with mock.patch('sky.__version__', '1.0.0-dev0'), \
         mock.patch('sky.server.versions.logger') as mock_logger:

        versions._remind_minor_version_upgrade('1.1.0')

    mock_logger.warning.assert_not_called()
    assert versions._reminded_for_minor_version_upgrade is False


def test_remind_minor_version_upgrade_skip_when_already_reminded():
    """Test _remind_minor_version_upgrade skips when already reminded."""
    versions._reminded_for_minor_version_upgrade = True

    with mock.patch('sky.__version__', '1.0.0'), \
         mock.patch('sky.server.versions.logger') as mock_logger:

        versions._remind_minor_version_upgrade('1.1.0')

    mock_logger.warning.assert_not_called()


def test_remind_minor_version_upgrade_skip_same_version():
    """Test _remind_minor_version_upgrade skips when versions are same."""
    versions._reminded_for_minor_version_upgrade = False

    with mock.patch('sky.__version__', '1.1.0'), \
         mock.patch('sky.server.versions.logger') as mock_logger:

        versions._remind_minor_version_upgrade('1.1.0')

    mock_logger.warning.assert_not_called()


def test_remind_minor_version_upgrade_skip_newer_local():
    """Test _remind_minor_version_upgrade skips when local is newer."""
    versions._reminded_for_minor_version_upgrade = False

    with mock.patch('sky.__version__', '1.2.0'), \
         mock.patch('sky.server.versions.logger') as mock_logger:

        versions._remind_minor_version_upgrade('1.1.0')

    mock_logger.warning.assert_not_called()


def test_version_info_named_tuple():
    """Test VersionInfo NamedTuple creation and access."""
    # Test with all fields
    version_info = versions.VersionInfo(api_version=1,
                                        version='1.2.3',
                                        error='test error')
    assert version_info.api_version == 1
    assert version_info.version == '1.2.3'
    assert version_info.error == 'test error'

    # Test with default error field
    version_info = versions.VersionInfo(api_version=2, version='1.3.0')
    assert version_info.api_version == 2
    assert version_info.version == '1.3.0'
    assert version_info.error is None


def test_minimal_api_version_decorator_no_remote_version():
    """Test minimal_api_version decorator when remote version is None."""

    @versions.minimal_api_version(2)
    def test_function(arg1, arg2=None):
        return f"called with {arg1}, {arg2}"

    # Mock get_remote_api_version to return None
    with mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=None):
        result = test_function("test", arg2="value")
        assert result == "called with test, value"


def test_minimal_api_version_decorator_compatible_version():
    """Test minimal_api_version decorator when remote version is compatible."""

    @versions.minimal_api_version(2)
    def test_function(arg1):
        return f"success: {arg1}"

    # Mock get_remote_api_version to return a compatible version
    with mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=3):
        result = test_function("test")
        assert result == "success: test"

    # Test with exact minimum version
    with mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=2):
        result = test_function("test")
        assert result == "success: test"


def test_minimal_api_version_decorator_incompatible_dev_version():
    """Test minimal_api_version decorator with incompatible version and dev client."""

    @versions.minimal_api_version(3)
    def test_function():
        return "should not be called"

    with mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=2), \
         mock.patch('sky.server.versions.get_remote_version',
                    return_value='1.0.0-dev0'), \
         mock.patch('sky.__version__', '1.1.0-dev0'), \
         mock.patch('sky.utils.ux_utils.print_exception_no_traceback'):

        with pytest.raises(exceptions.APINotSupportedError) as exc_info:
            test_function()

        error_message = str(exc_info.value)
        assert "test_function" in error_message
        assert "Please upgrade the remote server." in error_message
        assert "1.0.0-dev0" in error_message


def test_minimal_api_version_decorator_incompatible_release_version():
    """Test minimal_api_version decorator with incompatible version and release client."""

    @versions.minimal_api_version(3)
    def test_function(param1, param2):
        return f"{param1} {param2}"

    with mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=1), \
         mock.patch('sky.server.versions.get_remote_version',
                    return_value='0.9.0'), \
         mock.patch('sky.__version__', '1.0.0'), \
         mock.patch('sky.utils.ux_utils.print_exception_no_traceback'):

        with pytest.raises(exceptions.APINotSupportedError) as exc_info:
            test_function("arg1", "arg2")

        error_message = str(exc_info.value)
        assert "test_function" in error_message
        assert "Upgrade the remote server to 1.0.0" in error_message
        assert "0.9.0" in error_message


def test_minimal_api_version_decorator_preserves_function_metadata():
    """Test that minimal_api_version decorator preserves function metadata."""

    @versions.minimal_api_version(1)
    def test_function_with_docstring(arg1: str, arg2: int = 42) -> str:
        """This is a test function with docstring."""
        return f"{arg1}: {arg2}"

    # Check that function metadata is preserved
    assert test_function_with_docstring.__name__ == "test_function_with_docstring"
    assert test_function_with_docstring.__doc__ == "This is a test function with docstring."

    # Check that the function still works normally when version is compatible
    with mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=1):
        result = test_function_with_docstring("test")
        assert result == "test: 42"


def test_minimal_api_version_decorator_edge_case_zero_min_version():
    """Test minimal_api_version decorator with minimum version 0."""

    @versions.minimal_api_version(0)
    def test_function():
        return "called"

    # Any version should be compatible with min version 0
    with mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=0):
        result = test_function()
        assert result == "called"

    with mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=1):
        result = test_function()
        assert result == "called"


def test_minimal_api_version_decorator_function_with_kwargs():
    """Test minimal_api_version decorator with function that has *args and **kwargs."""

    @versions.minimal_api_version(1)
    def test_function(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    with mock.patch('sky.server.versions.get_remote_api_version',
                    return_value=1):
        result = test_function("arg1", "arg2", key1="value1", key2="value2")
        assert result["args"] == ("arg1", "arg2")
        assert result["kwargs"] == {"key1": "value1", "key2": "value2"}
