"""Utility functions for subprocesses."""
import collections
import multiprocessing
from multiprocessing import pool
import os
import random
import resource
import shlex
import subprocess
import sys
import termios
import threading
import time
import typing
from typing import (Any, Callable, Dict, List, Optional, Protocol, Set, Tuple,
                    Union)

import colorama

from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.skylet import log_lib
from sky.skylet import subprocess_daemon
from sky.utils import common_utils
from sky.utils import timeline
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import psutil
else:
    psutil = adaptors_common.LazyImport('psutil')

logger = sky_logging.init_logger(__name__)

_fd_limit_warning_shown = False

_PROCFS = '/proc'


def _safe_children(process: 'psutil.Process') -> List['psutil.Process']:
    """Tolerant wrapper around ``process.children(recursive=True)``."""
    try:
        return process.children(recursive=True)
    except psutil.NoSuchProcess:
        # Parent process exited, skip
        return []
    except psutil.AccessDenied as e:
        # process.children will lookup all processes in the same PID namespace,
        # and we will encounter this error if there is a process we cannot
        # access. Log and ignore it with _fallback_children()
        bad_pid = getattr(e, 'pid', None)
        diag = _pid_diag(bad_pid) if bad_pid is not None else {}
        logger.warning(
            f'psutil.Process.children() hit AccessDenied '
            f'(pid={bad_pid} uid={diag.get("uid", "?")} '
            f'exe={diag.get("exe", "?")}); falling back to /proc walk '
            f'that ignores unreadable PIDs.')
        # Snapshot the parent's create_time so the fallback can reject any
        # PID whose create_time predates the parent
        try:
            parent_ctime = process.create_time()
        except psutil.Error:
            return []
        return _fallback_children(process.pid, parent_ctime)


def _pid_diag(pid: int) -> Dict[str, str]:
    """Best-effort identification of a PID whose /proc/<pid>/stat read
    was just denied for some reasons."""
    diag: Dict[str, str] = {}
    try:
        st = os.stat(f'{_PROCFS}/{pid}')
        diag['uid'] = str(st.st_uid)
    except OSError:
        pass
    try:
        diag['exe'] = os.readlink(f'{_PROCFS}/{pid}/exe')
    except OSError:
        pass
    return diag


def _fallback_children(parent_pid: int,
                       parent_ctime: float) -> List['psutil.Process']:
    """Resilient descendant walk: read /proc/<N>/stat, ignore failures.

    Mirrors the DFS in ``psutil.Process.children(recursive=True)`` but
    silently drops PIDs whose stat we cannot read (the very condition
    that brought us here). ``parent_ctime`` is the parent's create_time;
    descendants whose create_time predates it are PID-reuse phantoms and
    are excluded. Linux only; on other platforms returns [] — if psutil
    itself failed there, we have no better source.
    """
    if not sys.platform.startswith('linux'):
        return []
    try:
        entries = os.listdir(_PROCFS)
    except OSError:
        return []
    ppid_map: Dict[int, int] = {}
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f'{_PROCFS}/{pid}/stat', 'rb') as f:
                data = f.read()
        except OSError:
            # FileNotFoundError, ProcessLookupError, PermissionError —
            # any read failure: skip this PID and keep walking the rest.
            continue
        try:
            # stat format: "<pid> (<comm>) <state> <ppid> ..."; comm can
            # contain spaces and ')', so locate the *last* ')'.
            rpar = data.rfind(b')')
            ppid_map[pid] = int(data[rpar + 2:].split()[1])
        except (ValueError, IndexError):
            continue
    # Traversal pattern (DFS via deque.pop) matches the upstream
    # psutil.Process.children(recursive=True) implementation; order does
    # not matter for our callers, which kill the whole subtree anyway.
    children_of: Dict[int, List[int]] = {}
    for pid, ppid in ppid_map.items():
        children_of.setdefault(ppid, []).append(pid)
    descendants: List['psutil.Process'] = []
    seen: Set[int] = set()
    stack: 'collections.deque[int]' = collections.deque([parent_pid])
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for child_pid in children_of.get(cur, []):
            try:
                child = psutil.Process(child_pid)
                # PID-reuse defense
                if parent_ctime <= child.create_time():
                    descendants.append(child)
                    stack.append(child_pid)
            except psutil.Error:
                # NoSuchProcess / ZombieProcess / AccessDenied — in this
                # degraded LSM fallback we drop the PID and keep walking
                # so kill_children_processes can still clean up the rest.
                pass
    return descendants


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
                    args: Union[List[Any], Set[Any]],
                    num_threads: Optional[int] = None) -> List[Any]:
    """Run a function in parallel on a list of arguments.

    Args:
        func: The function to run in parallel
        args: Iterable of arguments to pass to func
        num_threads: Number of threads to use. If None, uses
          get_parallel_threads()

    Returns:
      A list of the return values of the function func, in the same order as the
        arguments.

    Raises:
        Exception: The first exception encountered.
    """
    # Short-circuit for short lists
    if len(args) == 0:
        return []
    if len(args) == 1:
        return [func(list(args)[0])]

    processes = (num_threads
                 if num_threads is not None else get_parallel_threads())
    # len(args) is guaranteed to be greater than 1.
    processes = min(processes, len(args))
    with pool.ThreadPool(processes=processes) as p:
        ordered_iterators = p.imap(func, args)
        return list(ordered_iterators)


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


def kill_children_processes(parent_pids: Optional[Union[
    int, List[Optional[int]]]] = None,
                            force: bool = False) -> None:
    """Kill children processes recursively.

    We need to kill the children, so that
    1. The underlying subprocess will not print the logs to the terminal,
       after this program exits.
    2. The underlying subprocess will not continue with starting a cluster
       etc. while we are cleaning up the clusters.

    Args:
        parent_pids: Optional PIDs of a series of processes. The processes and
          their children will be killed.  If a list of PID is specified, it is
          killed by the order in the list. This is for guaranteeing the order
          of cleaning up and suppress flaky errors.
        force: bool, send SIGKILL if force, otherwise, use SIGTERM for
          gracefully kill the process.
    """
    if isinstance(parent_pids, int):
        parent_pids = [parent_pids]

    parent_processes = []
    if parent_pids is None:
        parent_processes = [psutil.Process()]
    else:
        for pid in parent_pids:
            try:
                process = psutil.Process(pid)
            except psutil.NoSuchProcess:
                continue
            parent_processes.append(process)

    for parent_process in parent_processes:
        # _safe_children() so that an EACCES on a single unrelated PID
        # (hardened-host LSM policy) does not abort the whole cleanup and
        # leave children behind. The explicitly given parent_process is
        # killed regardless of enumeration results.
        child_processes = _safe_children(parent_process)
        if parent_pids is not None:
            kill_process_with_grace_period(parent_process, force=force)
        logger.debug(f'Killing child processes: {child_processes}')
        for child in child_processes:
            kill_process_with_grace_period(child, force=force)


GenericProcess = Union[multiprocessing.Process, psutil.Process,
                       subprocess.Popen]


def _wait_non_child_process(proc: 'psutil.Process', grace_period: int) -> None:
    """Wait for a non-child process to exit by polling its status.

    psutil.Process.wait() internally polls kill(pid, 0) for non-children,
    which always succeeds for zombies, causing it to block for the full
    timeout. This is a problem in containers without a proper init (e.g.
    no tini) where PID 1 does not reap orphaned zombies.

    Instead, we poll proc.status() which correctly distinguishes zombies
    (process exited but not reaped) from running processes.
    """
    deadline = time.monotonic() + grace_period
    while time.monotonic() < deadline:
        try:
            if proc.status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(0.1)
    raise psutil.TimeoutExpired(grace_period, pid=proc.pid)


def kill_process_with_grace_period(proc: GenericProcess,
                                   force: bool = False,
                                   grace_period: int = 10) -> None:
    """Kill a process with SIGTERM and wait for it to exit.

    Args:
        proc: The process to kill, either a multiprocessing.Process or a
            psutil.Process.
        force: Whether to force kill the process.
        grace_period: The grace period seconds to wait for the process to exit.
    """
    if isinstance(proc, psutil.Process):
        alive = proc.is_running
        wait = proc.wait
    elif isinstance(proc, subprocess.Popen):
        alive = lambda: proc.poll() is None
        wait = proc.wait
    else:
        alive = proc.is_alive
        wait = proc.join
    if not alive():
        # Skip if the process is not running.
        return
    logger.debug(f'Killing process {proc.pid}')
    try:
        if force:
            proc.kill()
        else:
            proc.terminate()
        if isinstance(proc, psutil.Process) and proc.ppid() != os.getpid():
            # Not our child — use status polling instead of psutil.wait()
            # which hangs on zombies (see _wait_non_child_process).
            _wait_non_child_process(proc, grace_period)
        else:
            wait(timeout=grace_period)
    except (psutil.NoSuchProcess, ValueError):
        # The child process may have already been terminated.
        return
    except (psutil.TimeoutExpired, subprocess.TimeoutExpired):
        logger.debug(f'Process {proc.pid} did not terminate after '
                     f'{grace_period} seconds')
    finally:
        # Escalate to SIGKILL if we only tried SIGTERM and the process is
        # still around. The finally block is needed because
        # multiprocessing.Process.join() returns None on timeout and
        # subprocess.Popen.wait() raises subprocess.TimeoutExpired
        # (not psutil.TimeoutExpired).
        if not force and alive():
            logger.debug(f'Force killing process {proc.pid}')
            kill_process_with_grace_period(proc, force=True, grace_period=5)


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


def kill_process_daemon(process_pid: int, use_kill_pg: bool = False) -> None:
    """Start a daemon as a safety net to kill the process.

    Args:
        process_pid: The PID of the process to kill.
        use_kill_pg: Whether to use kill process group to kill the process. If
            True, the process will use os.killpg() to kill the target process
            group on UNIX system, which is more efficient than using the daemon
            to refresh the process tree in the daemon. Note that both
            implementations have corner cases where subprocesses might not be
            killed. Refer to subprocess_daemon.py for more details.
    """
    # Get initial children list via _safe_children so that an EACCES from
    # a hardened-host LSM policy doesn't abort us before the daemon is
    # registered. See _safe_children for the failure mode and rationale.
    try:
        process = psutil.Process(process_pid)
    except psutil.NoSuchProcess:
        initial_children: List[int] = []
    else:
        initial_children = [p.pid for p in _safe_children(process)]

    parent_pid = os.getpid()
    daemon_script = os.path.join(
        os.path.dirname(os.path.abspath(log_lib.__file__)),
        'subprocess_daemon.py')
    daemon_cmd = [
        sys.executable,
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

    env = os.environ.copy()
    if use_kill_pg:
        env[subprocess_daemon.USE_KILL_PG_ENV_VAR] = '1'

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
        env=env,
    )


def launch_new_process_tree(cmd: str, log_output: str = '/dev/null') -> int:
    """Launch a new process that will not be a child of the current process.

    This will launch bash in a new session, which will launch the given cmd.
    This will ensure that cmd is in its own process tree, and once bash exits,
    will not be an ancestor of the current process. This is useful for job
    launching.

    Returns the pid of the launched cmd.
    """
    # Use nohup to ensure the job driver process is a separate process tree,
    # instead of being a child of the current process. This is important to
    # avoid a chain of driver processes (job driver can call schedule_step() to
    # submit new jobs, and the new job can also call schedule_step()
    # recursively).
    #
    # echo $! will output the PID of the last background process started in the
    # current shell, so we can retrieve it and record in the DB.
    #
    # TODO(zhwu): A more elegant solution is to use another daemon process to be
    # in charge of starting these driver processes, instead of starting them in
    # the current process.
    wrapped_cmd = (f'nohup bash -c {shlex.quote(cmd)} '
                   f'</dev/null >{log_output} 2>&1 & echo $!')
    proc = subprocess.run(wrapped_cmd,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL,
                          start_new_session=True,
                          check=True,
                          shell=True,
                          text=True)
    # Get the PID of the detached process
    return int(proc.stdout.strip())


# A protocol for objects that can be started, designed to be used with
# slow_start_processes() so that we can handle different wrappers of
# multiprocessing.Process in a uniform way.
class Startable(Protocol):

    def start(self) -> None:
        ...


OnStartFn = Callable[[Startable], None]


def slow_start_processes(processes: List[Startable],
                         delay: float = 2.0,
                         on_start: Optional[OnStartFn] = None,
                         should_exit: Optional[threading.Event] = None) -> None:
    """Start processes with slow start.

    Profile shows that it takes 1~2 seconds to start a worker process when
    CPU is relatively idle. However, starting all workers simultaneously will
    overwhelm the CPU and cause the time for the first worker to be ready to
    be delayed. Slow start start a group of workers slowly to accelerate the
    start time (i.e. the time for the first worker to be ready), while
    gradually increasing the batch size in exponential manner to make the
    time of achieving full parallelism as short as possible.

    Args:
        processes: The list of processes to start.
        delay: The delay between starting each process, default to 2.0 seconds,
            based on profile.
        on_start: An optional function to callback when a process starts.
        should_exit: An optional event to check if the function should exit
            before starting all the processes.
    """
    max_batch_size = max(1, int(common_utils.get_cpu_count() / 2))
    batch_size = 1
    left = len(processes)
    while left > 0:
        if should_exit and should_exit.is_set():
            break
        current_batch = min(batch_size, left)
        for i in range(current_batch):
            worker_idx = len(processes) - left + i
            processes[worker_idx].start()
            if on_start:
                on_start(processes[worker_idx])
        left -= current_batch
        if left <= 0:
            break
        batch_size = min(batch_size * 2, max_batch_size)
        time.sleep(delay)


def is_echo_disabled(fd: int) -> bool:
    """Check if terminal ECHO is disabled on the given fd.

    When a subprocess wants password/sensitive input, it disables ECHO.
    This is how pexpect's waitnoecho() works. See:
    https://pexpect.readthedocs.io/en/stable/api/pexpect.html#pexpect.spawn.waitnoecho
    """
    assert os.isatty(fd), 'fd is not connected to a terminal'
    try:
        attr = termios.tcgetattr(fd)
        echo_on = bool(attr[3] & termios.ECHO)
        return not echo_on
    except (termios.error, OSError):
        return False
