"""Sky logging utils.

This is a remote utility module that provides logging functionality.
"""
import io
import os
import selectors
import subprocess
import sys
import time
import tempfile
from typing import Iterator, List, Optional, Tuple, Union

from sky.skylet import job_lib


def redirect_process_output(proc, log_path, stream_logs, start_streaming_at=''):
    """Redirect the process's filtered stdout/stderr to both stream and file"""
    log_path = os.path.expanduser(log_path)
    dirname = os.path.dirname(log_path)
    os.makedirs(dirname, exist_ok=True)

    out_io = io.TextIOWrapper(proc.stdout,
                              encoding='utf-8',
                              newline='',
                              errors='replace')
    err_io = io.TextIOWrapper(proc.stderr,
                              encoding='utf-8',
                              newline='',
                              errors='replace')
    sel = selectors.DefaultSelector()
    sel.register(out_io, selectors.EVENT_READ)
    sel.register(err_io, selectors.EVENT_READ)

    stdout = ''
    stderr = ''

    start_streaming_flag = False
    with open(log_path, 'a') as fout:
        while len(sel.get_map()) > 0:
            events = sel.select()
            for key, _ in events:
                line = key.fileobj.readline()
                if not line:
                    # Unregister the io when EOF reached
                    sel.unregister(key.fileobj)
                    continue
                # Remove special characters to avoid cursor hidding
                line = line.replace('\x1b[?25l', '')
                if start_streaming_at in line:
                    start_streaming_flag = True
                if key.fileobj is out_io:
                    stdout += line
                    out_stream = sys.stdout
                else:
                    stderr += line
                    out_stream = sys.stderr
                if stream_logs and start_streaming_flag:
                    out_stream.write(line)
                    out_stream.flush()
                fout.write(line)
    return stdout, stderr


def run_with_log(
    cmd: List[str],
    log_path: str,
    stream_logs: bool = False,
    start_streaming_at: str = '',
    return_none: bool = False,
    check: bool = False,
    **kwargs,
) -> Union[None, Tuple[subprocess.Popen, str, str]]:
    """Runs a command and logs its output to a file.

    Retruns the process, stdout and stderr of the command.
      Note that the stdout and stderr is already decoded.
    """
    try:
        with subprocess.Popen(cmd,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              start_new_session=True,
                              **kwargs) as proc:
            proc_pid = os.getpgid(proc.pid)
            stdout, stderr = redirect_process_output(
                proc,
                log_path,
                stream_logs,
                start_streaming_at=start_streaming_at)
            proc.wait()
            if proc.returncode != 0 and check:
                raise RuntimeError('Command failed, please check the logs.')
            if return_none:
                return None
            return proc, stdout, stderr
    finally:
        # The proc can be defunct if the python program is killed. Here we
        # open a new subprocess to kill the process, SIGKILL the process group.
        # Adapted from ray/dashboard/modules/job/job_manager.py#L154
        subprocess.Popen(
            f'kill -9 -{proc_pid}',
            shell=True,
            # Suppress output
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def run_bash_command_with_log(bash_command: str,
                              log_path: str,
                              stream_logs: bool = False):
    with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
        fp.write(bash_command)
        fp.flush()
        script_path = fp.name
        run_with_log(
            f'/bin/bash {script_path}',
            log_path,
            stream_logs=stream_logs,
            return_none=True,
            # The script will be not found without this
            shell=True,
            check=True,
        )


def _follow_job_logs(file,
                     job_id: int,
                     sleep_sec: float = 0.5,
                     start_streaming_at: str = '') -> Iterator[str]:
    """Yield each line from a file as they are written.

    `sleep_sec` is the time to sleep after empty reads. """
    line = ''
    status = job_lib.query_job_status([job_id])[0]
    start_streaming = False
    while True:
        tmp = file.readline()
        if tmp is not None and tmp != '':
            line += tmp
            if '\n' in line or '\r' in line:
                if start_streaming_at in line:
                    start_streaming = True
                if start_streaming:
                    yield line
                line = ''
        else:
            # Reach the end of the file, check the status or sleep and
            # retry.

            # Auto-exit the log tailing, if the job has finished. Check
            # the job status before query again to avoid unfinished logs.
            if status not in [
                    job_lib.JobStatus.RUNNING, job_lib.JobStatus.PENDING
            ]:
                return

            if sleep_sec:
                time.sleep(sleep_sec)
            status = job_lib.query_job_status([job_id])[0]


def tail_logs(job_id: int, log_dir: Optional[str],
              status: Optional[job_lib.JobStatus]):
    if log_dir is None:
        print(f'Job {job_id} not found (see `sky queue`).', file=sys.stderr)
        return

    log_path = os.path.join(job_lib.SKY_REMOTE_LOGS_ROOT, log_dir, 'run.log')
    log_path = os.path.expanduser(log_path)
    if status in [job_lib.JobStatus.RUNNING, job_lib.JobStatus.PENDING]:
        try:
            with open(log_path, 'r', newline='') as log_file:
                # Using `_follow` instead of `tail -f` to streaming the whole
                # log and creating a new process for tail.
                for line in _follow_job_logs(
                        log_file,
                        job_id=job_id,
                        start_streaming_at='SKY INFO: Reserving task slots on'):
                    print(line, end='', flush=True)
        except KeyboardInterrupt:
            return
    else:
        with open(log_path, 'r') as f:
            print(f.read())
