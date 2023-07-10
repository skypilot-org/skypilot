"""Utility functions for subprocesses."""
from multiprocessing import pool
import psutil
import random
import subprocess
import time
from typing import Any, Callable, List, Optional, Tuple, Union

import colorama

from sky import exceptions
from sky import sky_logging
from sky.skylet import log_lib
from sky.utils import timeline
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


@timeline.event
def run(cmd, **kwargs):
    # Should be careful to use this function, as the child process cmd spawn may
    # keep running in the background after the current program is killed. To get
    # rid of this problem, use `log_lib.run_with_log`.
    shell = kwargs.pop('shell', True)
    check = kwargs.pop('check', True)
    executable = kwargs.pop('executable', '/bin/bash')
    if not shell:
        executable = None
    return subprocess.run(cmd,
                          shell=shell,
                          check=check,
                          executable=executable,
                          **kwargs)


def run_no_outputs(cmd, **kwargs):
    return run(cmd,
               stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL,
               **kwargs)


def run_in_parallel(func: Callable, args: List[Any]) -> List[Any]:
    """Run a function in parallel on a list of arguments.

    The function should raise a CommandError if the command fails.
    Returns a list of the return values of the function func, in the same order
    as the arguments.
    """
    # Reference: https://stackoverflow.com/questions/25790279/python-multiprocessing-early-termination # pylint: disable=line-too-long
    with pool.ThreadPool() as p:
        # Run the function in parallel on the arguments, keeping the order.
        return list(p.imap(func, args))


def handle_returncode(returncode: int,
                      command: str,
                      error_msg: Union[str, Callable[[], str]],
                      stderr: Optional[str] = None,
                      stream_logs: bool = True) -> None:
    """Handle the returncode of a command.

    Args:
        returncode: The returncode of the command.
        command: The command that was run.
        error_msg: The error message to print.
        stderr: The stderr of the command.
    """
    echo = logger.error if stream_logs else lambda _: None
    if returncode != 0:
        if stderr is not None:
            echo(stderr)

        if callable(error_msg):
            error_msg = error_msg()
        format_err_msg = (
            f'{colorama.Fore.RED}{error_msg}{colorama.Style.RESET_ALL}')
        with ux_utils.print_exception_no_traceback():
            raise exceptions.CommandError(returncode, command, format_err_msg)


def kill_children_processes(first_pid_to_kill: Optional[int] = None,
                            force: bool = False):
    """Kill children processes recursively.

    We need to kill the children, so that
    1. The underlying subprocess will not print the logs to the terminal,
       after this program exits.
    2. The underlying subprocess will not continue with starting a cluster
       etc. while we are cleaning up the clusters.

    Args:
        first_pid_to_kill: Optional PID of a process to be killed first.
         This is for guaranteeing the order of cleaning up and suppress
         flaky errors.
    """
    parent_process = psutil.Process()
    child_processes = []
    for child in parent_process.children(recursive=True):
        if child.pid == first_pid_to_kill:
            try:
                if force:
                    child.kill()
                else:
                    child.terminate()
                child.wait()
            except psutil.NoSuchProcess:
                # The child process may have already been terminated.
                pass
        else:
            child_processes.append(child)

    for child in child_processes:
        try:
            if force:
                child.kill()
            else:
                child.terminate()
        except psutil.NoSuchProcess:
            # The child process may have already been terminated.
            pass


def run_with_retries(
        cmd: str,
        max_retry: int = 3,
        retry_returncode: Optional[List[int]] = None,
        retry_stderrs: Optional[List[str]] = None) -> Tuple[int, str, str]:
    """Run a command and retry if it fails due to the specified reasons.

    Args:
        cmd: The command to run.
        max_retry: The maximum number of retries.
        retry_returncode: The returncodes that should be retried.
        retry_stderr: The cmd needs to be retried if the stderr contains any of
            the strings in this list.

    Returns:
        The returncode, stdout, and stderr of the command.
    """
    retry_cnt = 0
    while True:
        returncode, stdout, stderr = log_lib.run_with_log(cmd,
                                                          '/dev/null',
                                                          require_outputs=True,
                                                          shell=True)
        if retry_cnt < max_retry:
            if (retry_returncode is not None and
                    returncode in retry_returncode):
                retry_cnt += 1
                time.sleep(random.uniform(0, 1) * 2)
                continue

            if retry_stderrs is None:
                break

            need_retry = False
            for retry_err in retry_stderrs:
                if retry_err in stderr:
                    retry_cnt += 1
                    time.sleep(random.uniform(0, 1) * 2)
                    need_retry = True
                    break
            if need_retry:
                continue
        break
    return returncode, stdout, stderr
