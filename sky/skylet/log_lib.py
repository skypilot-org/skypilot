"""Sky logging library.

This is a remote utility module that provides logging functionality.
"""
import copy
import io
import multiprocessing.pool
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from typing import Dict, Iterator, List, Optional, Tuple, Union

import colorama

from sky import sky_logging
from sky.skylet import constants
from sky.skylet import job_lib
from sky.utils import log_utils
from sky.utils import subprocess_utils

_SKY_LOG_WAITING_GAP_SECONDS = 1
_SKY_LOG_WAITING_MAX_RETRY = 5
_SKY_LOG_TAILING_GAP_SECONDS = 0.2

logger = sky_logging.init_logger(__name__)


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
    with open(args.log_path, 'a') as fout:
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
    ray_job_id: Optional[str] = None,
    use_sudo: bool = False,
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
        ray_job_id: The id for a ray job.
        use_sudo: Whether to use sudo to create log_path.

    Returns the returncode or returncode, stdout and stderr of the command.
      Note that the stdout and stderr is already decoded.
    """
    assert process_stream or not require_outputs, (
        process_stream, require_outputs,
        'require_outputs should be False when process_stream is False')

    log_path = os.path.expanduser(log_path)
    dirname = os.path.dirname(log_path)
    if use_sudo:
        # Sudo case is encountered when submitting
        # a job for Sky on-prem, when a non-admin user submits a job.
        subprocess.run(f'sudo mkdir -p {dirname}', shell=True, check=True)
        subprocess.run(f'sudo touch {log_path}; sudo chmod a+rwx {log_path}',
                       shell=True,
                       check=True)
        # Hack: Subprocess Popen does not accept sudo.
        # subprocess.Popen in local mode with shell=True does not work,
        # as it does not understand what -H means for sudo.
        shell = False
    else:
        os.makedirs(dirname, exist_ok=True)
    # Redirect stderr to stdout when using ray, to preserve the order of
    # stdout and stderr.
    stdout_arg = stderr_arg = None
    if process_stream:
        stdout_arg = subprocess.PIPE
        stderr_arg = subprocess.PIPE if not with_ray else subprocess.STDOUT
    with subprocess.Popen(cmd,
                          stdout=stdout_arg,
                          stderr=stderr_arg,
                          start_new_session=True,
                          shell=shell,
                          **kwargs) as proc:
        try:
            # The proc can be defunct if the python program is killed. Here we
            # open a new subprocess to gracefully kill the proc, SIGTERM
            # and then SIGKILL the process group.
            # Adapted from ray/dashboard/modules/job/job_manager.py#L154
            parent_pid = os.getpid()
            daemon_script = os.path.join(
                os.path.dirname(os.path.abspath(job_lib.__file__)),
                'subprocess_daemon.py')
            daemon_cmd = [
                'python3',
                daemon_script,
                '--parent-pid',
                str(parent_pid),
                '--proc-pid',
                str(proc.pid),
            ]
            # Bool use_sudo is true in the Sky On-prem case.
            # In this case, subprocess_daemon.py should run on the root user
            # and the Ray job id should be passed for daemon to poll for
            # job status (as `ray job stop` does not work in the
            # multitenant case).
            if use_sudo:
                daemon_cmd.insert(0, 'sudo')
                daemon_cmd.extend(['--local-ray-job-id', str(ray_job_id)])
            subprocess.Popen(
                daemon_cmd,
                start_new_session=True,
                # Suppress output
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Disable input
                stdin=subprocess.DEVNULL,
            )
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
    # PYTHONUNBUFFERED is used to disable python output buffering.
    script = [
        textwrap.dedent(f"""\
            #!/bin/bash
            source ~/.bashrc
            set -a
            . $(conda info --base 2> /dev/null)/etc/profile.d/conda.sh > /dev/null 2>&1 || true
            set +a
            export PYTHONUNBUFFERED=1
            cd {constants.SKY_REMOTE_WORKDIR}"""),
    ]
    if env_vars is not None:
        for k, v in env_vars.items():
            script.append(f'export {k}="{v}"')
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
                              job_owner: Optional[str] = None,
                              job_id: Optional[int] = None,
                              env_vars: Optional[Dict[str, str]] = None,
                              stream_logs: bool = False,
                              with_ray: bool = False,
                              use_sudo: bool = False):
    with tempfile.NamedTemporaryFile('w', prefix='sky_app_',
                                     delete=False) as fp:
        if use_sudo:
            env_vars = add_ray_env_vars(env_vars)
        bash_command = make_task_bash_script(bash_command, env_vars=env_vars)
        fp.write(bash_command)
        fp.flush()
        script_path = fp.name

        # Need this `-i` option to make sure `source ~/.bashrc` work.
        inner_command = f'/bin/bash -i {script_path}'

        subprocess_cmd: Union[str, List[str]]
        if use_sudo and job_owner is not None:
            subprocess.run(f'chmod a+rwx {script_path}', shell=True, check=True)
            subprocess_cmd = job_lib.make_job_command_with_user_switching(
                job_owner, inner_command)
        else:
            subprocess_cmd = inner_command

        ray_job_id = job_lib.make_ray_job_id(job_id,
                                             job_owner) if job_id else None
        return run_with_log(
            subprocess_cmd,
            log_path,
            ray_job_id=ray_job_id,
            stream_logs=stream_logs,
            with_ray=with_ray,
            use_sudo=use_sudo,
            # Disable input to avoid blocking.
            stdin=subprocess.DEVNULL,
            shell=True)


def _follow_job_logs(file,
                     job_id: int,
                     start_streaming_at: str = '') -> Iterator[str]:
    """Yield each line from a file as they are written.

    `sleep_sec` is the time to sleep after empty reads. """
    line = ''
    # No need to lock the status here, as the while loop can handle
    # the older status.
    status = job_lib.get_status_no_lock(job_id)
    start_streaming = False
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
                    time.sleep(1 + _SKY_LOG_TAILING_GAP_SECONDS)
                    wait_last_logs = False
                    continue
                status_str = status.value if status is not None else 'None'
                print(f'INFO: Job finished (status: {status_str}).')
                return

            time.sleep(_SKY_LOG_TAILING_GAP_SECONDS)
            status = job_lib.get_status_no_lock(job_id)


def tail_logs(job_owner: str,
              job_id: Optional[int],
              log_dir: Optional[str],
              spot_job_id: Optional[int] = None,
              follow: bool = True) -> None:
    """Tail the logs of a job.

    Args:
        job_owner: The owner username of the job.
        job_id: The job id.
        log_dir: The log directory of the job.
        spot_job_id: The spot job id (for logging info only to avoid confusion).
        follow: Whether to follow the logs or print the logs so far and exit.
    """
    if job_id is None:
        # This only happens when job_lib.get_latest_job_id() returns None,
        # which means no job has been submitted to this cluster. See
        # sky.skylet.job_lib.JobLibCodeGen.tail_logs for more details.
        logger.info('Skip streaming logs as no job has been submitted.')
        return
    job_str = f'job {job_id}'
    if spot_job_id is not None:
        job_str = f'spot job {spot_job_id}'
    if log_dir is None:
        print(f'{job_str.capitalize()} not found (see `sky queue`).',
              file=sys.stderr)
        return
    logger.debug(f'Tailing logs for job, real job_id {job_id}, spot_job_id '
                 f'{spot_job_id}.')
    logger.info(f'{colorama.Fore.YELLOW}Start streaming logs for {job_str}.'
                f'{colorama.Style.RESET_ALL}')
    log_path = os.path.join(log_dir, 'run.log')
    log_path = os.path.expanduser(log_path)

    status = job_lib.update_job_status(job_owner, [job_id], silent=True)[0]

    # Wait for the log to be written. This is needed due to the `ray submit`
    # will take some time to start the job and write the log.
    retry_cnt = 0
    while status is not None and not status.is_terminal():
        retry_cnt += 1
        if os.path.exists(log_path) and status != job_lib.JobStatus.INIT:
            break
        if retry_cnt >= _SKY_LOG_WAITING_MAX_RETRY:
            print(
                f'{colorama.Fore.RED}ERROR: Logs for '
                f'{job_str} (status: {status.value}) does not exist '
                f'after retrying {retry_cnt} times.{colorama.Style.RESET_ALL}')
            return
        print(f'INFO: Waiting {_SKY_LOG_WAITING_GAP_SECONDS}s for the logs '
              'to be written...')
        time.sleep(_SKY_LOG_WAITING_GAP_SECONDS)
        status = job_lib.update_job_status(job_owner, [job_id], silent=True)[0]

    start_stream_at = 'INFO: Tip: use Ctrl-C to exit log'
    if follow and status in [
            job_lib.JobStatus.SETTING_UP,
            job_lib.JobStatus.PENDING,
            job_lib.JobStatus.RUNNING,
    ]:
        # Not using `ray job logs` because it will put progress bar in
        # multiple lines.
        with open(log_path, 'r', newline='') as log_file:
            # Using `_follow` instead of `tail -f` to streaming the whole
            # log and creating a new process for tail.
            for line in _follow_job_logs(log_file,
                                         job_id=job_id,
                                         start_streaming_at=start_stream_at):
                print(line, end='', flush=True)
    else:
        try:
            start_stream = False
            with open(log_path, 'r') as f:
                for line in f.readlines():
                    if start_stream_at in line:
                        start_stream = True
                    if start_stream:
                        print(line, end='', flush=True)
        except FileNotFoundError:
            print(f'{colorama.Fore.RED}ERROR: Logs for job {job_id} (status:'
                  f' {status.value}) does not exist.{colorama.Style.RESET_ALL}')
