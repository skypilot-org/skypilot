"""Sky logging utils.

This is a remote utility module that provides logging functionality.
"""
import io
import os
import selectors
import subprocess
import sys
import textwrap
import time
import tempfile
from typing import Iterator, List, Optional, Tuple, Union

from sky.skylet import job_lib

SKY_REMOTE_WORKDIR = '~/sky_workdir'


def redirect_process_output(proc,
                            log_path: str,
                            stream_logs: bool,
                            start_streaming_at: str = '',
                            skip_lines: Optional[List[str]] = None,
                            replace_crlf: bool = False) -> Tuple[str, str]:
    """Redirect the process's filtered stdout/stderr to both stream and file"""
    log_path = os.path.expanduser(log_path)
    dirname = os.path.dirname(log_path)
    os.makedirs(dirname, exist_ok=True)

    sel = selectors.DefaultSelector()
    out_io = io.TextIOWrapper(proc.stdout,
                              encoding='utf-8',
                              newline='',
                              errors='replace')
    sel.register(out_io, selectors.EVENT_READ)
    if proc.stderr is not None:
        err_io = io.TextIOWrapper(proc.stderr,
                                  encoding='utf-8',
                                  newline='',
                                  errors='replace')
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
                if replace_crlf and line.endswith('\r\n'):
                    # Replace CRLF with LF to avoid ray logging to the same line
                    # due to separating lines with '\n'.
                    line = line[:-2] + '\n'
                if (skip_lines is not None and
                        any(skip in line for skip in skip_lines)):
                    continue
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
                if log_path != '/dev/null':
                    fout.write(line)
                    fout.flush()
    return stdout, stderr


def run_with_log(
    cmd: Union[List[str], str],
    log_path: str,
    stream_logs: bool = False,
    start_streaming_at: str = '',
    return_code: bool = False,
    check: bool = False,
    shell: bool = False,
    with_ray: bool = False,
    **kwargs,
) -> Union[None, Tuple[subprocess.Popen, str, str]]:
    """Runs a command and logs its output to a file.

    Retruns the process, stdout and stderr of the command.
      Note that the stdout and stderr is already decoded.
    """
    # Redirect stderr to stdout when using ray, to preserve the order of
    # stdout and stderr.
    stderr = subprocess.PIPE if not with_ray else subprocess.STDOUT
    with subprocess.Popen(cmd,
                          stdout=subprocess.PIPE,
                          stderr=stderr,
                          start_new_session=True,
                          shell=shell,
                          **kwargs) as proc:
        # The proc can be defunct if the python program is killed. Here we
        # open a new subprocess to gracefully kill the proc, SIGTERM
        # and then SIGKILL the process group.
        # Adapted from ray/dashboard/modules/job/job_manager.py#L154
        parent_pid = os.getpid()
        daemon_script = os.path.join(
            os.path.dirname(os.path.abspath(job_lib.__file__)),
            'subprocess_daemon.sh')
        daemon_cmd = [
            '/bin/bash', daemon_script,
            str(parent_pid),
            str(proc.pid)
        ]
        subprocess.Popen(
            daemon_cmd,
            start_new_session=True,
            # Suppress output
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # We need this even if the log_path is '/dev/null' to ensure the
        # progress bar is shown.
        stdout, stderr = redirect_process_output(
            proc,
            log_path,
            stream_logs,
            start_streaming_at=start_streaming_at,
            # Skip these lines caused by `-i` option of bash. Failed to find
            # other way to turn off these two warning.
            # https://stackoverflow.com/questions/13300764/how-to-tell-bash-not-to-issue-warnings-cannot-set-terminal-process-group-and # pylint: disable=line-too-long
            skip_lines=[
                'bash: cannot set terminal process group',
                'bash: no job control in this shell',
            ],
            # Replace CRLF when the output is logged to driver by ray.
            replace_crlf=with_ray,
        )
        proc.wait()
        if proc.returncode and check:
            if stderr:
                print(stderr, file=sys.stderr)
            raise subprocess.CalledProcessError(proc.returncode, cmd)
        if return_code:
            return proc.returncode
        return proc, stdout, stderr


def make_task_bash_script(codegen: str) -> str:
    # set -a is used for exporting all variables functions to the environment
    # so that bash `user_script` can access `conda activate`. Detail: #436.
    # Reference: https://www.gnu.org/software/bash/manual/html_node/The-Set-Builtin.html # pylint: disable=line-too-long
    script = [
        textwrap.dedent(f"""\
                #!/bin/bash
                source ~/.bashrc
                set -a
                . $(conda info --base)/etc/profile.d/conda.sh 2> /dev/null || true
                set +a
                cd {SKY_REMOTE_WORKDIR}"""),
        codegen,
    ]
    script = '\n'.join(script)
    return script


def run_bash_command_with_log(bash_command: str,
                              log_path: str,
                              export_sky_env_vars: Optional[str] = None,
                              stream_logs: bool = False,
                              with_ray: bool = False):
    with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
        if export_sky_env_vars is not None:
            bash_command = export_sky_env_vars + '\n' + bash_command
        bash_command = make_task_bash_script(bash_command)
        fp.write(bash_command)
        fp.flush()
        script_path = fp.name
        return run_with_log(
            # Need this `-i` option to make sure `source ~/.bashrc` work.
            # Do not use shell=True because it will cause the environment
            # set in this task visible to other tasks. shell=False requires
            # the cmd to be a list.
            ['/bin/bash', '-i', script_path],
            log_path,
            stream_logs=stream_logs,
            return_code=True,
            with_ray=with_ray,
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
                    # TODO(zhwu): Consider using '\33[2K' to clear the
                    # line when line endswith '\r' (to avoid previous line
                    # to long problem). `colorama.ansi.clear_line`
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
    # TODO(zhwu): Maybe switch to `ray job logs` according to the performance.
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
