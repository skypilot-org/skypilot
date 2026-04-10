"""Provider for request logs."""
import abc
import pathlib
from typing import AsyncGenerator, Optional

from sky import sky_logging
from sky.server import stream_utils

logger = sky_logging.init_logger(__name__)


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
        raise NotImplementedError
        yield  # Make this an async generator for type checking


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
