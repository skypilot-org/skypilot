"""Sky subprocess daemon.

Wait for parent_pid to exit, then SIGTERM (or SIGKILL if needed) the child
processes of proc_pid.
"""

import psutil
import argparse
import time
import sys

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--proc-pid', type=int, required=True)
    args = parser.parse_args()

    parent_process = psutil.Process(args.parent_pid)
    process = psutil.Process(args.proc_pid)

    if process is None or parent_process is None:
        sys.exit()

    # Fetch children first to avoid target process termination.
    children = process.children(recursive=True)
    children.append(process)

    # Wait for either parent or target process to exit.
    while True:
        time.sleep(1)
        if not process.is_running() or not parent_process.is_running():
            break

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
