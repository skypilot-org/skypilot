"""Unit tests for attempt_skylet module."""
import importlib
import os
from unittest import mock

import pytest

from sky.skylet import attempt_skylet


@pytest.mark.parametrize('use_custom_dir', [False, True])
def test_version_file_runtime_dir(tmp_path, monkeypatch, use_custom_dir):
    """Test VERSION_FILE respects SKY_RUNTIME_DIR overrides."""
    monkeypatch.delenv('SKY_RUNTIME_DIR', raising=False)
    if use_custom_dir:
        monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

    # Mock subprocess and file operations to prevent actual execution
    with mock.patch('subprocess.run'), \
         mock.patch('builtins.open', mock.mock_open()), \
         mock.patch('os.path.exists', return_value=True):
        # RUNTIME_DIR is evaluated at import time; re-import for fresh values.
        importlib.reload(attempt_skylet)

        if use_custom_dir:
            expected_path = str(tmp_path / '.sky/skylet_version')
        else:
            expected_path = os.path.expanduser('~/.sky/skylet_version')
        assert attempt_skylet.VERSION_FILE == expected_path


def test_runtime_dir_variable_construction(tmp_path, monkeypatch):
    """Test RUNTIME_DIR variable is constructed correctly."""
    monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

    # Mock subprocess and file operations to prevent actual execution
    with mock.patch('subprocess.run'), \
         mock.patch('builtins.open', mock.mock_open()), \
         mock.patch('os.path.exists', return_value=True):
        importlib.reload(attempt_skylet)

        assert attempt_skylet.RUNTIME_DIR == str(tmp_path)
