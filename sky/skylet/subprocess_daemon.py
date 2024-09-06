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
    """Separates the process from parent process with double-forking."""
    # First fork
    if os.fork() > 0:
        # original process terminates.
        sys.exit()

    # Continues to run from first forked child process.
    # Detach from parent environment
    os.setsid()

    # Second fork
    if os.fork() > 0:
        # The first forked child process terminates.
        sys.exit()
    # Continues to run from second forked child process.


if __name__ == '__main__':
    daemonize()
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--proc-pid', type=int, required=True)
    args = parser.parse_args()

    process = None
    parent_process = None
    try:
        process = psutil.Process(args.proc_pid)
        parent_process = psutil.Process(args.parent_pid)
    except psutil.NoSuchProcess:
        pass

    if process is None:
        sys.exit()

    children = []
    if parent_process is not None:
        # Wait for either parent or target process to exit.
        while process.is_running() and parent_process.is_running():
            try:
                # if process is terminated by the time reaching this line,
                # it returns an empty list.
                tmp_children = process.children(recursive=True)
                if tmp_children:
                    children = tmp_children
                    children.append(process)
            except psutil.NoSuchProcess:
                pass
            time.sleep(1)

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
