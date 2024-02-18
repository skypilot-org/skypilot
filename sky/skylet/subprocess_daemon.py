"""Sky subprocess daemon.

Wait for parent_pid to exit, then SIGTERM (or SIGKILL if needed) the child
processes of proc_pid.
"""

import argparse
import sys
import time

import psutil

if __name__ == '__main__':

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

    if parent_process is not None:
        # Wait for either parent or target process to exit.
        while process.is_running() and parent_process.is_running():
            time.sleep(1)

    try:
        children = process.children(recursive=True)
        children.append(process)
    except psutil.NoSuchProcess:
        sys.exit()

    for pid in children:
        try:
            pid.terminate()
        except psutil.NoSuchProcess:
            pass

    # Wait 30s for the processes to exit gracefully.
    time.sleep(30)

    # SIGKILL if they're still running.
    for pid in children:
        try:
            pid.kill()
        except psutil.NoSuchProcess:
            pass
