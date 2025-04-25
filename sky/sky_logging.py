"""Logging utilities."""
import builtins
import contextlib
from datetime import datetime
import logging
import os
import sys
import threading
from typing import Dict, Optional

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

_internal_logger = logging.getLogger('internal')
_HANDLER_RESERVED_ATTRS = ['__init__', 'get_active_handler', 'apply_env']


def get_active_handler() -> Optional[logging.StreamHandler]:
    """Get the active handler based on context."""
    ctx = context.get()
    if ctx is not None and ctx.log_handler is not None:
        return ctx.log_handler
    return None


def apply_env(handler: logging.StreamHandler):
    """Apply the environment variables to the active handler."""
    if env_options.Options.SHOW_DEBUG_INFO.get():
        handler.setLevel(logging.DEBUG)
    else:
        handler.setLevel(logging.INFO)
    if _show_logging_prefix():
        handler.setFormatter(FORMATTER)
    else:
        handler.setFormatter(NO_PREFIX_FORMATTER)


class ContextAwareHandler(rich_utils.RichSafeStreamHandler):
    """A logging handler that awares the SkyPilot context."""

    def __getattribute__(self, name):
        """Route all method calls to the active handler."""
        active_handler = get_active_handler()
        if active_handler is not None:
            return getattr(active_handler, name)
        return super().__getattribute__(name)


class SensitiveWrapper(ContextAwareHandler):
    """A handler wrapper that filters the sensitive logs."""

    # TODO(aylei): figure out how to handle sensitive logs.
    def get_level(self):
        """Evalute the level based on environment variables.

        For sensitive handler, if SUPPRESS_SENSITIVE_LOG is set, we set
        the level to at least INFO to suppress the debug logs which may
        contain sensitive information. SKYPILOT_DEBUG is not respected in
        this case.
        """
        # Evalute the level on the fly to avoid overriding the level
        # of the contextual handler.
        if env_options.Options.SUPPRESS_SENSITIVE_LOG.get():
            return min(logging.INFO, super().level)
        return super().level


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


_root_logger = logging.getLogger('sky')
_default_handler: Optional[ContextAwareHandler] = None
_child_handlers: Dict[str, ContextAwareHandler] = {}
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
        _default_handler = ContextAwareHandler(sys.stdout)
        _root_logger.addHandler(_default_handler)
    apply_env(_default_handler)

    # Add configuration for internal logger
    internal_logger = logging.getLogger('internal')
    internal_logger.setLevel(logging.DEBUG)
    internal_handler = rich_utils.RichSafeStreamHandler(sys.stdout)
    internal_handler.setFormatter(FORMATTER)
    internal_logger.addHandler(internal_handler)
    internal_logger.propagate = False

    # Setting this will avoid the message
    # being propagated to the parent logger.
    _root_logger.propagate = False
    for logger_name in _SENSITIVE_LOGGER:
        logger = logging.getLogger(logger_name)
        # Use sensitive handler for sensitive loggers to honor the
        # SKYPILOT_SUPPRESS_SENSITIVE_LOG environment variable.
        handler_to_logger = SensitiveWrapper(sys.stdout)
        apply_env(handler_to_logger)
        logger.addHandler(handler_to_logger)
        _child_handlers[logger_name] = handler_to_logger
        # Do not propagate to the parent logger to avoid parent
        # logger printing the logs.
        logger.propagate = False


def reload_logger():
    """Reload the logger.

    This ensures that the logger takes the new environment variables,
    such as SKYPILOT_DEBUG.
    """
    apply_env(_default_handler)
    for handler in _child_handlers.values():
        apply_env(handler)


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
