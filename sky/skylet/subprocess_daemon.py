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

    parent_process.wait()

    if not process.is_running():
        sys.exit()
    children = process.children(recursive=True)
    children.append(process)
    for pid in children:
        try:
            pid.terminate()
        except psutil.NoSuchProcess:
            pass

    # Wait 30s for the processes to exit gracefully.
    time.sleep(30)

    for pid in children:
        try:
            pid.kill()
        except psutil.NoSuchProcess:
            pass
