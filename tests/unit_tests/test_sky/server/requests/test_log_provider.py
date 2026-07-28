"""Tests for sky.server.requests.log_provider."""
import pathlib
from unittest import mock

import pytest

from sky.server.requests import log_provider


class TestLocalLogPath:

    def test_request_log_path(self):
        path = log_provider.local_log_path('req-123',
                                           log_provider.RequestLogType.REQUEST)
        assert path.name == 'req-123.log'
        assert 'request_logs' in str(path)

    def test_debug_log_path(self):
        path = log_provider.local_log_path('req-123',
                                           log_provider.RequestLogType.DEBUG)
        assert path.name == 'req-123.log'
        assert 'request_debug_logs' in str(path)

    def test_path_traversal_rejected(self):
        for invalid_id in ('../sneaky', '../../etc/passwd', '/abs/path'):
            for log_type in (log_provider.RequestLogType.REQUEST,
                             log_provider.RequestLogType.DEBUG):
                with pytest.raises(ValueError):
                    log_provider.local_log_path(invalid_id, log_type)


class TestCopyLogFile:

    def test_copies_existing_request_log(self, tmp_path):
        src_dir = tmp_path / 'request_logs'
        src_dir.mkdir()
        (src_dir / 'req-1.log').write_text('log content')
        dest = tmp_path / 'request.log'

        with mock.patch.object(log_provider,
                               'local_log_path',
                               return_value=src_dir / 'req-1.log'):
            provider = log_provider.LocalLogProvider()
            copied = provider.copy_log_file('req-1',
                                            log_provider.RequestLogType.REQUEST,
                                            dest)

        assert copied
        assert dest.read_text() == 'log content'

    def test_missing_log_returns_false(self, tmp_path):
        dest = tmp_path / 'request.log'
        with mock.patch.object(log_provider,
                               'local_log_path',
                               return_value=tmp_path / 'nonexistent.log'):
            provider = log_provider.LocalLogProvider()
            copied = provider.copy_log_file('req-1',
                                            log_provider.RequestLogType.REQUEST,
                                            dest)

        assert not copied
        assert not dest.exists()

    def test_uses_real_paths_by_default(self, tmp_path, monkeypatch):
        # Verify the default path derivation end-to-end with a fake HOME.
        monkeypatch.setenv('HOME', str(tmp_path))
        src = pathlib.Path(
            log_provider.local_log_path('req-2',
                                        log_provider.RequestLogType.REQUEST))
        # The request log prefix is under ~, so with HOME patched it must
        # resolve inside tmp_path.
        assert str(src).startswith(str(tmp_path))
