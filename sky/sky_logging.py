"""Logging utilities."""
import builtins
import contextlib
import logging
import os
import pathlib
import sys
import threading
from typing import Callable, Dict, List, Union

import colorama

from sky.backends import backend_utils
from sky.skylet import constants
from sky.utils import env_options
from sky.utils import rich_utils

# UX: Should we show logging prefixes and some extra information in optimizer?
_show_logging_prefix = (env_options.Options.SHOW_DEBUG_INFO.get() or
                        not env_options.Options.MINIMIZE_LOGGING.get())
_FORMAT = '%(levelname).1s %(asctime)s %(filename)s:%(lineno)d] %(message)s'
_DATE_FORMAT = '%m-%d %H:%M:%S'


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


class RichSafeStreamHandler(logging.StreamHandler):

    def emit(self, record: logging.LogRecord) -> None:
        with rich_utils.safe_logger():
            return super().emit(record)


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
        _default_handler = RichSafeStreamHandler(sys.stdout)
        _default_handler.flush = sys.stdout.flush  # type: ignore
        if env_options.Options.SHOW_DEBUG_INFO.get():
            _default_handler.setLevel(logging.DEBUG)
        else:
            _default_handler.setLevel(logging.INFO)
        _root_logger.addHandler(_default_handler)
    if _show_logging_prefix:
        _default_handler.setFormatter(FORMATTER)
    else:
        _default_handler.setFormatter(NO_PREFIX_FORMATTER)
    # Setting this will avoid the message
    # being propagated to the parent logger.
    _root_logger.propagate = False


def reload_logger():
    """Reload the logger.

    This is useful when the logging configuration is changed.
    e.g., the logging level is changed or stdout/stderr is reset.
    """
    global _default_handler
    _root_logger.removeHandler(_default_handler)
    _default_handler = None
    _setup_logger()


# The logger is initialized when the module is imported.
# This is thread-safe as the module is only imported once,
# guaranteed by the Python GIL.
_setup_logger()


def init_logger(name: str):
    if name in _LOGGER_NAME_INITIALIZER_MAP and not\
          _LOGGER_NAME_INITIALIZER_MAP[name][1]:
        # Initialize the logger if it is not initialized
        # and configured in _LOGGER_NAME_INITIALIZER_MAP.
        _LOGGER_NAME_INITIALIZER_MAP[name][0](name)  # type: ignore
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


def _initialize_tmp_file_logger(logger_name: str) -> None:
    initialized = _LOGGER_NAME_INITIALIZER_MAP[logger_name][1]
    if initialized:
        return

    # Initialize the logger
    run_timestamp = backend_utils.get_run_timestamp()

    # set up the logger to write to a tmp file
    log_dir = os.path.join(constants.SKY_LOGS_DIRECTORY, run_timestamp)
    log_path = os.path.expanduser(os.path.join(log_dir, logger_name))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log_abs_path = pathlib.Path(log_path).expanduser().absolute()
    fh = logging.FileHandler(log_abs_path)

    logger = logging.getLogger(logger_name)

    for handler in logger.handlers:
        print(handler)

    fh.setFormatter(FORMATTER)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    # Clear stream handler
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            logger.removeHandler(handler)

    _LOGGER_NAME_INITIALIZER_MAP[logger_name][1] = True


# A map from logger name to a tuple of (initializer, is_initialized).
# The initializer is a function that initializes the logger.
# The is_initialized is a boolean indicating if the logger is initialized.
_LOGGER_NAME_INITIALIZER_MAP: Dict[str,
                                   List[Union[Callable[[str], None], bool]]] = {
                                       'sky.data.storage': [
                                           _initialize_tmp_file_logger, False
                                       ]
                                   }
