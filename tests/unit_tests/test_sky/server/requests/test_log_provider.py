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

    def test_oversized_log_capped_to_trailing_bytes(self, tmp_path):
        """An oversized source copies as header + exactly the trailing cap."""
        cap = 1024
        src_dir = tmp_path / 'request_logs'
        src_dir.mkdir()
        src = src_dir / 'req-big.log'
        content = b'A' * 500 + b'B' * cap
        src.write_bytes(content)
        dest = tmp_path / 'request.log'

        with mock.patch.object(log_provider, '_MAX_LOG_COPY_BYTES',
                               cap), mock.patch.object(log_provider,
                                                       'local_log_path',
                                                       return_value=src):
            provider = log_provider.LocalLogProvider()
            copied = provider.copy_log_file('req-big',
                                            log_provider.RequestLogType.REQUEST,
                                            dest)

        assert copied
        data = dest.read_bytes()
        header, _, body = data.partition(b'\n')
        # The header records the original size; the body is exactly the
        # trailing cap bytes (also bounds a source growing mid-copy).
        assert header.startswith(b'[sky debug-dump] Truncated log:')
        assert str(len(content)).encode() in header
        assert str(cap).encode() in header
        assert body == b'B' * cap

    def test_copy_at_cap_is_verbatim_with_mtime(self, tmp_path):
        """A source at exactly the cap copies byte-identically (no header)."""
        cap = 1024
        src_dir = tmp_path / 'request_logs'
        src_dir.mkdir()
        src = src_dir / 'req-at.log'
        src.write_bytes(b'x' * cap)
        dest = tmp_path / 'request.log'

        with mock.patch.object(log_provider, '_MAX_LOG_COPY_BYTES',
                               cap), mock.patch.object(log_provider,
                                                       'local_log_path',
                                                       return_value=src):
            provider = log_provider.LocalLogProvider()
            copied = provider.copy_log_file('req-at',
                                            log_provider.RequestLogType.REQUEST,
                                            dest)

        assert copied
        assert dest.read_bytes() == b'x' * cap
        assert dest.stat().st_mtime == src.stat().st_mtime


class TestCapLogFileInPlace:

    def test_noop_at_or_below_cap(self, tmp_path):
        path = tmp_path / 'small.log'
        path.write_bytes(b'small')
        log_provider.cap_log_file_in_place(path)
        assert path.read_bytes() == b'small'

    def test_noop_when_missing(self, tmp_path):
        # Best-effort: a missing file is not an error.
        log_provider.cap_log_file_in_place(tmp_path / 'nope.log')

    def test_truncates_oversized_file_to_trailing_cap(self, tmp_path):
        cap = 100
        path = tmp_path / 'big.log'
        path.write_bytes(b'C' * 400 + b'D' * cap)

        with mock.patch.object(log_provider, '_MAX_LOG_COPY_BYTES', cap):
            log_provider.cap_log_file_in_place(path)

        data = path.read_bytes()
        header, _, body = data.partition(b'\n')
        assert header.startswith(b'[sky debug-dump] Truncated log:')
        assert str(400 + cap).encode() in header
        assert body == b'D' * cap
        # No temp residue.
        assert list(tmp_path.iterdir()) == [path]

    def test_noop_on_already_capped_file(self, tmp_path):
        """A provider-capped file (header + cap bytes, slightly over the
        cap) must not be re-truncated."""
        cap = 100
        path = tmp_path / 'capped.log'
        original = (log_provider._truncation_header(500, cap).encode() +
                    b'E' * cap)
        path.write_bytes(original)
        assert len(original) > cap  # over the cap on purpose

        with mock.patch.object(log_provider, '_MAX_LOG_COPY_BYTES', cap):
            log_provider.cap_log_file_in_place(path)

        assert path.read_bytes() == original
