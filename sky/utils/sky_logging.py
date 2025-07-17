"""Logging utilities."""
import builtins
import contextlib
from datetime import datetime
import logging
import os
import sys
import threading

import colorama

from sky.skylet import constants
from sky.utils import context
from sky.utils import env_options
from sky.utils import rich_utils

# UX: Should we show logging prefixes and some extra information in optimizer?
_FORMAT = '%(levelname).1s %(asctime)s %(filename)s:%(lineno)d] %(message)s'
_DATE_FORMAT = '%m-%d %H:%M:%S'
_SENSITIVE_LOGGER = ['sky.provisioner', 'sky.optimizer']

DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL


def _show_logging_prefix():
    return env_options.Options.SHOW_DEBUG_INFO.get(
    ) or not env_options.Options.MINIMIZE_LOGGING.get()


class NewLineFormatter(logging.Formatter):
    """Adds logging prefix to newlines to align multi-line messages."""

    def __init__(self, fmt, datefmt=None, dim=False):
        logging.Formatter.__init__(self, fmt, datefmt)
        self.dim = dim

    def format(self, record):
        msg = logging.Formatter.format(self, record)
        if record.message != '':
            parts = msg.partition(record.message)
            msg = msg.replace('\n', '\r\n' + parts[0])
            if self.dim:
                msg = colorama.Style.DIM + msg + colorama.Style.RESET_ALL
        return msg


class EnvAwareHandler(rich_utils.RichSafeStreamHandler):
    """A handler that awares environment variables.

    This handler dynamically reflects the log level from environment variables.
    """

    def __init__(self, stream=None, level=logging.NOTSET, sensitive=False):
        super().__init__(stream)
        self.level = level
        self._sensitive = sensitive

    @property
    def level(self):
        # Only refresh log level if we are in a context, since the log level
        # has already been reloaded eagerly in multi-processing. Refresh again
        # is a no-op and can be avoided.
        # TODO(aylei): unify the mechanism for coroutine context and
        # multi-processing.
        if context.get() is not None:
            if self._sensitive:
                # For sensitive logger, suppress debug log despite the
                # SKYPILOT_DEBUG env var if SUPPRESS_SENSITIVE_LOG is set
                if env_options.Options.SUPPRESS_SENSITIVE_LOG.get():
                    return logging.INFO
            if env_options.Options.SHOW_DEBUG_INFO.get():
                return logging.DEBUG
            else:
                return self._level
        else:
            return self._level

    @level.setter
    def level(self, level):
        # pylint: disable=protected-access
        self._level = logging._checkLevel(level)


_root_logger = logging.getLogger('sky')
_default_handler = None
_logging_config = threading.local()

NO_PREFIX_FORMATTER = NewLineFormatter(None, datefmt=_DATE_FORMAT)
FORMATTER = NewLineFormatter(_FORMAT, datefmt=_DATE_FORMAT)
DIM_FORMATTER = NewLineFormatter(_FORMAT, datefmt=_DATE_FORMAT, dim=True)

# All code inside the library should use sky_logging.print()
# rather than print().
# This is to make controlled logging via is_silent() possible:
# in some situation we would like to disable any
# printing/logging.
print = builtins.print  # pylint: disable=redefined-builtin


def _setup_logger():
    _root_logger.setLevel(logging.DEBUG)
    global _default_handler
    if _default_handler is None:
        _default_handler = EnvAwareHandler(sys.stdout)
        _default_handler.flush = sys.stdout.flush  # type: ignore
        if env_options.Options.SHOW_DEBUG_INFO.get():
            _default_handler.setLevel(logging.DEBUG)
        else:
            _default_handler.setLevel(logging.INFO)
        _root_logger.addHandler(_default_handler)
    if _show_logging_prefix():
        _default_handler.setFormatter(FORMATTER)
    else:
        _default_handler.setFormatter(NO_PREFIX_FORMATTER)
    # Setting this will avoid the message
    # being propagated to the parent logger.
    _root_logger.propagate = False
    if env_options.Options.SUPPRESS_SENSITIVE_LOG.get():
        # If the sensitive log is enabled, we reinitialize a new handler
        # and force set the level to INFO to suppress the debug logs
        # for certain loggers.
        for logger_name in _SENSITIVE_LOGGER:
            logger = logging.getLogger(logger_name)
            handler_to_logger = EnvAwareHandler(sys.stdout, sensitive=True)
            handler_to_logger.flush = sys.stdout.flush  # type: ignore
            logger.addHandler(handler_to_logger)
            logger.setLevel(logging.INFO)
            if _show_logging_prefix():
                handler_to_logger.setFormatter(FORMATTER)
            else:
                handler_to_logger.setFormatter(NO_PREFIX_FORMATTER)
            # Do not propagate to the parent logger to avoid parent
            # logger printing the logs.
            logger.propagate = False


def reload_logger():
    """Reload the logger.

    This ensures that the logger takes the new environment variables,
    such as SKYPILOT_DEBUG.
    """
    global _default_handler
    _root_logger.removeHandler(_default_handler)
    _default_handler = None
    _setup_logger()


# The logger is initialized when the module is imported.
# This is thread-safe as the module is only imported once,
# guaranteed by the Python GIL.
_setup_logger()


def init_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextlib.contextmanager
def set_logging_level(logger: str, level: int):
    logger = logging.getLogger(logger)
    original_level = logger.level
    logger.setLevel(level)
    try:
        yield
    finally:
        logger.setLevel(original_level)


def logging_enabled(logger: logging.Logger, level: int) -> bool:
    return logger.level <= level


@contextlib.contextmanager
def silent():
    """Make all sky_logging.print() and logger.{info, warning...} silent.

    We preserve the ERROR level logging, so that errors are
    still printed.
    """
    global print
    previous_level = _root_logger.level
    previous_is_silent = is_silent()
    previous_print = print

    # Turn off logger
    _root_logger.setLevel(logging.ERROR)
    _logging_config.is_silent = True
    print = lambda *args, **kwargs: None
    try:
        yield
    finally:
        # Restore logger
        print = previous_print
        _root_logger.setLevel(previous_level)
        _logging_config.is_silent = previous_is_silent


def is_silent():
    if not hasattr(_logging_config, 'is_silent'):
        # Should not set it globally, as the global assignment
        # will be executed only once if the module is imported
        # in the main thread, and will not be executed in other
        # threads.
        _logging_config.is_silent = False
    return _logging_config.is_silent


def get_run_timestamp() -> str:
    return 'sky-' + datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f')


def generate_tmp_logging_file_path(file_name: str) -> str:
    """Generate an absolute path of a tmp file for logging."""
    run_timestamp = get_run_timestamp()
    log_dir = os.path.join(constants.SKY_LOGS_DIRECTORY, run_timestamp)
    log_path = os.path.expanduser(os.path.join(log_dir, file_name))

    return log_path
