"""Code generator for task execution on Ray and Slurm backends."""

import inspect
import math
import textwrap
from typing import Dict, List, Optional, Tuple

import colorama

from sky.skylet import constants
from sky.skylet import log_lib
from sky.utils import accelerator_registry
from sky.utils import ux_utils


class TaskCodeGen:
    """Base code generator for task execution on Ray and Slurm.

    This class provides common functionality for code generation that both
    RayCodeGen and SlurmCodeGen can use.
    """

    def __init__(self) -> None:
        # Code generated so far, to be joined via '\n'.
        self._code: List[str] = []
        # Guard method calling order.
        self._has_prologue: bool = False
        self._has_epilogue: bool = False
        self._has_setup: bool = False
        # Job ID is used to identify the job (also this generated code).
        self.job_id: Optional[int] = None

    def _add_common_imports(self) -> None:
        """Add common imports for both Ray and Slurm execution."""
        self._code.append(
            textwrap.dedent("""\
            import functools
            import getpass
            import hashlib
            import io
            import copy
            import colorama
            import multiprocessing
            import os
            import pathlib
            import selectors
            import shlex
            import subprocess
            import sys
            import tempfile
            import textwrap
            import threading
            import time
            from typing import Dict, List, Optional, Tuple, Union
            """))

    def _add_skylet_imports(self) -> None:
        """Add SkyPilot skylet imports."""
        self._code.append(
            textwrap.dedent("""\
            from sky.skylet import autostop_lib
            from sky.skylet import constants
            from sky.skylet import job_lib
            from sky.utils import log_utils
            from sky.utils import subprocess_utils
            """))

    def _add_logging_functions(self) -> None:
        """Add log streaming functions from log_lib."""
        self._code += [
            # FIXME: This is a hack to make sure that the functions can be found
            # by ray.remote. This should be removed once we have a better way to
            # specify dependencies for ray.
            inspect.getsource(log_lib._ProcessingArgs),  # pylint: disable=protected-access
            inspect.getsource(log_lib._get_context),  # pylint: disable=protected-access
            inspect.getsource(log_lib._handle_io_stream),  # pylint: disable=protected-access
            inspect.getsource(log_lib.process_subprocess_stream),
            inspect.getsource(log_lib.run_with_log),
            inspect.getsource(log_lib.make_task_bash_script),
            inspect.getsource(log_lib.add_ray_env_vars),
            inspect.getsource(log_lib.run_bash_command_with_log),
            inspect.getsource(log_lib.run_bash_command_with_log_and_return_pid),
        ]

    def _add_waiting_for_resources_msg(self, num_nodes: int) -> None:
        self._code.append(
            textwrap.dedent(f"""\
            plural = 's' if {num_nodes} > 1 else ''
            node_str = f'{num_nodes} node{{plural}}'
            message = ('{ux_utils.INDENT_SYMBOL}{colorama.Style.DIM}'
                       'Waiting for task resources on '
                       f'{{node_str}}.{colorama.Style.RESET_ALL}')
            print(message, flush=True)"""))

    def _get_job_started_msg(self) -> str:
        """Returns the 'Job started' streaming message with ANSI formatting."""
        return (
            f'{ux_utils.INDENT_LAST_SYMBOL}Job started. Streaming logs... '
            f'{colorama.Style.DIM}(Ctrl-C to exit log streaming; job will not '
            f'be killed){colorama.Style.RESET_ALL}')

    def _add_job_started_msg(self) -> None:
        streaming_message = self._get_job_started_msg()
        self._code.append(f'print({streaming_message!r}, flush=True)')

    def _get_accelerator_details(
        self,
        resources_dict: Dict[str, float],
    ) -> Tuple[Optional[str], float]:
        resources_copy = resources_dict.copy()
        resources_copy.pop('CPU', None)

        if not resources_copy:
            return None, 0.0

        assert len(resources_copy) == 1, (
            'There can only be one type of accelerator per instance. '
            f'Found: {resources_copy}.')

        acc_name, acc_count = list(resources_copy.items())[0]
        return acc_name, float(acc_count)

    def _add_constants(self) -> None:
        self._code.append(
            textwrap.dedent(f"""\
            SKY_REMOTE_WORKDIR = {constants.SKY_REMOTE_WORKDIR!r}

            CANCELLED_RETURN_CODE = 137
            """))

    def _get_rclone_flush_script(self) -> str:
        """Generate rclone flush script for cached storage mounts.

        This script blocks job completion until all storage mounted with
        CACHED_MOUNT mode is uploaded to remote.

        Returns:
            Bash script as string
        """
        return textwrap.dedent(f"""\

        # Only waits if cached mount is enabled (RCLONE_MOUNT_CACHED_LOG_DIR is not empty)
        # findmnt alone is not enough, as some clouds (e.g. AWS on ARM64) uses
        # rclone for normal mounts as well.
        if [ $(findmnt -t fuse.rclone --noheading | wc -l) -gt 0 ] && \
           [ -d {constants.RCLONE_MOUNT_CACHED_LOG_DIR} ] && \
           [ "$(ls -A {constants.RCLONE_MOUNT_CACHED_LOG_DIR})" ]; then
            flushed=0
            # extra second on top of --vfs-cache-poll-interval to
            # avoid race condition between rclone log line creation and this check.
            sleep 1
            while [ $flushed -eq 0 ]; do
                # sleep for the same interval as --vfs-cache-poll-interval
                sleep {constants.RCLONE_CACHE_REFRESH_INTERVAL}
                flushed=1
                for file in {constants.RCLONE_MOUNT_CACHED_LOG_DIR}/*; do
                    exitcode=0
                    tac $file | grep "vfs cache: cleaned:" -m 1 | grep "in use 0, to upload 0, uploading 0" -q || exitcode=$?
                    if [ $exitcode -ne 0 ]; then
                        echo "skypilot: cached mount is still uploading to remote"
                        flushed=0
                        break
                    fi
                done
            done
            echo "skypilot: cached mount uploaded complete"
        fi""")

    def add_prologue(self, job_id: int) -> None:
        """Initialize code generator and add prologue code.

        Args:
            job_id: SkyPilot internal job ID
        """
        raise NotImplementedError

    def add_setup(
        self,
        num_nodes: int,
        resources_dict: Dict[str, float],
        stable_cluster_internal_ips: List[str],
        env_vars: Dict[str, str],
        setup_cmd: Optional[str] = None,
        setup_log_path: Optional[str] = None,
    ) -> None:
        """Generates code to set up the task on each node.

        stable_cluster_internal_ips is used to ensure that the
        SKYPILOT_NODE_RANK environment variable is assigned in a
        deterministic order whenever a new task is added.
        """
        raise NotImplementedError

    def add_tasks(
        self,
        num_nodes: int,
        bash_script: Optional[str],
        task_name: Optional[str],
        resources_dict: Dict[str, float],
        log_dir: str,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> None:
        """Generates code to run the bash command on each node."""
        raise NotImplementedError

    def add_epilogue(self) -> None:
        """Generate code that checks return codes and updates job status."""
        assert self._has_prologue, 'Call add_prologue() before add_epilogue().'
        assert not self._has_epilogue, 'add_epilogue() called twice?'
        self._has_epilogue = True

        self._code += [
            textwrap.dedent(f"""\
            if sum(returncodes) != 0:
                job_lib.set_status({self.job_id!r}, job_lib.JobStatus.FAILED)
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
                    reason = f'(A Worker failed with return code {{non_137}}, SkyPilot cleaned up the processes on other nodes with return code 137)'
                print('ERROR: {colorama.Fore.RED}Job {self.job_id} failed with '
                      'return code list:{colorama.Style.RESET_ALL}',
                      returncodes,
                      reason,
                      flush=True)
                # Need this to set the job status in ray job to be FAILED.
                sys.exit(1)
            else:
                job_lib.set_status({self.job_id!r}, job_lib.JobStatus.SUCCEEDED)
                # Schedule the next pending job immediately to make the job
                # scheduling more efficient.
                job_lib.scheduler.schedule_step()
                # This waits for all streaming logs to finish.
                time.sleep(0.5)
            """)
        ]

    def build(self) -> str:
        """Returns the entire generated program."""
        assert self._has_epilogue, 'Call add_epilogue() before build().'
        return '\n'.join(self._code)


class SlurmCodeGen(TaskCodeGen):
    """Code generator for task execution on Slurm using native srun."""

    def __init__(self, slurm_job_id: str):
        """Initialize SlurmCodeGen

        Args:
            slurm_job_id: The Slurm job ID, i.e. SLURM_JOB_ID
        """
        super().__init__()
        self._slurm_job_id = slurm_job_id

    def add_prologue(self, job_id: int) -> None:
        assert not self._has_prologue, 'add_prologue() called twice?'
        self._has_prologue = True
        self.job_id = job_id

        self._add_common_imports()

        self._add_skylet_imports()

        self._add_constants()

        self._add_logging_functions()

        self._code += [
            'autostop_lib.set_last_active_time_to_now()',
            f'job_lib.set_status({job_id!r}, job_lib.JobStatus.PENDING)',
            'has_setup_cmd = False',
            'setup_cmd = None',
            'setup_envs = None',
            'setup_log_path = None',
            'setup_num_nodes = None',
        ]

    def add_setup(
        self,
        num_nodes: int,
        resources_dict: Dict[str, float],
        stable_cluster_internal_ips: List[str],
        env_vars: Dict[str, str],
        setup_cmd: Optional[str] = None,
        setup_log_path: Optional[str] = None,
    ) -> None:
        assert self._has_prologue, ('Call add_prologue() before add_setup().')
        self._has_setup = True

        self._add_waiting_for_resources_msg(num_nodes)

        # Store setup information for use in add_tasks().
        if setup_cmd is not None:
            setup_envs = env_vars.copy()
            setup_envs[constants.SKYPILOT_NUM_NODES] = str(num_nodes)
            self._code += [
                textwrap.dedent(f"""\
                has_setup_cmd = True
                setup_cmd = {setup_cmd!r}
                setup_envs = {setup_envs!r}
                setup_log_path = {setup_log_path!r}
                setup_num_nodes = {num_nodes}
                """),
            ]

        # Get the IP addresses of the nodes in the job.
        # --overlap as this is a very cheap operation
        # and we don't want it to be blocked.
        self._code.append(
            textwrap.dedent(f"""\
            result = subprocess.run(
                ['srun', '--overlap', '--jobid={self._slurm_job_id}', '--nodes={num_nodes}', '--ntasks={num_nodes}',
                 '--ntasks-per-node=1', 'bash', '-c',
                 'hostname -I | awk "{{print \\$1}}"'],
                capture_output=True,
                text=True,
                check=True
            )
            job_ips = result.stdout.strip().split('\\n')
            cluster_ips_to_node_id = {{ip: i for i, ip in enumerate({stable_cluster_internal_ips!r})}}
            job_ip_rank_list = sorted(job_ips, key=cluster_ips_to_node_id.get)
            # Note: job_ip_rank_map is not needed for Slurm, as
            # we will use $SLURM_PROCID to get the node rank.
            job_ip_list_str = '\\n'.join(job_ip_rank_list)
            """))

    def add_tasks(
        self,
        num_nodes: int,
        bash_script: Optional[str],
        task_name: Optional[str],
        resources_dict: Dict[str, float],
        log_dir: str,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> None:
        """Generates code for invoking a bash command
        using srun within sbatch allocation.
        """
        assert self._has_setup, 'Call add_setup() before add_task().'
        env_vars = env_vars or {}
        task_name = task_name if task_name is not None else 'task'

        acc_name, acc_count = self._get_accelerator_details(resources_dict)
        num_gpus = 0
        if (acc_name is not None and
                not accelerator_registry.is_schedulable_non_gpu_accelerator(
                    acc_name)):
            num_gpus = int(math.ceil(acc_count))

        # Slurm does not support fractional CPUs.
        task_cpu_demand = int(math.ceil(resources_dict.pop('CPU')))

        # Note: $SLURM_* env vars are only defined when the generated script is
        # run with srun, which is the case with SlurmCommandRunner.run().
        sky_env_vars_dict_str = [
            textwrap.dedent(f"""\
            sky_env_vars_dict = {{}}
            sky_env_vars_dict['{constants.SKYPILOT_NODE_IPS}'] = job_ip_list_str
            sky_env_vars_dict['{constants.SKYPILOT_NUM_NODES}'] = os.environ.get('SLURM_NNODES') or os.environ.get('SLURM_JOB_NUM_NODES')
            sky_env_vars_dict['{constants.SKYPILOT_NODE_RANK}'] = os.environ.get('SLURM_PROCID')
            sky_env_vars_dict['SKYPILOT_INTERNAL_JOB_ID'] = {self.job_id}
            """)
        ]

        if env_vars:
            sky_env_vars_dict_str.extend(f'sky_env_vars_dict[{k!r}] = {v!r}'
                                         for k, v in env_vars.items())
        sky_env_vars_dict_str = '\n'.join(sky_env_vars_dict_str)

        rclone_flush_script = self._get_rclone_flush_script()
        streaming_msg = self._get_job_started_msg()

        self._code += [
            sky_env_vars_dict_str,
            textwrap.dedent(f"""\
            script = {bash_script!r}
            rclone_flush_script = {rclone_flush_script!r}

            if script is not None:
                script += rclone_flush_script
                sky_env_vars_dict['{constants.SKYPILOT_NUM_GPUS_PER_NODE}'] = {num_gpus}
                # TODO(kevin): Handle multi-node job log paths.
                log_path = os.path.expanduser(os.path.join({log_dir!r}, "run.log"))

                # Signal files that signal:
                # 1. srun has acquired allocation within its existing sbatch allocation.
                alloc_signal_file = f'/tmp/sky_alloc_{self._slurm_job_id}_{self.job_id}'
                # 2. Driver script has finished `setup` and `run` is ready to proceed.
                setup_done_signal_file = f'/tmp/sky_setup_done_{self._slurm_job_id}_{self.job_id}'

                # Add prolog to signal allocation and wait for setup to finish.
                # We also need to source ~/.bashrc again to make it as if the
                # run section is run in a new shell, after setup is finished.
                run_script = (
                    f"touch {{alloc_signal_file}} && "
                    f"while [ ! -f {{setup_done_signal_file}} ]; do sleep 0.05; done && "
                    f"rm -f {{setup_done_signal_file}} && "
                    "source ~/.bashrc && "
                    + script
                )
                # Start exclusive srun in a thread to reserve allocation (similar to ray.get(pg.ready()))
                gpu_arg = f'--gpus-per-node={num_gpus}' if {num_gpus} > 0 else ''
                def run_thread_func():
                    # This blocks until Slurm allocates resources (--exclusive)
                    # --mem=0 to match RayCodeGen's behavior where we don't explicitly request memory.
                    srun_cmd = f'srun --quiet --unbuffered --jobid={self._slurm_job_id} --nodes={num_nodes} --cpus-per-task={task_cpu_demand} --mem=0 --ntasks-per-node=1 {{gpu_arg}} --exclusive bash -c {{shlex.quote(run_script)}}'
                    result = run_bash_command_with_log_and_return_pid(
                        srun_cmd,
                        log_path,
                        env_vars=sky_env_vars_dict,
                        stream_logs=True,
                        with_ray=False,
                        streaming_prefix=f'{{colorama.Fore.CYAN}}({task_name}, pid={{{{pid}}}}){{colorama.Style.RESET_ALL}} ',
                    )
                    return result

                run_thread_result = {{'result': None}}
                def run_thread_wrapper():
                    run_thread_result['result'] = run_thread_func()

                run_thread = threading.Thread(target=run_thread_wrapper)
                run_thread.start()

                # Wait for allocation signal from inside srun
                while not os.path.exists(alloc_signal_file):
                    time.sleep(0.1)

                print({streaming_msg!r}, flush=True)

                if has_setup_cmd:
                    job_lib.set_status({self.job_id!r}, job_lib.JobStatus.SETTING_UP)

                    # The schedule_step should be called after the job status is set to
                    # non-PENDING, otherwise, the scheduler will think the current job
                    # is not submitted yet, and skip the scheduling step.
                    job_lib.scheduler.schedule_step()

                    # --overlap as we have already secured allocation with the srun for the run section,
                    # and otherwise this srun would get blocked and deadlock.
                    setup_srun = f'srun --quiet --unbuffered --overlap --jobid={self._slurm_job_id} --nodes={{setup_num_nodes}} --ntasks-per-node=1 bash -c {{shlex.quote(setup_cmd)}}'

                    setup_result = run_bash_command_with_log_and_return_pid(
                        setup_srun,
                        os.path.expanduser(setup_log_path),
                        env_vars=setup_envs,
                        stream_logs=True,
                        with_ray=False,
                        streaming_prefix=f'{{colorama.Fore.CYAN}}(setup pid={{{{pid}}}}){{colorama.Style.RESET_ALL}} ',
                    )

                    setup_returncode = int(setup_result.get('return_code', 1))
                    if setup_returncode != 0:
                        setup_pid = setup_result.get('pid', os.getpid())
                        msg = f'ERROR: {colorama.Fore.RED}Job {self.job_id}\\'s setup failed with return code {{setup_returncode}} (pid={{setup_pid}}).'
                        msg += f' See error logs above for more details.{colorama.Style.RESET_ALL}'
                        print(msg, flush=True)
                        job_lib.set_status({self.job_id!r}, job_lib.JobStatus.FAILED_SETUP)
                        time.sleep(1)
                        sys.exit(1)

                job_lib.set_job_started({self.job_id!r})
                if not has_setup_cmd:
                    # Need to call schedule_step() to make sure the scheduler
                    # schedule the next pending job.
                    job_lib.scheduler.schedule_step()

                # Signal run thread to proceed.
                pathlib.Path(setup_done_signal_file).touch()

                # Wait for run thread to complete.
                run_thread.join()
                result = run_thread_result['result']

                # Cleanup signal file
                if os.path.exists(alloc_signal_file):
                    os.remove(alloc_signal_file)

                returncodes = [int(result.get('return_code', 1))]
            else:
                returncodes = [0]
            """),
        ]
