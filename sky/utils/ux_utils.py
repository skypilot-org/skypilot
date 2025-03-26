"""Utility functions for UX."""
import contextlib
import enum
import os
import sys
import traceback
import typing
from typing import Callable, Optional, Union

import colorama

from sky import sky_logging
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import rich_console_utils

if typing.TYPE_CHECKING:
    import pathlib

INDENT_SYMBOL = f'{colorama.Style.DIM}├── {colorama.Style.RESET_ALL}'
INDENT_LAST_SYMBOL = f'{colorama.Style.DIM}└── {colorama.Style.RESET_ALL}'

# Console formatting constants
BOLD = '\033[1m'
RESET_BOLD = '\033[0m'

# Log path hint in the spinner during launching
_LOG_PATH_HINT = (f'{colorama.Style.DIM}View logs: sky api logs -l '
                  '{log_path}'
                  f'{colorama.Style.RESET_ALL}')
_LOG_PATH_HINT_LOCAL = (f'{colorama.Style.DIM}View logs: '
                        '{log_path}'
                        f'{colorama.Style.RESET_ALL}')


def console_newline():
    """Prints a newline to the console using rich.

    Useful when catching exceptions inside console.status()
    """
    rich_console_utils.get_console().print()


@contextlib.contextmanager
def print_exception_no_traceback():
    """A context manager that prints out an exception without traceback.

    Mainly for UX: user-facing errors, e.g., ValueError, should suppress long
    tracebacks.

    If SKYPILOT_DEBUG environment variable is set, this context manager is a
    no-op and the full traceback will be shown.

    Example usage:

        with print_exception_no_traceback():
            if error():
                raise ValueError('...')
    """
    original_tracelimit = getattr(sys, 'tracebacklimit', 1000)
    sys.tracebacklimit = 0
    yield
    sys.tracebacklimit = original_tracelimit


@contextlib.contextmanager
def enable_traceback():
    """Reverts the effect of print_exception_no_traceback().

    This is used for usage_lib to collect the full traceback.
    """
    original_tracelimit = getattr(sys, 'tracebacklimit', 1000)
    sys.tracebacklimit = 1000
    yield
    sys.tracebacklimit = original_tracelimit


class RedirectOutputForProcess:
    """Redirects stdout and stderr to a file.

    This class enabled output redirect for multiprocessing.Process.
    Example usage:

    p = multiprocessing.Process(
        target=RedirectOutputForProcess(func, file_name).run, args=...)

    This is equal to:

    p = multiprocessing.Process(target=func, args=...)

    Plus redirect all stdout/stderr to file_name.
    """

    def __init__(self, func: Callable, file: str, mode: str = 'w') -> None:
        self.func = func
        self.file = file
        self.mode = mode

    def run(self, *args, **kwargs):
        with open(self.file, self.mode, encoding='utf-8') as f:
            sys.stdout = f
            sys.stderr = f
            # reconfigure logger since the logger is initialized before
            # with previous stdout/stderr
            sky_logging.reload_logger()
            logger = sky_logging.init_logger(__name__)
            # The subprocess_util.run('sky status') inside
            # sky.execution::_execute cannot be redirect, since we cannot
            # directly operate on the stdout/stderr of the subprocess. This
            # is because some code in skypilot will specify the stdout/stderr
            # of the subprocess.
            try:
                self.func(*args, **kwargs)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Failed to run {self.func.__name__}. '
                             f'Details: {common_utils.format_exception(e)}')
                with enable_traceback():
                    logger.error(f'  Traceback:\n{traceback.format_exc()}')
                raise


def log_path_hint(log_path: Union[str, 'pathlib.Path'],
                  is_local: bool = False) -> str:
    """Gets the log path hint for the given log path."""
    log_path = str(log_path)
    expanded_home = os.path.expanduser('~')
    if log_path.startswith(expanded_home):
        log_path = '~' + log_path[len(expanded_home):]
    if is_local:
        return _LOG_PATH_HINT_LOCAL.format(log_path=log_path)
    if log_path.startswith(constants.SKY_LOGS_DIRECTORY):
        log_path = log_path[len(constants.SKY_LOGS_DIRECTORY):]
    log_path = log_path.lstrip(os.path.sep)
    return _LOG_PATH_HINT.format(log_path=log_path)


def starting_message(message: str) -> str:
    """Gets the starting message for the given message."""
    # We have to reset the color before the message, because sometimes if a
    # previous spinner with dimmed color overflows in a narrow terminal, the
    # color might be messed up.
    return f'{colorama.Style.RESET_ALL}⚙︎ {message}'


def finishing_message(message: str,
                      log_path: Optional[Union[str, 'pathlib.Path']] = None,
                      is_local: bool = False,
                      follow_up_message: Optional[str] = None) -> str:
    """Gets the finishing message for the given message.

    Args:
        message: The main message to be displayed.
        log_path: The log path to be displayed in the message.
        is_local: Whether the log path is local or on remote API server.
        follow_up_message: A message to be displayed after the main message.
          The follow up message is not colored.
    """
    # We have to reset the color before the message, because sometimes if a
    # previous spinner with dimmed color overflows in a narrow terminal, the
    # color might be messed up.
    follow_up_message = follow_up_message if (follow_up_message
                                              is not None) else ''
    success_prefix = (f'{colorama.Style.RESET_ALL}{colorama.Fore.GREEN}✓ '
                      f'{message}{colorama.Style.RESET_ALL}{follow_up_message}')
    if log_path is None:
        return success_prefix
    path_hint = log_path_hint(log_path, is_local)
    return f'{success_prefix}  {path_hint}'


def error_message(message: str,
                  log_path: Optional[Union[str, 'pathlib.Path']] = None,
                  is_local: bool = False) -> str:
    """Gets the error message for the given message."""
    # We have to reset the color before the message, because sometimes if a
    # previous spinner with dimmed color overflows in a narrow terminal, the
    # color might be messed up.
    error_prefix = (f'{colorama.Style.RESET_ALL}{colorama.Fore.RED}⨯'
                    f'{colorama.Style.RESET_ALL} {message}')
    if log_path is None:
        return error_prefix
    path_hint = log_path_hint(log_path, is_local)
    return f'{error_prefix}  {path_hint}'


def retry_message(message: str) -> str:
    """Gets the retry message for the given message."""
    # We have to reset the color before the message, because sometimes if a
    # previous spinner with dimmed color overflows in a narrow terminal, the
    # color might be messed up.
    return (f'{colorama.Style.RESET_ALL}{colorama.Fore.YELLOW}↺'
            f'{colorama.Style.RESET_ALL} {message}')


def spinner_message(message: str,
                    log_path: Optional[Union[str, 'pathlib.Path']] = None,
                    is_local: bool = False) -> str:
    """Gets the spinner message for the given message and log path."""
    colored_spinner = f'[bold cyan]{message}[/]'
    if log_path is None:
        return colored_spinner
    path_hint = log_path_hint(log_path, is_local)
    return f'{colored_spinner}  {path_hint}'


class CommandHintType(enum.Enum):
    CLUSTER_JOB = 'cluster_job'
    MANAGED_JOB = 'managed_job'


def command_hint_messages(hint_type: CommandHintType,
                          job_id: Optional[str] = None,
                          cluster_name: Optional[str] = None) -> str:
    """Gets the command hint messages for the given job id."""
    if hint_type == CommandHintType.CLUSTER_JOB:
        job_hint_str = (f'\nJob ID: {job_id}'
                        f'\n{INDENT_SYMBOL}To cancel the job:\t\t'
                        f'{BOLD}sky cancel {cluster_name} {job_id}{RESET_BOLD}'
                        f'\n{INDENT_SYMBOL}To stream job logs:\t\t'
                        f'{BOLD}sky logs {cluster_name} {job_id}{RESET_BOLD}'
                        f'\n{INDENT_LAST_SYMBOL}To view job queue:\t\t'
                        f'{BOLD}sky queue {cluster_name}{RESET_BOLD}')
        cluster_hint_str = (f'\nCluster name: {cluster_name}'
                            f'\n{INDENT_SYMBOL}To log into the head VM:\t'
                            f'{BOLD}ssh {cluster_name}'
                            f'{RESET_BOLD}'
                            f'\n{INDENT_SYMBOL}To submit a job:'
                            f'\t\t{BOLD}sky exec {cluster_name} yaml_file'
                            f'{RESET_BOLD}'
                            f'\n{INDENT_SYMBOL}To stop the cluster:'
                            f'\t{BOLD}sky stop {cluster_name}'
                            f'{RESET_BOLD}'
                            f'\n{INDENT_LAST_SYMBOL}To teardown the cluster:'
                            f'\t{BOLD}sky down {cluster_name}'
                            f'{RESET_BOLD}')
        hint_str = '\n📋 Useful Commands'
        if job_id is not None:
            hint_str += f'{job_hint_str}'
        hint_str += f'{cluster_hint_str}'
        return hint_str
    elif hint_type == CommandHintType.MANAGED_JOB:
        return (f'\n📋 Useful Commands'
                f'\nManaged Job ID: {job_id}'
                f'\n{INDENT_SYMBOL}To cancel the job:\t\t'
                f'{BOLD}sky jobs cancel {job_id}{RESET_BOLD}'
                f'\n{INDENT_SYMBOL}To stream job logs:\t\t'
                f'{BOLD}sky jobs logs {job_id}{RESET_BOLD}'
                f'\n{INDENT_SYMBOL}To stream controller logs:\t\t'
                f'{BOLD}sky jobs logs --controller {job_id}{RESET_BOLD}'
                f'\n{INDENT_SYMBOL}To view all managed jobs:\t\t'
                f'{BOLD}sky jobs queue{RESET_BOLD}'
                f'\n{INDENT_LAST_SYMBOL}To view managed job dashboard:\t\t'
                f'{BOLD}sky jobs dashboard{RESET_BOLD}')
    else:
        raise ValueError(f'Invalid hint type: {hint_type}')
