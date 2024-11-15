"""Sky subprocess daemon.
Wait for parent_pid to exit, then SIGTERM (or SIGKILL if needed) the child
processes of proc_pid.
"""
import argparse
import os
import sys
import time

import psutil


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


if __name__ == '__main__':
    daemonize()
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

    if process is not None and parent_process is not None:
        # Wait for either parent or target process to exit
        while process.is_running() and parent_process.is_running():
            try:
                tmp_children = process.children(recursive=True)
                if tmp_children:
                    children = tmp_children
            except psutil.NoSuchProcess:
                pass
            time.sleep(1)

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
