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

# Set the environment variables to avoid deduplicating logs and
# scheduler events. This should be set in driver code, since we are
# not using `ray job submit` anymore, and the environment variables
# from the ray cluster is not inherited.
os.environ['RAY_DEDUP_LOGS'] = '0'
os.environ['RAY_SCHEDULER_EVENTS'] = '0'

import ray
import ray.util as ray_util

from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.utils import log_utils
from sky.utils import subprocess_utils

SKY_REMOTE_WORKDIR = '~/sky_workdir'

CANCELLED_RETURN_CODE = 137

kwargs = dict()
# Only set the `_temp_dir` to SkyPilot's ray cluster directory when
# the directory exists for backward compatibility for the VM
# launched before #1790.
if os.path.exists('/tmp/ray_skypilot'):
    kwargs['_temp_dir'] = '/tmp/ray_skypilot'
ray.init(
    address='auto',
    namespace='__sky__2__',
    log_to_driver=True,
    **kwargs
)
def get_or_fail(futures, pg) -> List[int]:
    """Wait for tasks, if any fails, cancel all unready."""
    if not futures:
        return [], []
    returncodes = [1] * len(futures)
    pids = [None] * len(futures)
    failed = False
    # Wait for 1 task to be ready.
    ready = []
    # Keep invoking ray.wait if ready is empty. This is because
    # ray.wait with timeout=None will only wait for 10**6 seconds,
    # which will cause tasks running for more than 12 days to return
    # before becoming ready.
    # (Such tasks are common in serving jobs.)
    # Reference: https://github.com/ray-project/ray/blob/ray-2.9.3/python/ray/_private/worker.py#L2845-L2846

    def handle_ready_tasks(tasks: List[ray.ObjectRef]) -> None:
        nonlocal returncodes, pids, failed
        for task in tasks:
            idx = futures.index(task)
            res = ray.get(task)
            returncodes[idx] = res['return_code']
            pids[idx] = res['pid']
            if res['return_code'] != 0:
                failed = True

    while not ready:
        ready, unready = ray.wait(futures)
    handle_ready_tasks(ready)
    while unready:
        if failed:
            for task in unready:
                # ray.cancel without force fails to kill tasks.
                # We use force=True to kill unready tasks.
                ray.cancel(task, force=True)
                # Use SIGKILL=128+9 to indicate the task is forcely
                # killed.
                idx = futures.index(task)
                returncodes[idx] = CANCELLED_RETURN_CODE
            break
        ready, unready = ray.wait(unready)
        handle_ready_tasks(ready)
    # Remove the placement group after all tasks are done, so that
    # the next job can be scheduled on the released resources
    # immediately.
    ray_util.remove_placement_group(pg)
    sys.stdout.flush()
    return returncodes, pids

futures = []

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

run_bash_command_with_log = run_bash_command_with_log
run_bash_command_with_log_and_return_pid =                 ray.remote(run_bash_command_with_log_and_return_pid)
autostop_lib.set_last_active_time_to_now()
job_lib.set_status(2, job_lib.JobStatus.PENDING)
pg = ray_util.placement_group([{"CPU": 4.0, "GPU": 1.0}], 'STRICT_SPREAD')
plural = 's' if 1 > 1 else ''
node_str = f'1 node{plural}'
message = ('[2m├── [0m[2m'
           'Waiting for task resources on '
           f'{node_str}.[0m')
print(message, flush=True)
# FIXME: This will print the error message from autoscaler if
# it is waiting for other task to finish. We should hide the
# error message.
ray.get(pg.ready())
print('\x1b[2m└── \x1b[0mJob started. Streaming logs... \x1b[2m(Ctrl-C to exit log streaming; job will not be killed)\x1b[0m', flush=True)
setup_cmd = 'pip install torch'
_SETUP_CPUS = 0.0001
# The setup command will be run as a ray task with num_cpus=_SETUP_CPUS as the
# requirement; this means Ray will set CUDA_VISIBLE_DEVICES to an empty string.
# We unset it so that user setup command may properly use this env var.
setup_cmd = 'unset CUDA_VISIBLE_DEVICES; ' + setup_cmd
job_lib.set_status(2, job_lib.JobStatus.SETTING_UP)

# The schedule_step should be called after the job status is set to non-PENDING,
# otherwise, the scheduler will think the current job is not submitted yet, and
# skip the scheduling step.
job_lib.scheduler.schedule_step()

# If some nodes are down and then new nodes are added after launching again,
# the result of `ray.nodes()` will include all the nodes, so we need to get
# the alive nodes.
alive_nodes = [n for n in ray.nodes() if 'Alive' in n and n['Alive']]
total_num_nodes = len(alive_nodes)
setup_bundles = [{"CPU": _SETUP_CPUS} for _ in range(total_num_nodes)]
setup_pg = ray.util.placement_group(setup_bundles, strategy='STRICT_SPREAD')
setup_workers = [run_bash_command_with_log_and_return_pid \
    .options(
        name='setup',
        num_cpus=_SETUP_CPUS,
        scheduling_strategy=ray.util.scheduling_strategies.PlacementGroupSchedulingStrategy(
            placement_group=setup_pg,
            placement_group_bundle_index=i)
    ) \
    .remote(
        setup_cmd,
        os.path.expanduser('/sky/logs/setup.log'),
        env_vars={'SKYPILOT_TASK_ID': 'sky-2024-11-17-00-00-00-000001-cluster-2', 'MODEL_NAME': 'resnet50', 'SKYPILOT_NUM_NODES': '1'},
        stream_logs=True,
        with_ray=True,
    ) for i in range(total_num_nodes)]
setup_returncodes, setup_pids = get_or_fail(setup_workers, setup_pg)
success = True
failed_workers_and_returncodes = []
for i in range(len(setup_returncodes)):
    returncode = setup_returncodes[i]
    pid = setup_pids[i]
    if pid == None:
        pid = os.getpid()
    if returncode != 0 and returncode != CANCELLED_RETURN_CODE:
        success = False
        failed_workers_and_returncodes.append((pid, returncode))
if not success:
    msg = f'ERROR: [31mJob 2\'s setup failed. '
    msg += f'Failed workers: ' + ', '.join([f'(pid={pid}, returncode={returncode})' for pid, returncode in failed_workers_and_returncodes])
    msg += f'. See error logs above for more details.[0m'
    print(msg, flush=True)
    job_lib.set_status(2, job_lib.JobStatus.FAILED_SETUP)
    # This waits for all streaming logs to finish.
    time.sleep(1)
    # Need this to set the job status in ray job to be FAILED.
    sys.exit(1)

job_lib.set_job_started(2)
@ray.remote
def check_ip():
    return ray.util.get_node_ip_address()
gang_scheduling_id_to_ip = ray.get([
    check_ip.options(
            num_cpus=4.0,
            scheduling_strategy=ray.util.scheduling_strategies.PlacementGroupSchedulingStrategy(
                placement_group=pg,
                placement_group_bundle_index=i
            )).remote()
    for i in range(pg.bundle_count)
])

cluster_ips_to_node_id = {ip: i for i, ip in enumerate(['10.0.0.1'])}
job_ip_rank_list = sorted(gang_scheduling_id_to_ip, key=cluster_ips_to_node_id.get)
job_ip_rank_map = {ip: i for i, ip in enumerate(job_ip_rank_list)}
job_ip_list_str = '\n'.join(job_ip_rank_list)

sky_env_vars_dict = {}
sky_env_vars_dict['SKYPILOT_NODE_IPS'] = job_ip_list_str
sky_env_vars_dict['SKYPILOT_NUM_NODES'] = len(job_ip_rank_list)

sky_env_vars_dict['SKYPILOT_TASK_ID'] = 'sky-2024-11-17-00-00-00-000001-cluster-2'
sky_env_vars_dict['MODEL_NAME'] = 'resnet50'
script = 'python train.py'
rclone_flush_script = '\n# Only waits if cached mount is enabled (RCLONE_MOUNT_CACHED_LOG_DIR is not empty)\n# findmnt alone is not enough, as some clouds (e.g. AWS on ARM64) uses\n# rclone for normal mounts as well.\nif [ $(findmnt -t fuse.rclone --noheading | wc -l) -gt 0 ] &&            [ -d ~/.sky/rclone_log ] &&            [ "$(ls -A ~/.sky/rclone_log)" ]; then\n    flushed=0\n    # extra second on top of --vfs-cache-poll-interval to\n    # avoid race condition between rclone log line creation and this check.\n    sleep 1\n    while [ $flushed -eq 0 ]; do\n        # sleep for the same interval as --vfs-cache-poll-interval\n        sleep 10\n        flushed=1\n        for file in ~/.sky/rclone_log/*; do\n            exitcode=0\n            tac $file | grep "vfs cache: cleaned:" -m 1 | grep "in use 0, to upload 0, uploading 0" -q || exitcode=$?\n            if [ $exitcode -ne 0 ]; then\n                echo "skypilot: cached mount is still uploading to remote"\n                flushed=0\n                break\n            fi\n        done\n    done\n    echo "skypilot: cached mount uploaded complete"\nfi'

if script is not None:
    script=f'unset RAY_RAYLET_PID; {script}'
    script += rclone_flush_script
    sky_env_vars_dict['SKYPILOT_NUM_GPUS_PER_NODE'] = 1

    ip = gang_scheduling_id_to_ip[0]
    rank = job_ip_rank_map[ip]

    if len(cluster_ips_to_node_id) == 1: # Single-node task on single-node cluter
        name_str = 'train_task,' if 'train_task' != None else 'task,'
        log_path = os.path.expanduser(os.path.join('/sky/logs/tasks', 'run.log'))
    else: # Single-node or multi-node task on multi-node cluster
        idx_in_cluster = cluster_ips_to_node_id[ip]
        if cluster_ips_to_node_id[ip] == 0:
            node_name = 'head'
        else:
            node_name = f'worker{idx_in_cluster}'
        name_str = f'{node_name}, rank={rank},'
        log_path = os.path.expanduser(os.path.join('/sky/logs/tasks', f'{rank}-{node_name}.log'))
    sky_env_vars_dict['SKYPILOT_NODE_RANK'] = rank

    sky_env_vars_dict['SKYPILOT_INTERNAL_JOB_ID'] = 2

    futures.append(run_bash_command_with_log_and_return_pid \
            .options(name=name_str, num_cpus=4.0, resources={"GPU": 1.0}, num_gpus=1.0, scheduling_strategy=ray.util.scheduling_strategies.PlacementGroupSchedulingStrategy(placement_group=pg, placement_group_bundle_index=0)) \
            .remote(
                script,
                log_path,
                env_vars=sky_env_vars_dict,
                stream_logs=True,
                with_ray=True,
            ))
returncodes, _ = get_or_fail(futures, pg)
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
