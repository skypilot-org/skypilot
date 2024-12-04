"""Utility functions for subprocesses."""
from multiprocessing import pool
import os
import random
import resource
import subprocess
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

import colorama
import psutil

from sky import exceptions
from sky import sky_logging
from sky.skylet import constants
from sky.skylet import log_lib
from sky.utils import timeline
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

_fd_limit_warning_shown = False


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


def _get_thread_multiplier(cloud_str: Optional[str] = None) -> int:
    # If using Kubernetes, we use 4x the number of cores.
    if cloud_str and cloud_str.lower() == 'kubernetes':
        return 4
    return 1


def get_max_workers_for_file_mounts(common_file_mounts: Dict[str, str],
                                    cloud_str: Optional[str] = None) -> int:
    global _fd_limit_warning_shown
    fd_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)

    # Raise warning for low fd_limit (only once)
    if fd_limit < 1024 and not _fd_limit_warning_shown:
        logger.warning(
            f'Open file descriptor limit ({fd_limit}) is low. File sync to '
            'remote clusters may be slow. Consider increasing the limit using '
            '`ulimit -n <number>` or modifying system limits.')
        _fd_limit_warning_shown = True

    fd_per_rsync = 5
    for src in common_file_mounts.values():
        if os.path.isdir(src):
            # Assume that each file/folder under src takes 5 file descriptors
            # on average.
            fd_per_rsync = max(fd_per_rsync, len(os.listdir(src)) * 5)

    # Reserve some file descriptors for the system and other processes
    fd_reserve = 100

    max_workers = (fd_limit - fd_reserve) // fd_per_rsync
    # At least 1 worker, and avoid too many workers overloading the system.
    num_threads = get_parallel_threads(cloud_str)
    max_workers = min(max(max_workers, 1), num_threads)
    logger.debug(f'Using {max_workers} workers for file mounts.')
    return max_workers


def get_parallel_threads(cloud_str: Optional[str] = None) -> int:
    """Returns the number of threads to use for parallel execution.

    Args:
        cloud_str: The cloud
    """
    cpu_count = os.cpu_count()
    if cpu_count is None:
        cpu_count = 1
    return max(4, cpu_count - 1) * _get_thread_multiplier(cloud_str)


def run_in_parallel(func: Callable,
                    args: Iterable[Any],
                    num_threads: Optional[int] = None) -> List[Any]:
    """Run a function in parallel on a list of arguments.

    The function 'func' should raise a CommandError if the command fails.

    Args:
        func: The function to run in parallel
        args: Iterable of arguments to pass to func
        num_threads: Number of threads to use. If None, uses
          get_parallel_threads()

    Returns:
      A list of the return values of the function func, in the same order as the
      arguments.
    """
    # Reference: https://stackoverflow.com/questions/25790279/python-multiprocessing-early-termination # pylint: disable=line-too-long
    processes = num_threads if num_threads is not None else get_parallel_threads(
    )
    with pool.ThreadPool(processes=processes) as p:
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
        stream_logs: Whether to stream logs.
    """
    echo = logger.error if stream_logs else logger.debug
    if returncode != 0:
        if stderr is not None:
            echo(stderr)

        if callable(error_msg):
            error_msg = error_msg()
        format_err_msg = (
            f'{colorama.Fore.RED}{error_msg}{colorama.Style.RESET_ALL}')
        with ux_utils.print_exception_no_traceback():
            raise exceptions.CommandError(returncode, command, format_err_msg,
                                          stderr)


def kill_children_processes(
        first_pid_to_kill: Optional[Union[int, List[Optional[int]]]] = None,
        force: bool = False):
    """Kill children processes recursively.

    We need to kill the children, so that
    1. The underlying subprocess will not print the logs to the terminal,
       after this program exits.
    2. The underlying subprocess will not continue with starting a cluster
       etc. while we are cleaning up the clusters.

    Args:
        first_pid_to_kill: Optional PID of a process, or PIDs of a series of
         processes to be killed first. If a list of PID is specified, it is
         killed by the order in the list.
         This is for guaranteeing the order of cleaning up and suppress
         flaky errors.
    """
    pid_to_proc = dict()
    child_processes = []
    if isinstance(first_pid_to_kill, int):
        first_pid_to_kill = [first_pid_to_kill]
    elif first_pid_to_kill is None:
        first_pid_to_kill = []

    def _kill_processes(processes: List[psutil.Process]) -> None:
        for process in processes:
            try:
                if force:
                    process.kill()
                else:
                    process.terminate()
            except psutil.NoSuchProcess:
                # The process may have already been terminated.
                pass

    parent_process = psutil.Process()
    for child in parent_process.children(recursive=True):
        if child.pid in first_pid_to_kill:
            pid_to_proc[child.pid] = child
        else:
            child_processes.append(child)

    _kill_processes([
        pid_to_proc[proc] for proc in first_pid_to_kill if proc in pid_to_proc
    ])
    _kill_processes(child_processes)


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
                logger.debug(
                    f'Retrying command due to returncode {returncode}: {cmd}')
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


def kill_process_daemon(process_pid: int) -> None:
    """Start a daemon as a safety net to kill the process.

    Args:
        process_pid: The PID of the process to kill.
    """
    # Get initial children list
    try:
        process = psutil.Process(process_pid)
        initial_children = [p.pid for p in process.children(recursive=True)]
    except psutil.NoSuchProcess:
        initial_children = []

    parent_pid = os.getpid()
    daemon_script = os.path.join(
        os.path.dirname(os.path.abspath(log_lib.__file__)),
        'subprocess_daemon.py')
    python_path = subprocess.check_output(constants.SKY_GET_PYTHON_PATH_CMD,
                                          shell=True,
                                          stderr=subprocess.DEVNULL,
                                          encoding='utf-8').strip()
    daemon_cmd = [
        python_path,
        daemon_script,
        '--parent-pid',
        str(parent_pid),
        '--proc-pid',
        str(process_pid),
        # We pass the initial children list to avoid the race condition where
        # the process_pid is terminated before the daemon starts and gets the
        # children list.
        '--initial-children',
        ','.join(map(str, initial_children)),
    ]

    # We do not need to set `start_new_session=True` here, as the
    # daemon script will detach itself from the parent process with
    # fork to avoid being killed by parent process. See the reason we
    # daemonize the process in `sky/skylet/subprocess_daemon.py`.
    subprocess.Popen(
        daemon_cmd,
        # Suppress output
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # Disable input
        stdin=subprocess.DEVNULL,
    )
