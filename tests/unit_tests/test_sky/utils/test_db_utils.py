"""Unit tests for database utilities with SKY_RUNTIME_DIR environment variable."""
import os
from unittest import mock

import pytest

from sky.utils.db import db_utils


class TestSkyRuntimeDirEnvVar:
    """Test that db_utils correctly uses SKY_RUNTIME_DIR for database paths."""

    @pytest.mark.parametrize('use_custom_dir', [False, True])
    def test_get_engine_runtime_dir(self, tmp_path, monkeypatch,
                                    use_custom_dir):
        """Test get_engine respects SKY_RUNTIME_DIR when set."""
        monkeypatch.delenv('SKY_RUNTIME_DIR', raising=False)
        if use_custom_dir:
            monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            db_utils.get_engine(db_name='test')

            call_args = mock_create.call_args
            db_path = call_args[0][0]
            if use_custom_dir:
                expected_path = str(tmp_path / '.sky/test.db')
            else:
                expected_path = os.path.expanduser('~/.sky/test.db')
            assert expected_path in db_path

    def test_get_engine_async_custom_runtime_dir(self, tmp_path, monkeypatch):
        """Test async engine creation uses custom SKY_RUNTIME_DIR."""
        monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

        with mock.patch(
                'sqlalchemy.ext.asyncio.create_async_engine') as mock_create:
            db_utils.get_engine(db_name='test', async_engine=True)

            call_args = mock_create.call_args
            db_path = call_args[0][0]
            expected_path = str(tmp_path / '.sky/test.db')
            assert expected_path in db_path
