"""Sky subprocess daemon.

Wait for parent_pid to exit, then SIGTERM (or SIGKILL if needed) the child
processes of proc_pid.
"""

import argparse
from ray.dashboard.modules.job import common as job_common
from ray.dashboard.modules.job import sdk as job_sdk
import requests
import sys
import time

import psutil

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--proc-pid', type=int, required=True)
    parser.add_argument('--job-id', type=str, required=False)
    args = parser.parse_args()
    job_id = args.job_id

    process = None
    parent_process = None
    try:
        process = psutil.Process(args.proc_pid)
        parent_process = psutil.Process(args.parent_pid)
    except psutil.NoSuchProcess:
        pass

    if process is None:
        sys.exit()

    wait_for_process = False
    # If Ray job id is passed in, wait until the job is done/cancelled/failed
    if job_id is None:
        wait_for_process = True
    else:
        try:
            client = job_sdk.JobSubmissionClient('http://127.0.0.1:8265')
            while True:
                status_info = client.get_job_status(job_id)
                status = status_info.status
                if status in {
                        job_common.JobStatus.SUCCEEDED,
                        job_common.JobStatus.STOPPED,
                        job_common.JobStatus.FAILED
                }:
                    break
                time.sleep(1)
        except requests.exceptions.ConnectionError as e:
            print(e)
            wait_for_process = True

    if wait_for_process and parent_process is not None:
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
