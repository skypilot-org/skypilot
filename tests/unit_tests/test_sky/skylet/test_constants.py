"""Tests for sky.skylet.constants."""
from sky.skylet import constants


def test_sky_python_binary_name_no_python_substring():
    """SKY_PYTHON_BINARY_NAME must not contain 'python' so that
    `pkill python` does not kill SkyPilot runtime processes."""
    assert 'python' not in constants.SKY_PYTHON_BINARY_NAME.lower(), (
        f'SKY_PYTHON_BINARY_NAME ({constants.SKY_PYTHON_BINARY_NAME!r}) '
        'must not contain "python" to protect against `pkill python`')


def test_sky_python_binary_name_length():
    """SKY_PYTHON_BINARY_NAME must fit in the Linux comm field (15 chars)."""
    assert len(constants.SKY_PYTHON_BINARY_NAME) <= 15, (
        f'SKY_PYTHON_BINARY_NAME ({constants.SKY_PYTHON_BINARY_NAME!r}) '
        'must be <= 15 chars to fit in Linux process comm field')


def test_uv_installation_creates_hardlink():
    """UV_INSTALLATION_COMMANDS must create a hardlink of the Python binary
    using SKY_PYTHON_BINARY_NAME."""
    assert constants.SKY_PYTHON_BINARY_NAME in (
        constants.UV_INSTALLATION_COMMANDS), (
            f'UV_INSTALLATION_COMMANDS must reference '
            f'SKY_PYTHON_BINARY_NAME ({constants.SKY_PYTHON_BINARY_NAME!r})')
    assert 'ln -f' in constants.UV_INSTALLATION_COMMANDS, (
        'UV_INSTALLATION_COMMANDS must create a hardlink (ln -f)')


def test_python_path_points_to_renamed_binary():
    """The python_path file should be written with the renamed binary path."""
    assert constants.SKY_PYTHON_BINARY_NAME in (
        constants.UV_INSTALLATION_COMMANDS), (
            'UV_INSTALLATION_COMMANDS must write the renamed binary path '
            'to SKY_PYTHON_PATH_FILE')
