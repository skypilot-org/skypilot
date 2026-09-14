"""Tests for sky.server.requests.log_provider."""
import builtins
import pathlib
import threading
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


class TestCopyLogStopEvent:
    """stop_event makes the default local copy cooperatively cancellable."""

    def _write_src(self, tmp_path, size_bytes=64):
        src_dir = tmp_path / 'request_logs'
        src_dir.mkdir(exist_ok=True)
        src = src_dir / 'req-1.log'
        src.write_bytes(b'x' * size_bytes)
        return src

    @staticmethod
    def _open_that_stops_on_first_write(stop_event):
        """Patch target for builtins.open: the destination's first write
        flips the stop event, so the copy is deterministically abandoned
        mid-flight (after the destination file was created). File objects
        do not allow attribute overrides, hence the wrapper class."""
        real_open = builtins.open

        def _open(file, *args, **kwargs):
            f = real_open(file, *args, **kwargs)
            if getattr(f, 'mode', '') == 'wb':

                class _StopOnWrite:
                    """Wraps the dest file; the first write flips the stop
                    event (the copy is abandoned mid-flight)."""

                    def __init__(self, inner):
                        self._inner = inner

                    def write(self, data):
                        stop_event.set()
                        return self._inner.write(data)

                    def __enter__(self):
                        return self

                    def __exit__(self, *exc):
                        return self._inner.__exit__(*exc)

                return _StopOnWrite(f)
            return f

        return _open

    def test_stop_preset_returns_false_no_dest(self, tmp_path):
        src = self._write_src(tmp_path)
        dest = tmp_path / 'request.log'
        stop_event = threading.Event()
        stop_event.set()  # caller already abandoned the copy
        with mock.patch.object(log_provider, 'local_log_path',
                               return_value=src):
            provider = log_provider.LocalLogProvider()
            copied = provider.copy_log_file('req-1',
                                            log_provider.RequestLogType.REQUEST,
                                            dest,
                                            stop_event=stop_event)
        assert not copied
        assert not dest.exists()

    def test_stop_mid_copy_unlinks_partial_dest(self, tmp_path):
        src = self._write_src(tmp_path)
        dest = tmp_path / 'request.log'
        stop_event = threading.Event()
        with mock.patch(
                'builtins.open',
                side_effect=self._open_that_stops_on_first_write(
                    stop_event)), \
             mock.patch.object(log_provider,
                               'local_log_path',
                               return_value=src):
            provider = log_provider.LocalLogProvider()
            copied = provider.copy_log_file('req-1',
                                            log_provider.RequestLogType.REQUEST,
                                            dest,
                                            stop_event=stop_event)
        assert not copied
        assert not dest.exists()

    def test_stop_path_unlink_error_does_not_propagate(self, tmp_path):
        src = self._write_src(tmp_path)
        dest = tmp_path / 'request.log'
        stop_event = threading.Event()
        with mock.patch(
                'builtins.open',
                side_effect=self._open_that_stops_on_first_write(
                    stop_event)), \
             mock.patch.object(pathlib.Path,
                               'unlink',
                               side_effect=OSError('stalled fs')), \
             mock.patch.object(log_provider,
                               'local_log_path',
                               return_value=src):
            provider = log_provider.LocalLogProvider()
            # The guarded unlink must not mask the stop contract.
            copied = provider.copy_log_file('req-1',
                                            log_provider.RequestLogType.REQUEST,
                                            dest,
                                            stop_event=stop_event)
        assert not copied

    def test_multichunk_copy_is_byte_identical_with_mtime(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(log_provider, '_LOG_COPY_CHUNK_BYTES', 16)
        src = self._write_src(tmp_path, size_bytes=100)
        dest = tmp_path / 'request.log'
        with mock.patch.object(log_provider, 'local_log_path',
                               return_value=src):
            provider = log_provider.LocalLogProvider()
            copied = provider.copy_log_file('req-1',
                                            log_provider.RequestLogType.REQUEST,
                                            dest)
        assert copied
        assert dest.read_bytes() == src.read_bytes()
        # copystat parity with the old copy2 behavior (mtime included).
        assert dest.stat().st_mtime == src.stat().st_mtime
