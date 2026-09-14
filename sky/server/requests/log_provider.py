"""Provider for request logs."""
import abc
import enum
import os
import pathlib
import shutil
import tempfile
from typing import AsyncGenerator, Optional

from sky import sky_logging
from sky.server import constants as server_constants
from sky.server import stream_utils

logger = sky_logging.init_logger(__name__)

# Cap for a single request-log copy (e.g. for a debug dump). A log-follow
# request's log can hold a full copy of a tailed log that lives elsewhere,
# and such copies have been observed at hundreds of MB in production
# (dominating dump size), while the vast majority of request logs are far
# below this cap -- so oversized copies keep only the trailing bytes.
_MAX_LOG_COPY_BYTES = 10 * 1024 * 1024
# Prefix of the one-line header prepended to a truncated log copy. Doubles
# as the idempotence marker for cap_log_file_in_place.
_TRUNCATION_HEADER_PREFIX = '[sky debug-dump] Truncated log:'


class RequestLogType(enum.Enum):
    """Types of per-request log files."""
    # The request execution log, written by the executor to
    # REQUEST_LOG_PATH_PREFIX/<request_id>.log.
    REQUEST = 'request'
    # The request debug log, written to DEBUG_LOG_DIR/<request_id>.log
    # when SKYPILOT_SERVER_ENABLE_REQUEST_DEBUG_LOGGING is enabled.
    DEBUG = 'debug'


def local_log_path(request_id: str, log_type: RequestLogType) -> pathlib.Path:
    """Return the local filesystem path for a request's log file.

    Raises:
        ValueError: if request_id would escape the log directory
            (path traversal).
    """
    if log_type == RequestLogType.REQUEST:
        prefix = pathlib.Path(
            server_constants.REQUEST_LOG_PATH_PREFIX).expanduser()
    else:
        prefix = pathlib.Path(sky_logging.DEBUG_LOG_DIR)
    log_path = (prefix / f'{request_id}.log').resolve()
    try:
        log_path.relative_to(prefix.resolve())
    except ValueError:
        raise ValueError(f'Invalid request_id: {request_id!r}') from None
    return log_path


def _truncation_header(original_size: int, kept_bytes: int) -> str:
    """One-line header recording what a truncated log copy is missing."""
    return (f'{_TRUNCATION_HEADER_PREFIX} original size {original_size} '
            f'bytes; kept the trailing {kept_bytes} bytes.\n')


def _copy_log_tail(src_path: pathlib.Path, dest_path: pathlib.Path,
                   src_size: int) -> None:
    """Copy only the trailing _MAX_LOG_COPY_BYTES of an oversized log.

    The header records the original size so a reader can tell a truncated
    copy from a complete one. The read is bounded by an explicit remaining
    counter, so a source still being appended to mid-copy cannot push the
    copy past the cap; a source that shrank since the stat above just
    yields a shorter tail.
    """
    cap = _MAX_LOG_COPY_BYTES
    with open(src_path, 'rb') as src, open(dest_path, 'wb') as dest:
        dest.write(_truncation_header(src_size, cap).encode('utf-8'))
        src.seek(max(0, src_size - cap))
        remaining = cap
        while remaining > 0:
            chunk = src.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            dest.write(chunk)
            remaining -= len(chunk)
    # mtime parity with shutil.copy2's forensic semantics.
    shutil.copystat(src_path, dest_path)


def cap_log_file_in_place(path: pathlib.Path) -> None:
    """Truncate an oversized log file in place to the trailing cap bytes.

    Backstop for providers that override copy_log_file without a size
    cap: whatever wrote ``path``, it ends up as truncation header +
    trailing _MAX_LOG_COPY_BYTES bytes. Best-effort -- any OSError is
    logged at debug level and never raised.

    Idempotent: a file already carrying the truncation header (header +
    cap bytes, slightly over the cap) is left alone, so a provider-capped
    copy is not re-truncated.
    """
    try:
        size = path.stat().st_size
        if size <= _MAX_LOG_COPY_BYTES:
            return
        with open(path, 'rb') as f:
            prefix = f.read(len(_TRUNCATION_HEADER_PREFIX))
        if prefix == _TRUNCATION_HEADER_PREFIX.encode('utf-8'):
            return
        with tempfile.NamedTemporaryFile(dir=path.parent,
                                         prefix=path.name + '.',
                                         suffix='.tmp',
                                         delete=False) as tmp:
            tmp_path = pathlib.Path(tmp.name)
        try:
            _copy_log_tail(path, tmp_path, size)
            os.replace(tmp_path, path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except OSError as e:
        logger.debug(f'Failed to cap log file {path}: {e}')


class LogProvider(abc.ABC):
    """Abstract interface for backing request logs."""

    @abc.abstractmethod
    async def log_stream(
        self,
        request_id: str,
        log_path: pathlib.Path,
        *,
        plain_logs: bool = False,
        tail: Optional[int] = None,
        follow: bool = True,
        polling_interval: float = stream_utils.DEFAULT_POLL_INTERVAL,
    ) -> AsyncGenerator[str, None]:
        """Stream logs for the given request.

        Args:
            request_id: The request ID whose logs to stream.
            log_path: The local path where the log file is expected.
            plain_logs: If True, strip rich/control payloads.
            tail: Number of trailing lines to show. None means all.
            follow: If True, follow the file as it grows.
            polling_interval: How often to poll for new content / status.

        Yields:
            Log content strings.
        """
        del request_id, log_path, plain_logs, tail, follow, polling_interval
        yield ''

    def copy_log_file(self, request_id: str, log_type: RequestLogType,
                      dest_path: pathlib.Path) -> bool:
        """Copy a request's log file to dest_path (e.g. for a debug dump).

        The default implementation copies from the local filesystem;
        copies larger than _MAX_LOG_COPY_BYTES keep only the trailing
        bytes, prefixed with a truncation header naming the original
        size. Providers backed by other log stores may override this to
        fetch the log from wherever it lives (and should apply the same
        cap, or rely on the dump-side cap_log_file_in_place backstop).

        Returns:
            True if the log file was found and copied, False otherwise.
        """
        src_path = local_log_path(request_id, log_type)
        try:
            src_size = src_path.stat().st_size
        except FileNotFoundError:
            return False
        if src_size > _MAX_LOG_COPY_BYTES:
            _copy_log_tail(src_path, dest_path, src_size)
        else:
            shutil.copy2(src_path, dest_path)
        return True

    def discard_log(self, request_id: str) -> None:
        """Delete a request's execution log, leaving its debug log alone.

        Called once the response that streamed the log has ended, for
        requests whose whole output is a tail of a log that lives elsewhere.
        Providers backed by other log stores may override this to delete the
        log wherever it lives.
        """
        try:
            local_log_path(request_id,
                           RequestLogType.REQUEST).unlink(missing_ok=True)
        except OSError as e:
            logger.debug(f'Failed to remove log of request {request_id}: {e}')


class LocalLogProvider(LogProvider):
    """Default log provider."""

    async def log_stream(
        self,
        request_id: str,
        log_path: pathlib.Path,
        *,
        plain_logs: bool = False,
        tail: Optional[int] = None,
        follow: bool = True,
        polling_interval: float = stream_utils.DEFAULT_POLL_INTERVAL,
    ) -> AsyncGenerator[str, None]:
        async for chunk in stream_utils.log_streamer(
                request_id=request_id,
                log_path=log_path,
                plain_logs=plain_logs,
                tail=tail,
                follow=follow,
                polling_interval=polling_interval):
            yield chunk


_log_provider: Optional[LogProvider] = None


def get_log_provider() -> LogProvider:
    """Return the current log provider."""
    global _log_provider
    if _log_provider is None:
        _log_provider = LocalLogProvider()
    return _log_provider


def set_log_provider(lp: LogProvider) -> None:
    """Replace the log provider."""
    global _log_provider
    _log_provider = lp
