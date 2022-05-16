"""Sky subprocess daemon.

Wait for parent_pid to exit, then SIGTERM (or SIGKILL if needed) the child
processes of proc_pid.
"""

import psutil
import argparse
from ray.dashboard.modules.job.common import JobStatus
from ray.dashboard.modules.job.sdk import JobSubmissionClient
import requests
import sys
import time

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--proc-pid', type=int, required=True)
    parser.add_argument('--job-id', type=str, required=False)
    args = parser.parse_args()

    parent_process = psutil.Process(args.parent_pid)
    try:
        process = psutil.Process(args.proc_pid)
    except psutil.NoSuchProcess:
        process = None
        pass
    job_id = args.job_id

    # If Ray job id is passed in, wait until the job is done/cancelled/failed
    if job_id is None:
        try:
            client = JobSubmissionClient('http://127.0.0.1:8265')
            while True:
                status_info = client.get_job_status(job_id)
                status = status_info.status
                if status in {
                        JobStatus.SUCCEEDED, JobStatus.STOPPED, JobStatus.FAILED
                }:
                    break
                time.sleep(1)
        except requests.exceptions.ConnectionError as e:
            print(e)
            parent_process.wait()
    else:
        parent_process.wait()

    if process is None or not process.is_running():
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

    # SIGKILL if they're still running.
    for pid in children:
        try:
            pid.kill()
        except psutil.NoSuchProcess:
            pass
