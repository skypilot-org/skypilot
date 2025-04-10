"""Sky logging library.

This is a remote utility module that provides logging functionality.
"""
import collections
import copy
import io
import multiprocessing.pool
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import time
from typing import (Deque, Dict, Iterable, Iterator, List, Optional, TextIO,
                    Tuple, Union)

import colorama

from sky import sky_logging
from sky.skylet import constants
from sky.skylet import job_lib
from sky.utils import log_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

SKY_LOG_WAITING_GAP_SECONDS = 1
SKY_LOG_WAITING_MAX_RETRY = 5
SKY_LOG_TAILING_GAP_SECONDS = 0.2
# Peek the head of the lines to check if we need to start
# streaming when tail > 0.
PEEK_HEAD_LINES_FOR_START_STREAM = 20

logger = sky_logging.init_logger(__name__)

LOG_FILE_START_STREAMING_AT = 'Waiting for task resources on '


class _ProcessingArgs:
    """Arguments for processing logs."""

    def __init__(self,
                 log_path: str,
                 stream_logs: bool,
                 start_streaming_at: str = '',
                 end_streaming_at: Optional[str] = None,
                 skip_lines: Optional[List[str]] = None,
                 replace_crlf: bool = False,
                 line_processor: Optional[log_utils.LineProcessor] = None,
                 streaming_prefix: Optional[str] = None) -> None:
        self.log_path = log_path
        self.stream_logs = stream_logs
        self.start_streaming_at = start_streaming_at
        self.end_streaming_at = end_streaming_at
        self.skip_lines = skip_lines
        self.replace_crlf = replace_crlf
        self.line_processor = line_processor
        self.streaming_prefix = streaming_prefix


def _handle_io_stream(io_stream, out_stream, args: _ProcessingArgs):
    """Process the stream of a process."""
    out_io = io.TextIOWrapper(io_stream,
                              encoding='utf-8',
                              newline='',
                              errors='replace',
                              write_through=True)

    start_streaming_flag = False
    end_streaming_flag = False
    streaming_prefix = args.streaming_prefix if args.streaming_prefix else ''
    line_processor = (log_utils.LineProcessor()
                      if args.line_processor is None else args.line_processor)

    out = []
    with open(args.log_path, 'a', encoding='utf-8') as fout:
        with line_processor:
            while True:
                line = out_io.readline()
                if not line:
                    break
                # start_streaming_at logic in processor.process_line(line)
                if args.replace_crlf and line.endswith('\r\n'):
                    # Replace CRLF with LF to avoid ray logging to the same
                    # line due to separating lines with '\n'.
                    line = line[:-2] + '\n'
                if (args.skip_lines is not None and
                        any(skip in line for skip in args.skip_lines)):
                    continue
                if args.start_streaming_at in line:
                    start_streaming_flag = True
                if (args.end_streaming_at is not None and
                        args.end_streaming_at in line):
                    # Keep executing the loop, only stop streaming.
                    # E.g., this is used for `sky bench` to hide the
                    # redundant messages of `sky launch` while
                    # saving them in log files.
                    end_streaming_flag = True
                if (args.stream_logs and start_streaming_flag and
                        not end_streaming_flag):
                    print(streaming_prefix + line,
                          end='',
                          file=out_stream,
                          flush=True)
                if args.log_path != '/dev/null':
                    fout.write(line)
                    fout.flush()
                line_processor.process_line(line)
                out.append(line)
    return ''.join(out)


def process_subprocess_stream(proc, args: _ProcessingArgs) -> Tuple[str, str]:
    """Redirect the process's filtered stdout/stderr to both stream and file"""
    if proc.stderr is not None:
        # Asyncio does not work as the output processing can be executed in a
        # different thread.
        # selectors is possible to handle the multiplexing of stdout/stderr,
        # but it introduces buffering making the output not streaming.
        with multiprocessing.pool.ThreadPool(processes=1) as pool:
            err_args = copy.copy(args)
            err_args.line_processor = None
            stderr_fut = pool.apply_async(_handle_io_stream,
                                          args=(proc.stderr, sys.stderr,
                                                err_args))
            # Do not launch a thread for stdout as the rich.status does not
            # work in a thread, which is used in
            # log_utils.RayUpLineProcessor.
            stdout = _handle_io_stream(proc.stdout, sys.stdout, args)
            stderr = stderr_fut.get()
    else:
        stdout = _handle_io_stream(proc.stdout, sys.stdout, args)
        stderr = ''
    return stdout, stderr


def run_with_log(
    cmd: Union[List[str], str],
    log_path: str,
    *,
    require_outputs: bool = False,
    stream_logs: bool = False,
    start_streaming_at: str = '',
    end_streaming_at: Optional[str] = None,
    skip_lines: Optional[List[str]] = None,
    shell: bool = False,
    with_ray: bool = False,
    process_stream: bool = True,
    line_processor: Optional[log_utils.LineProcessor] = None,
    streaming_prefix: Optional[str] = None,
    log_cmd: bool = False,
    **kwargs,
) -> Union[int, Tuple[int, str, str]]:
    """Runs a command and logs its output to a file.

    Args:
        cmd: The command to run.
        log_path: The path to the log file.
        stream_logs: Whether to stream the logs to stdout/stderr.
        require_outputs: Whether to return the stdout/stderr of the command.
        process_stream: Whether to post-process the stdout/stderr of the
            command, such as replacing or skipping lines on the fly. If
            enabled, lines are printed only when '\r' or '\n' is found.

    Returns the returncode or returncode, stdout and stderr of the command.
      Note that the stdout and stderr is already decoded.
    """
    assert process_stream or not require_outputs, (
        process_stream, require_outputs,
        'require_outputs should be False when process_stream is False')

    log_path = os.path.expanduser(log_path)
    dirname = os.path.dirname(log_path)
    os.makedirs(dirname, exist_ok=True)
    # Redirect stderr to stdout when using ray, to preserve the order of
    # stdout and stderr.
    stdout_arg = stderr_arg = None
    if process_stream:
        stdout_arg = subprocess.PIPE
        stderr_arg = subprocess.PIPE if not with_ray else subprocess.STDOUT
    # Use stdin=subprocess.DEVNULL by default, as allowing inputs will mess up
    # the terminal output when typing in the terminal that starts the API
    # server.
    stdin = kwargs.pop('stdin', subprocess.DEVNULL)
    if log_cmd:
        with open(log_path, 'a', encoding='utf-8') as f:
            print(f'Running command: {cmd}', file=f)
    with subprocess.Popen(cmd,
                          stdout=stdout_arg,
                          stderr=stderr_arg,
                          start_new_session=True,
                          shell=shell,
                          stdin=stdin,
                          **kwargs) as proc:
        try:
            subprocess_utils.kill_process_daemon(proc.pid)
            stdout = ''
            stderr = ''

            if process_stream:
                if skip_lines is None:
                    skip_lines = []
                # Skip these lines caused by `-i` option of bash. Failed to
                # find other way to turn off these two warning.
                # https://stackoverflow.com/questions/13300764/how-to-tell-bash-not-to-issue-warnings-cannot-set-terminal-process-group-and # pylint: disable=line-too-long
                # `ssh -T -i -tt` still cause the problem.
                skip_lines += [
                    'bash: cannot set terminal process group',
                    'bash: no job control in this shell',
                ]
                # We need this even if the log_path is '/dev/null' to ensure the
                # progress bar is shown.
                # NOTE: Lines are printed only when '\r' or '\n' is found.
                args = _ProcessingArgs(
                    log_path=log_path,
                    stream_logs=stream_logs,
                    start_streaming_at=start_streaming_at,
                    end_streaming_at=end_streaming_at,
                    skip_lines=skip_lines,
                    line_processor=line_processor,
                    # Replace CRLF when the output is logged to driver by ray.
                    replace_crlf=with_ray,
                    streaming_prefix=streaming_prefix,
                )
                stdout, stderr = process_subprocess_stream(proc, args)
            proc.wait()
            if require_outputs:
                return proc.returncode, stdout, stderr
            return proc.returncode
        except KeyboardInterrupt:
            # Kill the subprocess directly, otherwise, the underlying
            # process will only be killed after the python program exits,
            # causing the stream handling stuck at `readline`.
            subprocess_utils.kill_children_processes()
            raise


def make_task_bash_script(codegen: str,
                          env_vars: Optional[Dict[str, str]] = None) -> str:
    # set -a is used for exporting all variables functions to the environment
    # so that bash `user_script` can access `conda activate`. Detail: #436.
    # Reference: https://www.gnu.org/software/bash/manual/html_node/The-Set-Builtin.html # pylint: disable=line-too-long
    # DEACTIVATE_SKY_REMOTE_PYTHON_ENV: Deactivate the SkyPilot runtime env, as
    # the ray cluster is started within the runtime env, which may cause the
    # user program to run in that env as well.
    # PYTHONUNBUFFERED is used to disable python output buffering.
    script = [
        textwrap.dedent(f"""\
            #!/bin/bash
            source ~/.bashrc
            set -a
            . $(conda info --base 2> /dev/null)/etc/profile.d/conda.sh > /dev/null 2>&1 || true
            set +a
            {constants.DEACTIVATE_SKY_REMOTE_PYTHON_ENV}
            export PYTHONUNBUFFERED=1
            cd {constants.SKY_REMOTE_WORKDIR}"""),
    ]
    if env_vars is not None:
        for k, v in env_vars.items():
            script.append(f'export {k}={shlex.quote(str(v))}')
    script += [
        codegen,
        '',  # New line at EOF.
    ]
    script = '\n'.join(script)
    return script


def add_ray_env_vars(
        env_vars: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    # Adds Ray-related environment variables.
    if env_vars is None:
        env_vars = {}
    ray_env_vars = [
        'CUDA_VISIBLE_DEVICES', 'RAY_CLIENT_MODE', 'RAY_JOB_ID',
        'RAY_RAYLET_PID', 'OMP_NUM_THREADS'
    ]
    env_dict = dict(os.environ)
    for env_var in ray_env_vars:
        if env_var in env_dict:
            env_vars[env_var] = env_dict[env_var]
    return env_vars


def run_bash_command_with_log(bash_command: str,
                              log_path: str,
                              env_vars: Optional[Dict[str, str]] = None,
                              stream_logs: bool = False,
                              with_ray: bool = False):
    with tempfile.NamedTemporaryFile('w', prefix='sky_app_',
                                     delete=False) as fp:
        bash_command = make_task_bash_script(bash_command, env_vars=env_vars)
        fp.write(bash_command)
        fp.flush()
        script_path = fp.name

        # Need this `-i` option to make sure `source ~/.bashrc` work.
        inner_command = f'/bin/bash -i {script_path}'

        return run_with_log(inner_command,
                            log_path,
                            stream_logs=stream_logs,
                            with_ray=with_ray,
                            shell=True)


def _follow_job_logs(file,
                     job_id: int,
                     start_streaming: bool,
                     start_streaming_at: str = '') -> Iterator[str]:
    """Yield each line from a file as they are written.

    `sleep_sec` is the time to sleep after empty reads. """
    line = ''
    # No need to lock the status here, as the while loop can handle
    # the older status.
    status = job_lib.get_status_no_lock(job_id)
    wait_last_logs = True
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
                    job_lib.JobStatus.SETTING_UP, job_lib.JobStatus.PENDING,
                    job_lib.JobStatus.RUNNING
            ]:
                if wait_last_logs:
                    # Wait all the logs are printed before exit.
                    time.sleep(1 + SKY_LOG_TAILING_GAP_SECONDS)
                    wait_last_logs = False
                    continue
                status_str = status.value if status is not None else 'None'
                print(ux_utils.finishing_message(
                    f'Job finished (status: {status_str}).'),
                      flush=True)
                return

            time.sleep(SKY_LOG_TAILING_GAP_SECONDS)
            status = job_lib.get_status_no_lock(job_id)


def _peek_head_lines(log_file: TextIO) -> List[str]:
    """Peek the head of the file."""
    lines = [
        log_file.readline() for _ in range(PEEK_HEAD_LINES_FOR_START_STREAM)
    ]
    # Reset the file pointer to the beginning
    log_file.seek(0, os.SEEK_SET)
    return [line for line in lines if line]


def _should_stream_the_whole_tail_lines(head_lines_of_log_file: List[str],
                                        tail_lines: Deque[str],
                                        start_stream_at: str) -> bool:
    """Check if the entire tail lines should be streamed."""
    # See comment:
    # https://github.com/skypilot-org/skypilot/pull/4241#discussion_r1833611567
    # for more details.
    # Case 1: If start_stream_at is found at the head of the tail lines,
    # we should not stream the whole tail lines.
    for index, line in enumerate(tail_lines):
        if index >= PEEK_HEAD_LINES_FOR_START_STREAM:
            break
        if start_stream_at in line:
            return False
    # Case 2: If start_stream_at is found at the head of log file, but not at
    # the tail lines, we need to stream the whole tail lines.
    for line in head_lines_of_log_file:
        if start_stream_at in line:
            return True
    # Case 3: If start_stream_at is not at the head, and not found at the tail
    # lines, we should not stream the whole tail lines.
    return False


def tail_logs(job_id: Optional[int],
              log_dir: Optional[str],
              managed_job_id: Optional[int] = None,
              follow: bool = True,
              tail: int = 0) -> None:
    """Tail the logs of a job.

    Args:
        job_id: The job id.
        log_dir: The log directory of the job.
        managed_job_id: The managed job id (for logging info only to avoid
            confusion).
        follow: Whether to follow the logs or print the logs so far and exit.
        tail: The number of lines to display from the end of the log file,
            if 0, print all lines.
    """
    if job_id is None:
        # This only happens when job_lib.get_latest_job_id() returns None,
        # which means no job has been submitted to this cluster. See
        # sky.skylet.job_lib.JobLibCodeGen.tail_logs for more details.
        logger.info('Skip streaming logs as no job has been submitted.')
        return
    job_str = f'job {job_id}'
    if managed_job_id is not None:
        job_str = f'managed job {managed_job_id}'
    if log_dir is None:
        print(f'{job_str.capitalize()} not found (see `sky queue`).',
              file=sys.stderr)
        return
    logger.debug(f'Tailing logs for job, real job_id {job_id}, managed_job_id '
                 f'{managed_job_id}.')
    log_path = os.path.join(log_dir, 'run.log')
    log_path = os.path.expanduser(log_path)

    status = job_lib.update_job_status([job_id], silent=True)[0]

    # Wait for the log to be written. This is needed due to the `ray submit`
    # will take some time to start the job and write the log.
    retry_cnt = 0
    while status is not None and not status.is_terminal():
        retry_cnt += 1
        if os.path.exists(log_path) and status != job_lib.JobStatus.INIT:
            break
        if retry_cnt >= SKY_LOG_WAITING_MAX_RETRY:
            print(
                f'{colorama.Fore.RED}ERROR: Logs for '
                f'{job_str} (status: {status.value}) does not exist '
                f'after retrying {retry_cnt} times.{colorama.Style.RESET_ALL}')
            return
        print(f'INFO: Waiting {SKY_LOG_WAITING_GAP_SECONDS}s for the logs '
              'to be written...')
        time.sleep(SKY_LOG_WAITING_GAP_SECONDS)
        status = job_lib.update_job_status([job_id], silent=True)[0]

    start_stream_at = LOG_FILE_START_STREAMING_AT
    # Explicitly declare the type to avoid mypy warning.
    lines: Iterable[str] = []
    if follow and status in [
            job_lib.JobStatus.SETTING_UP,
            job_lib.JobStatus.PENDING,
            job_lib.JobStatus.RUNNING,
    ]:
        # Not using `ray job logs` because it will put progress bar in
        # multiple lines.
        with open(log_path, 'r', newline='', encoding='utf-8') as log_file:
            # Using `_follow` instead of `tail -f` to streaming the whole
            # log and creating a new process for tail.
            start_streaming = False
            if tail > 0:
                head_lines_of_log_file = _peek_head_lines(log_file)
                lines = collections.deque(log_file, maxlen=tail)
                start_streaming = _should_stream_the_whole_tail_lines(
                    head_lines_of_log_file, lines, start_stream_at)
                for line in lines:
                    if start_stream_at in line:
                        start_streaming = True
                    if start_streaming:
                        print(line, end='')
                # Flush the last n lines
                print(end='', flush=True)
            # Now, the cursor is at the end of the last lines
            # if tail > 0
            for line in _follow_job_logs(log_file,
                                         job_id=job_id,
                                         start_streaming=start_streaming,
                                         start_streaming_at=start_stream_at):
                print(line, end='', flush=True)
    else:
        try:
            start_streaming = False
            with open(log_path, 'r', encoding='utf-8') as log_file:
                if tail > 0:
                    # If tail > 0, we need to read the last n lines.
                    # We use double ended queue to rotate the last n lines.
                    head_lines_of_log_file = _peek_head_lines(log_file)
                    lines = collections.deque(log_file, maxlen=tail)
                    start_streaming = _should_stream_the_whole_tail_lines(
                        head_lines_of_log_file, lines, start_stream_at)
                else:
                    lines = log_file
                for line in lines:
                    if start_stream_at in line:
                        start_streaming = True
                    if start_streaming:
                        print(line, end='', flush=True)
                status_str = status.value if status is not None else 'None'
                print(ux_utils.finishing_message(
                    f'Job finished (status: {status_str}).'),
                      flush=True)
        except FileNotFoundError:
            print(f'{colorama.Fore.RED}ERROR: Logs for job {job_id} (status:'
                  f' {status.value}) does not exist.{colorama.Style.RESET_ALL}')
