"""Sky logging library.

This is a remote utility module that provides logging functionality.
"""
import collections
import copy
import functools
import io
import multiprocessing.pool
import os
import re
import select
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
from sky.utils import context
from sky.utils import context_utils
from sky.utils import log_utils
from sky.utils import rich_utils
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


def _get_context():
    # TODO(aylei): remove this after we drop the backward-compatibility for
    # 0.9.x in 0.12.0
    # Keep backward-compatibility for the old version of SkyPilot runtimes.
    if 'context' in globals():
        return context.get()
    else:
        return None


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
                ctx = _get_context()
                if ctx is not None and ctx.is_canceled():
                    return
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


def process_subprocess_stream(proc, stdout_stream_handler,
                              stderr_stream_handler) -> Tuple[str, str]:
    """Process the stream of a process in threads, blocking."""
    if proc.stderr is not None:
        # Asyncio does not work as the output processing can be executed in a
        # different thread.
        # selectors is possible to handle the multiplexing of stdout/stderr,
        # but it introduces buffering making the output not streaming.
        with multiprocessing.pool.ThreadPool(processes=1) as pool:
            stderr_fut = pool.apply_async(stderr_stream_handler,
                                          args=(proc.stderr, sys.stderr))
            # Do not launch a thread for stdout as the rich.status does not
            # work in a thread, which is used in
            # log_utils.RayUpLineProcessor.
            stdout = stdout_stream_handler(proc.stdout, sys.stdout)
            stderr = stderr_fut.get()
    else:
        stdout = stdout_stream_handler(proc.stdout, sys.stdout)
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
    ctx = _get_context()
    if process_stream or ctx is not None:
        # Capture stdout/stderr of the subprocess if:
        # 1. Post-processing is needed (process_stream=True)
        # 2. Potential contextual handling is needed (ctx is not None)
        # TODO(aylei): can we always capture the stdout/stderr?
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
            stdout_stream_handler = None
            stderr_stream_handler = None

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
                stdout_stream_handler = functools.partial(
                    _handle_io_stream,
                    args=args,
                )
                if proc.stderr is not None:
                    err_args = copy.copy(args)
                    err_args.line_processor = None
                    stderr_stream_handler = functools.partial(
                        _handle_io_stream,
                        args=err_args,
                    )
            if ctx is not None:
                # When runs in a coroutine, always process the subprocess
                # stream to:
                # 1. handle context cancellation
                # 2. redirect subprocess stdout/stderr to the contextual
                #    stdout/stderr of current coroutine.
                stdout, stderr = context_utils.pipe_and_wait_process(
                    ctx,
                    proc,
                    cancel_callback=subprocess_utils.kill_children_processes,
                    stdout_stream_handler=stdout_stream_handler,
                    stderr_stream_handler=stderr_stream_handler)
            elif process_stream:
                # When runs in a process, only process subprocess stream if
                # necessary to avoid unnecessary stream handling overhead.
                stdout, stderr = process_subprocess_stream(
                    proc, stdout_stream_handler, stderr_stream_handler)
            # Ensure returncode is set.
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


def run_bash_command_with_log_and_return_pid(
        bash_command: str,
        log_path: str,
        env_vars: Optional[Dict[str, str]] = None,
        stream_logs: bool = False,
        with_ray: bool = False):
    return_code = run_bash_command_with_log(bash_command, log_path, env_vars,
                                            stream_logs, with_ray)
    return {'return_code': return_code, 'pid': os.getpid()}


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
                finish = ux_utils.finishing_message(
                    f'Job finished (status: {status_str}).')
                yield finish + '\n'
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


def _setup_watcher(log_file: TextIO, job_id: int, start_streaming: bool,
                   start_streaming_at: str, cluster_name: Optional[str],
                   num_nodes: int) -> bool:
    """Watch the setup logs and print logs with a spinner during setup."""

    # The logic of this function is complicated by the fact that during
    # detached setup the setup logs and the runtime logs are interleaved. That
    # means we need to parse the logs until we see the first setup log, and then
    # parse using a spinner until setup is complete.

    # Setup line format: (setup pid=<pid>) <message> or
    # (setup pid=<pid>, ip=<ip>) <message>
    # Use regex to parse the line.
    setup_line_pattern_one = r'\(setup pid=(\d+)\) (.*)'
    setup_line_pattern_two = r'\(setup pid=(\d+), ip=(\S+)\) (.*)'

    def parse_setup_line(line: str) -> Tuple[str, str]:
        # Attempt to match the two patterns. Return all None if failed, but
        # don't error out in case it's just a version change.
        # Remove all ANSI escape codes.
        line = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', line)
        match = re.match(setup_line_pattern_two, line)
        if match:
            pid = match.group(1)
            ip = match.group(2)
            message = match.group(3)
            return pid, ip, message
        match = re.match(setup_line_pattern_one, line)
        if match:
            pid = match.group(1)
            message = match.group(2)
            return pid, None, message
        logger.debug(f'Failed to parse setup line: {line}')
        return None, None, None

    def is_setup_line(line: str) -> bool:
        return '(setup' in line

    def is_setup_complete(line: str) -> bool:
        return 'Setup complete' == line

    iterator = _follow_job_logs(log_file,
                                job_id=job_id,
                                start_streaming=start_streaming,
                                start_streaming_at=start_streaming_at)

    def get_next_non_blocking() -> Optional[str]:
        fd = log_file.fileno()
        ready, _, _ = select.select([fd], [], [], 1.0)
        if ready:
            res = log_file.readline()
            if res == '':
                time.sleep(1)
                return None
            return res
        else:
            return None

    # Step 1: parse until we see the first setup log.
    # Get output from follow_job_logs
    line = next(iterator)
    while not is_setup_line(line):
        print(line, end='', flush=True)
        try:
            line = next(iterator)
        except StopIteration:
            return

    # Step 2: parse using a spinner until setup is complete.
    setup_timeout = 3
    pids = set()
    completed_pids = set()

    def update_pids(line: str) -> None:
        pid, _, message = parse_setup_line(line)
        if pid is None or message is None:
            return
        pids.add(pid)
        if is_setup_complete(message):
            completed_pids.add(pid)

    def resolve_str() -> str:
        spinner_str = (f'Job setup in progress: ({len(completed_pids)}/'
                       f'{num_nodes} workers setup)')
        uncompleted_pids = pids - completed_pids
        # Sort so the spinner doesn't interchange the order of the pids.
        uncompleted_pids = sorted(list(uncompleted_pids))
        for i in range(len(uncompleted_pids)):
            pid = uncompleted_pids[i]
            indent_symbol = (ux_utils.INDENT_SYMBOL
                             if i != len(uncompleted_pids) - 1 else
                             ux_utils.INDENT_LAST_SYMBOL)
            if cluster_name is not None:
                spinner_str += (
                    f'\n{colorama.Style.RESET_ALL}'
                    f'{colorama.Style.DIM}'
                    f'{indent_symbol} {colorama.Style.DIM} Worker: {pid} '
                    'may have stalled, '
                    f'check logs: `sky logs {cluster_name} {job_id} '
                    f'--no-follow | grep pid={pid}`')
            else:
                spinner_str += (f'\n{indent_symbol} Worker: {pid} may have '
                                'stalled')
        return spinner_str

    setup_start_time = time.time()
    with rich_utils.safe_status(
            ux_utils.spinner_message(
                f'{colorama.Style.RESET_ALL}{colorama.Style.DIM}Job setup in '
                'progress')):
        while True:
            if line:
                pid, _, message = parse_setup_line(line)
                if pid and message:
                    update_pids(line)
                print(line, end='', flush=True)
                line = None
                if len(completed_pids) == num_nodes:
                    break
            if time.time() - setup_start_time > setup_timeout:
                rich_utils.force_update_status(
                    ux_utils.spinner_message(f'{colorama.Style.RESET_ALL}'
                                             f'{colorama.Style.DIM}'
                                             f'{resolve_str()}'))
            line = get_next_non_blocking()

    # Step 3: print the running logs.
    while True:
        try:
            line = next(iterator)
            print(line, end='', flush=True)
        except StopIteration:
            return


def tail_logs(job_id: Optional[int],
              log_dir: Optional[str],
              managed_job_id: Optional[int] = None,
              follow: bool = True,
              tail: int = 0,
              setup_spinner: bool = False,
              cluster_name: Optional[str] = None,
              num_nodes: int = 0) -> None:
    """Tail the logs of a job.

    Args:
        job_id: The job id.
        log_dir: The log directory of the job.
        managed_job_id: The managed job id (for logging info only to avoid
            confusion).
        follow: Whether to follow the logs or print the logs so far and exit.
        tail: The number of lines to display from the end of the log file,
            if 0, print all lines.
        setup_spinner: Whether to show a spinner during setup.
        cluster_name: The name of the cluster.
        num_nodes: The number of nodes in the cluster.
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
            if setup_spinner:
                _setup_watcher(log_file, job_id, start_streaming,
                               start_stream_at, cluster_name, num_nodes)
            else:
                for line in _follow_job_logs(
                        log_file,
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
                # Only show "Job finished" for actually terminal states
                if status is not None and status.is_terminal():
                    print(ux_utils.finishing_message(
                        f'Job finished (status: {status_str}).'),
                          flush=True)
        except FileNotFoundError:
            print(f'{colorama.Fore.RED}ERROR: Logs for job {job_id} (status:'
                  f' {status.value}) does not exist.{colorama.Style.RESET_ALL}')
