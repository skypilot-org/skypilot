import io
import os
import selectors
import subprocess
import sys
import time
import tempfile
from typing import Iterator, List, Optional


def redirect_process_output(proc, log_path, stream_logs, start_streaming_at=''):
    """Redirect the process's filtered stdout/stderr to both stream and file"""
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


def run_with_log(cmd: List[str],
                 log_path: str,
                 stream_logs: bool = False,
                 start_streaming_at: str = '',
                 return_none: bool = False,
                 **kwargs):
    """Runs a command and logs its output to a file.

    Retruns the process, stdout and stderr of the command.
      Note that the stdout and stderr is already decoded.
    """
    with subprocess.Popen(cmd,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          **kwargs) as proc:
        stdout, stderr = redirect_process_output(
            proc, log_path, stream_logs, start_streaming_at=start_streaming_at)
        proc.wait()
        if return_none:
            return None
        return proc, stdout, stderr


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
        )


def tail_logs(job_id: str, log_dir: Optional[str], status: Optional[str]):
    if log_dir is None:
        print(f'Job {job_id} not found (see `sky queue`).', file=sys.stderr)
        return

    log_path = os.path.join(log_dir, 'run.log')
    if status in ['RUNNING', 'PENDING']:
        try:
            tail_cmd = f'tail -f {log_path} | sed \'/^SKY INFO: All tasks finished.$/ q\''
            run_with_log(tail_cmd.split(),
                         log_path='/dev/null',
                         stream_logs=True,
                         start_streaming_at='SKY INFO: All task slots reserved.')
        except KeyboardInterrupt:
            return
    else:
        with open(log_path, 'r') as f:
            print(f.read())
