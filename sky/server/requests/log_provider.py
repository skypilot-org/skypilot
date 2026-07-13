"""Provider for request logs."""
import abc
import enum
import pathlib
import shutil
from typing import AsyncGenerator, Optional

from sky import sky_logging
from sky.server import constants as server_constants
from sky.server import stream_utils

logger = sky_logging.init_logger(__name__)


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

        The default implementation copies from the local filesystem.
        Providers backed by other log stores may override this to fetch
        the log from wherever it lives.

        Returns:
            True if the log file was found and copied, False otherwise.
        """
        src_path = local_log_path(request_id, log_type)
        if not src_path.exists():
            return False
        shutil.copy2(src_path, dest_path)
        return True


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
