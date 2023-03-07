"""Logging utilities."""
import contextlib
import logging
import sys
import threading
from typing import Optional

from sky.utils import env_options

# If the SKYPILOT_MINIMIZE_LOGGING environment variable is set to True,
# remove logging prefixes and unnecessary information in optimizer
FORMAT = (None if env_options.Options.MINIMIZE_LOGGING.get() else
          '%(levelname).1s %(asctime)s %(filename)s:%(lineno)d] %(message)s')
DATE_FORMAT = '%m-%d %H:%M:%S'


class NewLineFormatter(logging.Formatter):
    """Adds logging prefix to newlines to align multi-line messages."""

    def __init__(self, fmt, datefmt=None):
        logging.Formatter.__init__(self, fmt, datefmt)

    def format(self, record):
        msg = logging.Formatter.format(self, record)
        if record.message != '':
            parts = msg.split(record.message)
            msg = msg.replace('\n', '\r\n' + parts[0])
        return msg


_root_logger = logging.getLogger('sky')
_default_handler = None
_logging_config = threading.local()
_logging_config.is_silent = False

echo = print


def _setup_logger(
    logging_level: int = logging.DEBUG,
    logging_format: Optional[str] = FORMAT,
):
    _root_logger.setLevel(logging_level)
    global _default_handler
    if _default_handler is None:
        _default_handler = logging.StreamHandler(sys.stdout)
        _default_handler.flush = sys.stdout.flush  # type: ignore
        if env_options.Options.SHOW_DEBUG_INFO.get():
            _default_handler.setLevel(logging.DEBUG)
        else:
            _default_handler.setLevel(logging.INFO)
        _root_logger.addHandler(_default_handler)
    fmt = NewLineFormatter(logging_format, datefmt=DATE_FORMAT)
    _default_handler.setFormatter(fmt)
    # Setting this will avoid the message
    # being propagated to the parent logger.
    _root_logger.propagate = False


_setup_logger()


def init_logger(name: str):
    return logging.getLogger(name)


@contextlib.contextmanager
def silent():
    """Turn off logging."""
    global echo
    global _logging_config
    previous_level = _root_logger.level
    previous_is_silent = _logging_config.is_silent
    previous_echo = echo

    # Turn off logger
    _root_logger.setLevel(logging.CRITICAL)
    _logging_config.is_silent = True
    echo = lambda *args, **kwargs: None
    yield

    # Restore logger
    echo = previous_echo
    _root_logger.setLevel(previous_level)
    _logging_config.is_silent = previous_is_silent


def is_silent():
    return _logging_config.is_silent
