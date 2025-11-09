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

_DEBUG_LOG_DIR = os.path.expanduser(
    os.path.join(constants.SKY_LOGS_DIRECTORY, 'request_debug'))

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


@contextlib.contextmanager
def set_sky_logging_levels(level: int):
    """Set the logging level for all loggers."""
    # Turn off logger
    previous_levels = {}
    for logger_name in logging.Logger.manager.loggerDict:
        if logger_name.startswith('sky'):
            logger = logging.getLogger(logger_name)
            previous_levels[logger_name] = logger.level
            logger.setLevel(level)
    if level == logging.DEBUG:
        previous_show_debug_info = env_options.Options.SHOW_DEBUG_INFO.get()
        os.environ[env_options.Options.SHOW_DEBUG_INFO.env_key] = '1'
    try:
        yield
    finally:
        # Restore logger
        for logger_name in logging.Logger.manager.loggerDict:
            if logger_name.startswith('sky'):
                logger = logging.getLogger(logger_name)
                try:
                    logger.setLevel(previous_levels[logger_name])
                except KeyError:
                    # New loggers maybe initialized after the context manager,
                    # no need to restore the level.
                    pass
        if level == logging.DEBUG and not previous_show_debug_info:
            os.environ.pop(env_options.Options.SHOW_DEBUG_INFO.env_key)


def logging_enabled(logger: logging.Logger, level: int) -> bool:
    # Note(cooperc): This may return true in a lot of cases where we won't
    # actually log anything, since the log level is set on the handler in
    # _setup_logger.
    return logger.getEffectiveLevel() <= level


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


@contextlib.contextmanager
def add_debug_log_handler(request_id: str):
    if os.getenv(constants.ENV_VAR_ENABLE_REQUEST_DEBUG_LOGGING) != 'true':
        yield
        return

    os.makedirs(_DEBUG_LOG_DIR, exist_ok=True)
    log_path = os.path.join(_DEBUG_LOG_DIR, f'{request_id}.log')
    try:
        debug_log_handler = logging.FileHandler(log_path)
        debug_log_handler.setFormatter(FORMATTER)
        debug_log_handler.setLevel(logging.DEBUG)
        _root_logger.addHandler(debug_log_handler)
        # sky.provision sets up its own logger/handler with propogate=False,
        # so add it there too.
        provision_logger = logging.getLogger('sky.provision')
        provision_logger.addHandler(debug_log_handler)
        provision_logger.setLevel(logging.DEBUG)
        yield
    finally:
        _root_logger.removeHandler(debug_log_handler)
        provision_logger.removeHandler(debug_log_handler)
        debug_log_handler.close()
