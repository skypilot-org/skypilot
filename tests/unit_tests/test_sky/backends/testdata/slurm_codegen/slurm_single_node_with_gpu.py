import functools
import getpass
import hashlib
import io
import os
import pathlib
import selectors
import shlex
import subprocess
import sys
import tempfile
import textwrap
import time
from typing import Dict, List, Optional, Tuple, Union

import colorama
import copy
import json
import multiprocessing
import signal
import threading
from sky.backends import backend_utils

from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.utils import log_utils
from sky.utils import subprocess_utils

SKY_REMOTE_WORKDIR = '~/sky_workdir'

CANCELLED_RETURN_CODE = 137

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
) -> Union[int, Tuple[int, str, str], Tuple[int, int]]:
    """Runs a command and logs its output to a file.

    Args:
        cmd: The command to run.
        log_path: The path to the log file.
        stream_logs: Whether to stream the logs to stdout/stderr.
        require_outputs: Whether to return the stdout/stderr of the command.
        process_stream: Whether to post-process the stdout/stderr of the
            command, such as replacing or skipping lines on the fly. If
            enabled, lines are printed only when '\r' or '\n' is found.
        streaming_prefix: Optional prefix for each log line. Can contain {pid}
            placeholder which will be replaced with the subprocess PID.

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
            if ctx is not None:
                # When runs in coroutine, use kill_pg if available to avoid
                # the overhead of refreshing the process tree in the daemon.
                subprocess_utils.kill_process_daemon(proc.pid, use_kill_pg=True)
            else:
                # For backward compatibility, do not specify use_kill_pg by
                # default.
                subprocess_utils.kill_process_daemon(proc.pid)

            # Format streaming_prefix with subprocess PID if it contains {pid}
            formatted_streaming_prefix = streaming_prefix
            if streaming_prefix and '{pid}' in streaming_prefix:
                formatted_streaming_prefix = streaming_prefix.format(
                    pid=proc.pid)

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
                    streaming_prefix=formatted_streaming_prefix,
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
                              with_ray: bool = False,
                              streaming_prefix: Optional[str] = None):
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
                            streaming_prefix=streaming_prefix,
                            shell=True)

def run_bash_command_with_log_and_return_pid(
        bash_command: str,
        log_path: str,
        env_vars: Optional[Dict[str, str]] = None,
        stream_logs: bool = False,
        with_ray: bool = False,
        streaming_prefix: Optional[str] = None):
    return_code = run_bash_command_with_log(bash_command,
                                            log_path,
                                            env_vars,
                                            stream_logs,
                                            with_ray,
                                            streaming_prefix=streaming_prefix)
    return {'return_code': return_code, 'pid': os.getpid()}

def _cancel_slurm_job_steps():
    slurm_job_id = '12345'
    assert slurm_job_id is not None, 'SLURM_JOB_ID is not set'
    try:
        # Query steps for this job: squeue -s -j JOBID -h -o "%i %j"
        # Output format: "JOBID.STEPID STEPNAME"
        # TODO(kevin): This assumes that compute node is able
        # to run client commands against the controller.
        # Validate this assumption.
        result = subprocess.run(
            ['squeue', '-s', '-j', slurm_job_id, '-h', '-o', '%i %j'],
            capture_output=True, text=True, check=False)
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split()
            assert len(parts) >= 2, 'Expected at least 2 parts'
            step_id, step_name = parts[0], parts[1]
            if step_name == f'sky-2':
                subprocess.run(['scancel', step_id],
                                check=False, capture_output=True)
    except Exception as e:
        print(f'Error in _cancel_slurm_job_steps: {e}', flush=True)
        pass

def _slurm_cleanup_handler(signum, _frame):
    _cancel_slurm_job_steps()
    # Re-raise to let default handler terminate.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)

signal.signal(signal.SIGTERM, _slurm_cleanup_handler)

autostop_lib.set_last_active_time_to_now()
job_lib.set_status(2, job_lib.JobStatus.PENDING)
plural = 's' if 1 > 1 else ''
node_str = f'1 node{plural}'
message = ('[2m├── [0m[2m'
           'Waiting for task resources on '
           f'{node_str}.[0m')
print(message, flush=True)
sky_env_vars_dict = {}
sky_env_vars_dict['SKYPILOT_INTERNAL_JOB_ID'] = 2

sky_env_vars_dict['SKYPILOT_TASK_ID'] = 'sky-2024-11-17-00-00-00-000001-cluster-2'
sky_env_vars_dict['MODEL_NAME'] = 'resnet50'
script = 'python train.py'
if script is None:
    script = ''
rclone_flush_script = '\n# Only waits if cached mount is enabled (RCLONE_MOUNT_CACHED_LOG_DIR is not empty)\n# findmnt alone is not enough, as some clouds (e.g. AWS on ARM64) uses\n# rclone for normal mounts as well.\nif [ $(findmnt -t fuse.rclone --noheading | wc -l) -gt 0 ] &&            [ -d ~/.sky/rclone_log ] &&            [ "$(ls -A ~/.sky/rclone_log)" ]; then\n    flushed=0\n    # extra second on top of --vfs-cache-poll-interval to\n    # avoid race condition between rclone log line creation and this check.\n    sleep 1\n    while [ $flushed -eq 0 ]; do\n        # sleep for the same interval as --vfs-cache-poll-interval\n        sleep 10\n        flushed=1\n        for file in ~/.sky/rclone_log/*; do\n            exitcode=0\n            tac $file | grep "vfs cache: cleaned:" -m 1 | grep "in use 0, to upload 0, uploading 0" -q || exitcode=$?\n            if [ $exitcode -ne 0 ]; then\n                echo "skypilot: cached mount is still uploading to remote"\n                flushed=0\n                break\n            fi\n        done\n    done\n    echo "skypilot: cached mount uploaded complete"\nfi'

if script or True:
    script += rclone_flush_script
    sky_env_vars_dict['SKYPILOT_NUM_GPUS_PER_NODE'] = 1

    # Signal files for setup/run synchronization:
    # 1. alloc_signal_file: srun has acquired allocation
    # 2. setup_done_signal_file: Driver has finished setup, run can proceed
    #
    # Signal files are stored in home directory, which is
    # assumed to be on a shared NFS mount accessible by all nodes.
    # To support clusters with non-NFS home directories, we would
    # need to let users specify an NFS-backed "working directory"
    # or use a different coordination mechanism.
    alloc_signal_file = f'~/.sky_alloc_12345_2'
    alloc_signal_file = os.path.expanduser(alloc_signal_file)
    setup_done_signal_file = f'~/.sky_setup_done_12345_2'
    setup_done_signal_file = os.path.expanduser(setup_done_signal_file)

    # Start exclusive srun in a thread to reserve allocation (similar to ray.get(pg.ready()))
    gpu_arg = f'--gpus-per-node=1' if 1 > 0 else ''

    def build_task_runner_cmd(user_script, extra_flags, log_dir, env_vars_dict,
                              task_name=None, is_setup=False,
                              alloc_signal=None, setup_done_signal=None):
        env_vars_json = json.dumps(env_vars_dict)

        log_dir = shlex.quote(log_dir)
        env_vars = shlex.quote(env_vars_json)
        cluster_ips = shlex.quote(",".join(['10.0.0.1']))

        runner_args = f'--log-dir={log_dir} --env-vars={env_vars} --cluster-num-nodes=1 --cluster-ips={cluster_ips}'

        if task_name is not None:
            runner_args += f' --task-name={shlex.quote(task_name)}'

        if is_setup:
            runner_args += ' --is-setup'

        if alloc_signal is not None:
            runner_args += f' --alloc-signal-file={shlex.quote(alloc_signal)}'

        if setup_done_signal is not None:
            runner_args += f' --setup-done-signal-file={shlex.quote(setup_done_signal)}'

        script_path = None
        prefix = 'sky_setup_' if is_setup else 'sky_task_'
        if backend_utils.is_command_length_over_limit(user_script):
            with tempfile.NamedTemporaryFile('w', prefix=prefix, suffix='.sh', delete=False) as f:
                f.write(user_script)
                script_path = f.name
            runner_args += f' --script-path={shlex.quote(script_path)}'
        else:
            runner_args += f' --script={shlex.quote(user_script)}'

        # Use /usr/bin/env explicitly to work around a Slurm quirk where
        # srun's execvp() doesn't check execute permissions, failing when
        # $HOME/.local/bin/env (non-executable, from uv installation)
        # shadows /usr/bin/env.
        job_suffix = '-setup' if is_setup else ''
        srun_cmd = (
            f'srun --export=ALL --quiet --unbuffered --kill-on-bad-exit --jobid=12345 '
            f'--job-name=sky-2{job_suffix} --ntasks-per-node=1 {extra_flags} '
            f'{constants.SKY_SLURM_PYTHON_CMD} -m sky.skylet.executor.slurm {runner_args}'
        )
        return srun_cmd, script_path

    def run_thread_func():
        # This blocks until Slurm allocates resources (--exclusive)
        # --mem=0 to match RayCodeGen's behavior where we don't explicitly request memory.
        run_flags = f'--nodes=1 --cpus-per-task=4 --mem=0 {gpu_arg} --exclusive'
        srun_cmd, task_script_path = build_task_runner_cmd(
            script, run_flags, '/sky/logs/tasks', sky_env_vars_dict,
            task_name='train_task',
            alloc_signal=alloc_signal_file,
            setup_done_signal=setup_done_signal_file
        )

        proc = subprocess.Popen(srun_cmd, shell=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              text=True)
        for line in proc.stdout:
            print(line, end='', flush=True)
        proc.wait()

        if task_script_path is not None:
            os.remove(task_script_path)
        return {'return_code': proc.returncode, 'pid': proc.pid}

    run_thread_result = {'result': None}
    def run_thread_wrapper():
        run_thread_result['result'] = run_thread_func()

    run_thread = threading.Thread(target=run_thread_wrapper)
    run_thread.start()

    # Wait for allocation signal from inside srun
    while not os.path.exists(alloc_signal_file):
        if not run_thread.is_alive():
            # srun failed before creating the signal file.
            run_thread.join()
            result = run_thread_result['result']
            returncode = int(result.get('return_code', 1))
            pid = result.get('pid', os.getpid())
            msg = f'ERROR: [31mJob 2\'s setup failed with return code {returncode} (pid={pid}).'
            msg += f' See error logs above for more details.[0m'
            print(msg, flush=True)
            returncodes = [returncode]
            job_lib.set_status(2, job_lib.JobStatus.FAILED_SETUP)
            sys.exit(1)
        time.sleep(0.1)

    print('\x1b[2m└── \x1b[0mJob started. Streaming logs... \x1b[2m(Ctrl-C to exit log streaming; job will not be killed)\x1b[0m', flush=True)

    if True:
        job_lib.set_status(2, job_lib.JobStatus.SETTING_UP)

        # The schedule_step should be called after the job status is set to
        # non-PENDING, otherwise, the scheduler will think the current job
        # is not submitted yet, and skip the scheduling step.
        job_lib.scheduler.schedule_step()

        # --overlap as we have already secured allocation with the srun for the run section,
        # and otherwise this srun would get blocked and deadlock.
        setup_flags = f'--overlap --nodes=1'
        setup_srun, setup_script_path = build_task_runner_cmd(
            'pip install torch', setup_flags, '/sky/logs', {'SKYPILOT_TASK_ID': 'sky-2024-11-17-00-00-00-000001-cluster-2', 'MODEL_NAME': 'resnet50', 'SKYPILOT_NUM_NODES': '1'},
            is_setup=True
        )

        # Run setup srun directly, streaming output to driver stdout
        setup_proc = subprocess.Popen(setup_srun, shell=True,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     text=True)
        for line in setup_proc.stdout:
            print(line, end='', flush=True)
        setup_proc.wait()

        if setup_script_path is not None:
            os.remove(setup_script_path)

        setup_returncode = setup_proc.returncode
        if setup_returncode != 0:
            setup_pid = setup_proc.pid
            msg = f'ERROR: [31mJob 2\'s setup failed with return code {setup_returncode} (pid={setup_pid}).'
            msg += f' See error logs above for more details.[0m'
            print(msg, flush=True)
            job_lib.set_status(2, job_lib.JobStatus.FAILED_SETUP)
            # Cancel the srun spawned by run_thread_func.
            _cancel_slurm_job_steps()
            sys.exit(1)

    job_lib.set_job_started(2)
    if not True:
        # Need to call schedule_step() to make sure the scheduler
        # schedule the next pending job.
        job_lib.scheduler.schedule_step()

    # Signal run thread to proceed.
    pathlib.Path(setup_done_signal_file).touch()

    # Wait for run thread to complete.
    run_thread.join()
    result = run_thread_result['result']

    # Cleanup signal files
    if os.path.exists(alloc_signal_file):
        os.remove(alloc_signal_file)
    if os.path.exists(setup_done_signal_file):
        os.remove(setup_done_signal_file)

    returncodes = [int(result.get('return_code', 1))]
else:
    returncodes = [0]

if sum(returncodes) != 0:
    job_lib.set_status(2, job_lib.JobStatus.FAILED)
    # Schedule the next pending job immediately to make the job
    # scheduling more efficient.
    job_lib.scheduler.schedule_step()
    # This waits for all streaming logs to finish.
    time.sleep(0.5)
    reason = ''
    # 139 is the return code of SIGSEGV, i.e. Segmentation Fault.
    if any(r == 139 for r in returncodes):
        reason = '(likely due to Segmentation Fault)'
    if any(r == 137 for r in returncodes):
        # Find the first non-137 return code
        non_137 = next(r for r in returncodes if r != 137)
        reason = f'(A Worker failed with return code {non_137}, SkyPilot cleaned up the processes on other nodes with return code 137)'
    print('ERROR: [31mJob 2 failed with '
          'return code list:[0m',
          returncodes,
          reason,
          flush=True)
    # Need this to set the job status in ray job to be FAILED.
    sys.exit(1)
else:
    job_lib.set_status(2, job_lib.JobStatus.SUCCEEDED)
    # Schedule the next pending job immediately to make the job
    # scheduling more efficient.
    job_lib.scheduler.schedule_step()
    # This waits for all streaming logs to finish.
    time.sleep(0.5)
