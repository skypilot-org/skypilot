"""Sky subprocess daemon.
Wait for parent_pid to exit, then SIGTERM (or SIGKILL if needed) the child
processes of proc_pid.
"""
import argparse
import os
import signal
import sys
import time
from typing import List, Optional

import psutil

# Environment variable to enable kill_pg in subprocess daemon.
USE_KILL_PG_ENV_VAR = 'SKYPILOT_SUBPROCESS_DAEMON_KILL_PG'


def daemonize():
    """Detaches the process from its parent process with double-forking.

    This detachment is crucial in the context of SkyPilot and Ray job. When
    'sky cancel' is executed, it uses Ray's stop job API to terminate the job.
    Without daemonization, this subprocess_daemon process will still be a child
    of the parent process which would be terminated along with the parent
    process, ray::task or the cancel request for jobs, which is launched with
    Ray job. Daemonization ensures this process survives the 'sky cancel'
    command, allowing it to prevent orphaned processes of Ray job.
    """
    # First fork: Creates a child process identical to the parent
    if os.fork() > 0:
        # Parent process exits, allowing the child to run independently
        sys.exit()

    # Continues to run from first forked child process.
    # Detach from parent environment.
    os.setsid()

    # Second fork: Creates a grandchild process
    if os.fork() > 0:
        # First child exits, orphaning the grandchild
        sys.exit()
    # Continues execution in the grandchild process
    # This process is now fully detached from the original parent and terminal


def get_pgid_if_leader(pid) -> Optional[int]:
    """Get the process group ID of the target process if it is the leader."""
    try:
        pgid = os.getpgid(pid)
        # Only use process group if the target process is the leader. This is
        # to avoid killing the entire process group while the target process is
        # just a subprocess in the group.
        if pgid == pid:
            print(f'Process group {pgid} is the leader.')
            return pgid
        return None
    except Exception:  # pylint: disable=broad-except
        # Process group is only available in UNIX.
        return None


def kill_process_group(pgid: int) -> bool:
    """Kill the target process group."""
    try:
        print(f'Terminating process group {pgid}...')
        os.killpg(pgid, signal.SIGTERM)
    except Exception:  # pylint: disable=broad-except
        return False

    # Wait 30s for the process group to exit gracefully.
    time.sleep(30)

    try:
        print(f'Force killing process group {pgid}...')
        os.killpg(pgid, signal.SIGKILL)
    except Exception:  # pylint: disable=broad-except
        pass

    return True


def kill_process_tree(process: psutil.Process,
                      children: List[psutil.Process]) -> bool:
    """Kill the process tree of the target process."""
    if process is not None:
        # Kill the target process first to avoid having more children, or fail
        # the process due to the children being defunct.
        children = [process] + children

    if not children:
        sys.exit()

    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue

    # Wait 30s for the processes to exit gracefully.
    time.sleep(30)

    # SIGKILL if they're still running.
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue

    return True


def main():
    # daemonize()
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--proc-pid', type=int, required=True)
    parser.add_argument(
        '--initial-children',
        type=str,
        default='',
        help=(
            'Comma-separated list of initial children PIDs. This is to guard '
            'against the case where the target process has already terminated, '
            'while the children are still running.'),
    )
    args = parser.parse_args()

    process = None
    parent_process = None
    try:
        process = psutil.Process(args.proc_pid)
        parent_process = psutil.Process(args.parent_pid)
    except psutil.NoSuchProcess:
        pass

    # Initialize children list from arguments
    children = []
    if args.initial_children:
        for pid in args.initial_children.split(','):
            try:
                child = psutil.Process(int(pid))
                children.append(child)
            except (psutil.NoSuchProcess, ValueError):
                pass

    pgid: Optional[int] = None
    if os.environ.get(USE_KILL_PG_ENV_VAR) == '1':
        # Use kill_pg on UNIX system if allowed to reduce the resource usage.
        # Note that both implementations might leave subprocessed uncancelled:
        # - kill_process_tree(default): a subprocess is able to detach itself
        #   from the process tree use the same technique as daemonize(). Also,
        #   since we refresh the process tree per second, if the subprocess is
        #   launched between the [last_poll, parent_die] interval, the
        #   subprocess will not be captured will not be killed.
        # - kill_process_group: kill_pg will kill all the processed in the group
        #   but if a subprocess calls setpgid(0, 0) to detach itself from the
        #   process group (usually to daemonize itself), the subprocess will
        #   not be killed.
        pgid = get_pgid_if_leader(process.pid)

    if process is not None and parent_process is not None:
        # Wait for either parent or target process to exit
        while process.is_running() and parent_process.is_running():
            if pgid is None:
                # Refresh process tree for cleanup if process group is not
                # available.
                try:
                    tmp_children = process.children(recursive=True)
                    if tmp_children:
                        children = tmp_children
                except psutil.NoSuchProcess:
                    pass
            time.sleep(1)

    if pgid is not None:
        kill_process_group(pgid)
    else:
        kill_process_tree(process, children)


if __name__ == '__main__':
    main()
