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

import asyncio
import colorama
import json

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
    timeout: Optional[int] = None,
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
        timeout: Optional timeout in seconds. If the command does not complete
            within this time, it will be terminated and TimeoutExpired will be
            raised. None means no timeout (default).

    Returns the returncode or returncode, stdout and stderr of the command.
      Note that the stdout and stderr is already decoded.

    Raises:
        subprocess.TimeoutExpired: If the command times out.
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
            # Use a timer to enforce timeout during stream processing.
            # Without this, process_subprocess_stream blocks until the process
            # finishes, making the timeout at proc.wait() ineffective.
            timeout_triggered = False
            timer = None

            def _timeout_handler():
                nonlocal timeout_triggered
                timeout_triggered = True
                subprocess_utils.kill_children_processes(proc.pid)

            if timeout is not None:
                timer = threading.Timer(timeout, _timeout_handler)
                timer.start()

            try:
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
            finally:
                if timer is not None:
                    timer.cancel()

            # Check if timeout was triggered during stream processing
            if timeout_triggered:
                logger.error(
                    f'Command timed out after {timeout} seconds: {cmd}')
                raise subprocess.TimeoutExpired(cmd, timeout)

            # Ensure returncode is set.
            if ctx is not None or process_stream:
                # Stream processing already waited for process completion, so
                # proc.wait() will return immediately. We still call it to
                # ensure proc.returncode is set.
                proc.wait()
            else:
                # No stream processing - use proc.wait with timeout as primary
                # timeout mechanism.
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # Kill the process and all its children
                    subprocess_utils.kill_children_processes(proc.pid)
                    logger.error(
                        f'Command timed out after {timeout} seconds: {cmd}')
                    raise
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

autostop_lib.set_last_active_time_to_now()
job_lib.set_status(3, job_lib.JobStatus.PENDING)
plural = 's' if 2 > 1 else ''
node_str = f'2 node{plural}'
message = ('[2m├── [0m[2m'
           'Waiting for task resources on '
           f'{node_str}.[0m')
print(message, flush=True)
sky_env_vars_dict = {}
sky_env_vars_dict['SKYPILOT_INTERNAL_JOB_ID'] = 3

sky_env_vars_dict['SKYPILOT_TASK_ID'] = 'sky-2024-11-17-00-00-00-000002-cluster-3'
script = 'echo "Running on node $SKYPILOT_NODE_RANK"'
if script is None:
    script = ''
rclone_flush_script = '\n# Only waits if cached mount is enabled (RCLONE_MOUNT_CACHED_LOG_DIR is not empty)\n# findmnt alone is not enough, as some clouds (e.g. AWS on ARM64) uses\n# rclone for normal mounts as well.\nif [ $(findmnt -t fuse.rclone --noheading | wc -l) -gt 0 ] &&            [ -d ~/.sky/rclone_log ] &&            [ "$(ls -A ~/.sky/rclone_log)" ]; then\n    FLUSH_START_TIME=$(date +%s)\n    flushed=0\n    # extra second on top of --vfs-cache-poll-interval to\n    # avoid race condition between rclone log line creation and this check.\n    sleep 1\n    while [ $flushed -eq 0 ]; do\n        # sleep for the same interval as --vfs-cache-poll-interval\n        sleep 10\n        flushed=1\n        for file in ~/.sky/rclone_log/*; do\n            exitcode=0\n            tac $file | grep "vfs cache: cleaned:" -m 1 | grep "in use 0, to upload 0, uploading 0" -q || exitcode=$?\n            if [ $exitcode -ne 0 ]; then\n                ELAPSED=$(($(date +%s) - FLUSH_START_TIME))\n                # Extract the last vfs cache status line to show what we\'re waiting for\n                CACHE_STATUS=$(tac $file | grep "vfs cache: cleaned:" -m 1 | sed \'s/.*vfs cache: cleaned: //\' 2>/dev/null)\n                # Extract currently uploading files from recent log lines (show up to 2 files)\n                UPLOADING_FILES=$(tac $file | head -30 | grep -E "queuing for upload" | head -2 | sed \'s/.*INFO  : //\' | sed \'s/: vfs cache:.*//\' | tr \'\\n\' \',\' | sed \'s/,$//\' | sed \'s/,/, /g\' 2>/dev/null)\n                # Build status message with available info\n                if [ -n "$CACHE_STATUS" ] && [ -n "$UPLOADING_FILES" ]; then\n                    echo "skypilot: cached mount is still uploading (elapsed: ${ELAPSED}s) [${CACHE_STATUS}] uploading: ${UPLOADING_FILES}"\n                elif [ -n "$CACHE_STATUS" ]; then\n                    echo "skypilot: cached mount is still uploading (elapsed: ${ELAPSED}s) [${CACHE_STATUS}]"\n                else\n                    # Fallback: show last non-empty line from log\n                    LAST_LINE=$(tac $file | grep -v "^$" | head -1 | sed \'s/.*INFO  : //\' | sed \'s/.*ERROR : //\' | sed \'s/.*NOTICE: //\' 2>/dev/null)\n                    if [ -n "$LAST_LINE" ]; then\n                        echo "skypilot: cached mount is still uploading (elapsed: ${ELAPSED}s) ${LAST_LINE}"\n                    else\n                        echo "skypilot: cached mount is still uploading (elapsed: ${ELAPSED}s)"\n                    fi\n                fi\n                flushed=0\n                break\n            fi\n        done\n    done\n    TOTAL_FLUSH_TIME=$(($(date +%s) - FLUSH_START_TIME))\n    echo "skypilot: cached mount upload complete (took ${TOTAL_FLUSH_TIME}s)"\nfi'

if script or False:
    script += rclone_flush_script
    sky_env_vars_dict['SKYPILOT_NUM_GPUS_PER_NODE'] = 0

    async def _monarch_run():
        import site as _site

        # Ensure XDG_RUNTIME_DIR points to a writable directory.
        # Monarch's Rust runtime creates Unix domain sockets there
        # and /run/user/<uid>/ often doesn't exist on Slurm nodes.
        _rd = os.path.join(os.path.expanduser('~'), '.monarch_runtime')
        os.makedirs(_rd, exist_ok=True)
        os.environ['XDG_RUNTIME_DIR'] = _rd

        # Monarch spawns child procs using os.path.realpath(sys.executable)
        # which resolves venv symlinks to the base Python. Set PYTHONPATH
        # so children can find packages from our venv.
        _sp = [p for p in _site.getsitepackages() if 'site-packages' in p]
        if _sp:
            os.environ['PYTHONPATH'] = os.pathsep.join(_sp)

        # Configure Monarch to use TCP transport for proc mesh
        # communication. The default (Unix domain sockets) fails
        # across Slurm nodes. Must be called BEFORE any other
        # Monarch API calls.
        from monarch._rust_bindings.monarch_hyperactor.channel import ChannelTransport
        from monarch._rust_bindings.monarch_hyperactor.config import configure
        configure(default_transport=ChannelTransport.TcpWithHostname)

        from monarch._src.actor.bootstrap import attach_to_workers
        from sky.skylet.executor.monarch_executor import SkyPilotExecutor

        # Read worker addresses from discovery files.
        # Workers are started by instance_setup.start_monarch_workers()
        # during cluster provisioning (after torchmonarch is installed).
        # HOME is always the sky cluster home dir in the job
        # execution context (both container and non-container).
        worker_dir = os.path.join(
            os.path.expanduser('~'),
            'monarch_workers')
        workers = []
        for f in sorted(os.listdir(worker_dir)):
            fpath = os.path.join(worker_dir, f)
            with open(fpath) as fh:
                addr = fh.read().strip()
            # Only accept valid worker addresses (tcp://host:port).
            # Skip stale or unrelated files.
            if addr.startswith('tcp://'):
                workers.append(addr)

        # Connect to Monarch workers
        host_mesh = attach_to_workers(
            name='sky_mesh',
            ca='trust_all_connections',
            workers=workers)
        await host_mesh.initialized

        # Create process mesh and spawn executor actors
        proc_mesh = host_mesh.spawn_procs()
        executors = proc_mesh.spawn('executors', SkyPilotExecutor)

        # Discover IPs for deterministic SKYPILOT_NODE_RANK assignment
        ip_futures = await executors.get_ip.call()
        actor_ips = [ip for _, ip in ip_futures.flatten('hosts')]

        cluster_ips = ['10.0.0.1', '10.0.0.2']

        task_display_name = 'distributed_task'

        def _build_prefix(node_idx, rank, ip, is_setup=False,
                           single_node=False):
            """Build colorama log prefix matching executor/slurm.py conventions."""
            node_name = 'head' if node_idx == 0 else f'worker{node_idx}'
            if is_setup:
                if node_idx == 0:
                    return (f'{colorama.Fore.CYAN}(setup pid={{pid}})'
                            f'{colorama.Style.RESET_ALL} ')
                return (f'{colorama.Fore.CYAN}(setup pid={{pid}}, ip={ip})'
                        f'{colorama.Style.RESET_ALL} ')
            elif single_node:
                return (f'{colorama.Fore.CYAN}({task_display_name}, pid={{pid}})'
                        f'{colorama.Style.RESET_ALL} ')
            else:
                if node_idx == 0:
                    return (f'{colorama.Fore.CYAN}({node_name}, rank={rank}, pid={{pid}})'
                            f'{colorama.Style.RESET_ALL} ')
                return (f'{colorama.Fore.CYAN}({node_name}, rank={rank}, pid={{pid}}, ip={ip})'
                        f'{colorama.Style.RESET_ALL} ')

        # --- Setup phase ---
        if False:
            job_lib.set_status(3, job_lib.JobStatus.SETTING_UP)
            job_lib.scheduler.schedule_step()

            setup_futures = []
            setup_log_paths = []
            for i, ip in enumerate(actor_ips):
                node_idx = cluster_ips.index(ip)
                node_name = 'head' if node_idx == 0 else f'worker{node_idx}'
                # Expand ~ here (controller side) because executor
                # procs may have a different HOME.
                log_path = os.path.expanduser(
                    os.path.join(None, f'setup-{node_name}.log'))
                setup_log_paths.append(log_path)
                prefix = _build_prefix(node_idx, i, ip, is_setup=True)
                setup_env = dict(None)

                fut = executors.slice(hosts=i).run_command.call(
                    None, setup_env, log_path, prefix)
                setup_futures.append(fut)

            # Wait for all setup completions
            for i, fut in enumerate(setup_futures):
                result = await fut
                setup_rc = [rc for _, rc in result.flatten('hosts')][0]
                # Print setup log output
                lp = setup_log_paths[i]
                if os.path.exists(lp):
                    with open(lp) as _f:
                        print(_f.read(), end='', flush=True)
                if setup_rc != 0:
                    msg = (f'ERROR: {colorama.Fore.RED}Job 3\'s '
                           f'setup failed with return code {setup_rc}.'
                           f' See error logs above for more details.'
                           f'{colorama.Style.RESET_ALL}')
                    print(msg, flush=True)
                    job_lib.set_status(3, job_lib.JobStatus.FAILED_SETUP)
                    return [setup_rc]

        print('\x1b[2m└── \x1b[0mJob started. Streaming logs... \x1b[2m(Ctrl-C to exit log streaming; job will not be killed)\x1b[0m', flush=True)

        job_lib.set_job_started(3)
        if not False:
            job_lib.scheduler.schedule_step()

        # --- Task phase ---
        # Only dispatch to num_nodes actors (task may use fewer
        # nodes than the cluster has).
        task_num_nodes = 2
        task_actor_ips = actor_ips[:task_num_nodes]
        node_ips_str = '\n'.join(task_actor_ips)

        task_futures = []
        task_log_paths = []
        for i, ip in enumerate(task_actor_ips):
            node_idx = cluster_ips.index(ip)
            rank = i
            node_name = 'head' if node_idx == 0 else f'worker{node_idx}'

            if task_num_nodes == 1:
                log_filename = 'run.log'
            else:
                log_filename = f'{rank}-{node_name}.log'
            # Expand ~ here (controller side) because executor
            # procs may have a different HOME.
            log_path = os.path.expanduser(
                os.path.join('/sky/logs/tasks', log_filename))
            task_log_paths.append(log_path)

            task_env = dict(sky_env_vars_dict)
            task_env['SKYPILOT_NODE_RANK'] = str(rank)
            task_env['SKYPILOT_NUM_NODES'] = str(task_num_nodes)
            task_env['SKYPILOT_NODE_IPS'] = node_ips_str

            prefix = _build_prefix(node_idx, rank, ip,
                                   single_node=(task_num_nodes == 1))
            fut = executors.slice(hosts=i).run_command.call(
                script, task_env, log_path, prefix)
            task_futures.append(fut)

        # Collect return codes
        task_returncodes = []
        for fut in task_futures:
            result = await fut
            rc = [rc for _, rc in result.flatten('hosts')][0]
            task_returncodes.append(rc)

        # Print log file contents. The Monarch actor writes output
        # to log files (via run_bash_command_with_log), but the
        # actor's stdout is not forwarded to the controller. Read
        # the log files from the shared filesystem and print them.
        for lp in task_log_paths:
            if os.path.exists(lp):
                with open(lp) as _f:
                    print(_f.read(), end='', flush=True)

        return task_returncodes

    returncodes = asyncio.run(_monarch_run())
else:
    returncodes = [0]

if sum(returncodes) != 0:
    # Save exit codes to job metadata for potential recovery logic
    if int(constants.SKYLET_VERSION) >= 28:
        job_lib.set_exit_codes(3, returncodes)
    job_lib.set_status(3, job_lib.JobStatus.FAILED)
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
    print('ERROR: [31mJob 3 failed with '
          'return code list:[0m',
          returncodes,
          reason,
          flush=True)
    # Need this to set the job status in ray job to be FAILED.
    sys.exit(1)
else:
    job_lib.set_status(3, job_lib.JobStatus.SUCCEEDED)
    # Schedule the next pending job immediately to make the job
    # scheduling more efficient.
    job_lib.scheduler.schedule_step()
    # This waits for all streaming logs to finish.
    time.sleep(0.5)
