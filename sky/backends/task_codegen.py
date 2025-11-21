"""Code generator for task execution on Ray and Slurm backends."""

import inspect
import textwrap
from typing import Dict, List, Optional

import colorama

from sky.skylet import constants
from sky.skylet import log_lib
from sky.utils import ux_utils


class TaskCodeGen:
    """Base code generator for task execution on Ray and Slurm.

    This class provides common functionality for code generation that both
    RayCodeGen and SlurmCodeGen can use.
    """

    def __init__(self):
        # Code generated so far, to be joined via '\n'.
        self._code = []
        # Guard method calling order.
        self._has_prologue = False
        self._has_epilogue = False
        self._has_register_run_fn = False

        # job_id is used to identify the job (also this generated code).
        self.job_id = None

    def _add_common_imports(self) -> None:
        """Add standard library imports common to both Ray and Slurm execution."""
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

    def _add_autostop_call(self) -> None:
        """Add autostop library initialization."""
        self._code.append(
            # Use hasattr to handle backward compatibility.
            # TODO(zongheng): remove in ~1-2 minor releases (currently 0.2.x).
            textwrap.dedent("""\
            if hasattr(autostop_lib, 'set_last_active_time_to_now'):
                autostop_lib.set_last_active_time_to_now()
            """))

    def _add_job_status_pending(self, job_id: int) -> None:
        """Set initial job status to PENDING."""
        self._code.append(
            f'job_lib.set_status({job_id!r}, job_lib.JobStatus.PENDING)')

    def _add_job_status_setting_up(self, job_id: int) -> None:
        """Set job status to SETTING_UP."""
        self._code.append(
            f'job_lib.set_status({job_id!r}, job_lib.JobStatus.SETTING_UP)')

    def _add_scheduler_schedule_step(self) -> None:
        """Schedule the next pending job."""
        self._code.append('job_lib.scheduler.schedule_step()')

    def _add_job_started(self, job_id: int) -> None:
        """Mark job as started."""
        self._code.append(f'job_lib.set_job_started({job_id!r})')

    def _add_constants(self) -> None:
        """Add common constants used by both Ray and Slurm."""
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

    def register_run_fn(self, run_fn_code: str, run_fn_name: str) -> None:
        """Register a callable run function to be included in generated code.

        This allows task.run to be a Python callable instead of a bash script.
        The function code is injected into the generated program and called
        dynamically to generate the bash script.

        Args:
            run_fn_code: Source code of the run function
            run_fn_name: Name of the run function
        """
        assert not self._has_register_run_fn, 'register_run_fn() called twice?'
        self._has_register_run_fn = True

        self._code += [
            run_fn_code,
            f'run_fn = {run_fn_name}',
        ]

    def add_prologue(self, job_id: int) -> None:
        """Initialize code generator and add prologue code.

        Args:
            job_id: SkyPilot internal job ID
        """
        raise NotImplementedError

    def add_gang_scheduling_and_setup(
        self,
        num_nodes: int,
        resources_dict: Dict[str, float],
        stable_cluster_internal_ips: List[str],
        env_vars: Dict[str, str],
        setup_cmd: Optional[str] = None,
        setup_log_path: Optional[str] = None,
    ) -> None:
        """Add gang scheduling coordination and setup command execution.

        Args:
            num_nodes: Number of nodes for the task
            resources_dict: Resource requirements (CPU, GPU, etc.)
            stable_cluster_internal_ips: Stable internal IPs of cluster nodes
            env_vars: Environment variables to set
            setup_cmd: Optional setup command to execute
            setup_log_path: Optional path to save setup logs
        """
        raise NotImplementedError

    def add_task(
        self,
        bash_script: Optional[str],
        task_name: Optional[str],
        resources_dict: Dict[str, float],
        log_dir: str,
        env_vars: Optional[Dict[str, str]] = None,
        gang_scheduling_id: int = 0,
        stable_cluster_internal_ips: Optional[List[str]] = None,
    ) -> None:
        """Add task execution code.

        Args:
            bash_script: Bash script to execute
            task_name: Optional task name for logging
            resources_dict: Resource requirements (CPU, GPU, etc.)
            log_dir: Directory for task logs
            env_vars: Optional environment variables to set
            gang_scheduling_id: ID for gang scheduling coordination (multi-node)
            stable_cluster_internal_ips: Stable internal IPs (for interface compatibility)
        """
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
        """Initialize SlurmCodeGen.

        Args:
            slurm_job_id: SLURM_JOB_ID from sbatch environment (provisioned job ID)
        """
        super().__init__()
        self._slurm_job_id = slurm_job_id
        self._num_nodes = 1  # Track for future multi-node support

    def add_prologue(self, job_id: int) -> None:
        """Initialize Slurm executor without Ray.

        Args:
            job_id: SkyPilot internal job ID
        """
        assert not self._has_prologue, 'add_prologue() called twice?'
        self._has_prologue = True
        self.job_id = job_id

        self._code = []

        # Add common imports
        self._add_common_imports()

        # Add skylet imports
        self._add_skylet_imports()

        # Add common constants
        self._add_constants()

        # Add logging functions
        self._add_logging_functions()

        # Note: No need to wrap functions with ray.remote() for Slurm execution
        # The functions from log_lib are used directly

        # Initialize run_fn variable (may be set via register_run_fn)
        self._code.append('run_fn = None')

        # Add autostop call
        self._add_autostop_call()

        # Set initial job status
        self._add_job_status_pending(job_id)

    def _add_node_discovery(self, num_nodes: int,
                            stable_cluster_internal_ips: List[str]) -> None:
        """Add code for discovering Slurm nodes.

        Args:
            num_nodes: Number of nodes in the task
            stable_cluster_internal_ips: Pre-fetched cluster IPs for sorting
        """
        self._num_nodes = num_nodes

        self._code.append(
            textwrap.dedent(f"""\
            result = subprocess.run(
                ['srun', '--jobid={self._slurm_job_id}', '--nodes={num_nodes}', '--ntasks={num_nodes}',
                 '--ntasks-per-node=1', 'bash', '-c',
                 'hostname -I | awk "{{print \\$1}}"'],
                capture_output=True,
                text=True,
                check=True
            )
            discovered_ips = result.stdout.strip().split('\\n')
            cluster_ips_to_node_id = {{ip: i for i, ip in enumerate({stable_cluster_internal_ips!r})}}
            node_ips = sorted(discovered_ips, key=cluster_ips_to_node_id.get)
            """))

    def _get_log_path_code(self, log_dir: str, num_nodes: int) -> str:
        """Generate code for determining log path.

        Mimics Ray's logic:
        - Single-node cluster: run.log
        - Multi-node cluster: {rank}-{head|workerN}.log

        Args:
            log_dir: Base log directory
            num_nodes: Number of nodes

        Returns:
            Python code string that sets 'log_path' variable
        """
        if num_nodes == 1:
            # Single-node: simple path
            return f'log_path = os.path.expanduser(os.path.join({log_dir!r}, "run.log"))'
        else:
            # Multi-node: rank-based paths (future)
            # TODO: Determine node name (head vs workerN) based on cluster position
            return textwrap.dedent(f"""\
            # Multi-node log paths: TODO - implement proper head/worker naming
            if node_rank == 0:
                node_name = 'head'
            else:
                node_name = f'worker{{node_rank}}'
            log_path = os.path.expanduser(os.path.join({log_dir!r}, f'{{node_rank}}-{{node_name}}.log'))
            """)

    def _build_env_vars_code(self, env_vars: Dict[str, str],
                             num_nodes: int) -> str:
        """Generate code for building environment variables dictionary.

        Args:
            env_vars: Base environment variables
            num_nodes: Number of nodes

        Returns:
            Python code string that builds sky_env_vars_dict
        """
        sky_env_vars_dict_str = [
            textwrap.dedent(f"""\
            sky_env_vars_dict = {{}}
            sky_env_vars_dict['{constants.SKYPILOT_NUM_NODES}'] = {num_nodes}
            sky_env_vars_dict['SKYPILOT_INTERNAL_JOB_ID'] = {self.job_id}
            sky_env_vars_dict['{constants.SKYPILOT_NODE_IPS}'] = '\\n'.join(node_ips)
            """)
        ]

        if env_vars:
            sky_env_vars_dict_str.extend(f'sky_env_vars_dict[{k!r}] = {v!r}'
                                         for k, v in env_vars.items())

        return '\n'.join(sky_env_vars_dict_str)

    def add_gang_scheduling_and_setup(
        self,
        num_nodes: int,
        resources_dict: Dict[str, float],
        stable_cluster_internal_ips: List[str],
        env_vars: Dict[str, str],
        setup_cmd: Optional[str] = None,
        setup_log_path: Optional[str] = None,
    ) -> None:
        """Unified interface: Add gang scheduling and setup.

        For Slurm, this handles setup command execution using srun.
        resources_dict is accepted but not used (Slurm uses sbatch allocation).
        """
        # Print the marker message that tail_logs expects
        # This allows log streaming to work properly
        # Must match LOG_FILE_START_STREAMING_AT in log_lib.py
        self._code.append(
            textwrap.dedent(f"""\
            plural = 's' if {num_nodes} > 1 else ''
            node_str = f'{num_nodes} node{{plural}}'
            message = ('{ux_utils.INDENT_SYMBOL}{colorama.Style.DIM}'
                       'Waiting for task resources on '
                       f'{{node_str}}.{colorama.Style.RESET_ALL}')
            print(message, flush=True)
            """))

        # Print streaming message after setup/gang scheduling is complete
        streaming_message = (
            f'{ux_utils.INDENT_LAST_SYMBOL}Job started. Streaming logs... '
            f'{colorama.Style.DIM}(Ctrl-C to exit log streaming; job will not '
            f'be killed){colorama.Style.RESET_ALL}')
        self._code.append(f'print({streaming_message!r}, flush=True)')

        if setup_cmd is not None:
            self._add_slurm_task_setup_impl(num_nodes, env_vars, setup_cmd,
                                            setup_log_path)
        else:
            self._add_job_started(self.job_id)
            self._add_scheduler_schedule_step()

    def _add_slurm_task_setup_impl(
        self,
        num_nodes: int,
        env_vars: Dict[str, str],
        setup_cmd: str,
        setup_log_path: str,
    ) -> None:
        """Add setup command execution using srun.

        Args:
            num_nodes: Number of nodes for the task
            env_vars: Environment variables to set
            setup_cmd: Setup command to execute
            setup_log_path: Path to save setup logs
        """
        assert self._has_prologue, 'Call add_prologue() before add_slurm_task_setup().'

        setup_envs = env_vars.copy()
        setup_envs[constants.SKYPILOT_NUM_NODES] = str(num_nodes)

        self._code += [
            textwrap.dedent(f"""\
            setup_cmd = {setup_cmd!r}
            setup_cmd = 'export SKYPILOT_NODE_RANK=$SLURM_PROCID; ' + setup_cmd
            # Unset CUDA_VISIBLE_DEVICES so setup can properly use this env var
            setup_cmd = 'unset CUDA_VISIBLE_DEVICES; ' + setup_cmd

            job_lib.set_status({self.job_id!r}, job_lib.JobStatus.SETTING_UP)
            job_lib.scheduler.schedule_step()

            # Run setup command on all nodes using srun
            # srun distributes across nodes automatically (single-node now, multi-node ready)
            setup_srun = f'srun --unbuffered --jobid={self._slurm_job_id} --nodes={num_nodes} --ntasks-per-node=1 bash -c {{shlex.quote(setup_cmd)}}'

            setup_result = run_bash_command_with_log_and_return_pid(
                setup_srun,
                os.path.expanduser({setup_log_path!r}),
                env_vars={setup_envs!r},
                stream_logs=True,
                with_ray=False,
                streaming_prefix=f'{{colorama.Fore.CYAN}}(sky-cmd, pid={{{{pid}}}}){{colorama.Style.RESET_ALL}} ',
            )

            # run_bash_command_with_log_and_return_pid returns dict with 'return_code' and 'pid' keys
            setup_returncode = int(setup_result.get('return_code', 1))
            setup_pid = setup_result.get('pid', os.getpid())

            if setup_returncode != 0:
                msg = f'ERROR: {colorama.Fore.RED}Job {self.job_id}\\'s setup failed with return code {{setup_returncode}} (pid={{setup_pid}}).'
                msg += f' See error logs above for more details.{colorama.Style.RESET_ALL}'
                print(msg, flush=True)
                job_lib.set_status({self.job_id!r}, job_lib.JobStatus.FAILED_SETUP)
                time.sleep(1)
                sys.exit(1)
            """),
        ]

        self._add_job_started(self.job_id)
        self._add_scheduler_schedule_step()

    def add_task(
        self,
        bash_script: Optional[str],
        task_name: Optional[str],
        resources_dict: Dict[str, float],
        log_dir: str,
        env_vars: Optional[Dict[str, str]] = None,
        gang_scheduling_id: int = 0,
        stable_cluster_internal_ips: Optional[List[str]] = None,
    ) -> None:
        """Unified interface: Add task execution.

        For Slurm, uses srun for execution within sbatch allocation.
        resources_dict is used to extract GPU count for SKYPILOT_NUM_GPUS_PER_NODE.
        Slurm doesn't use it for scheduling (sbatch handles allocation).
        gang_scheduling_id is accepted but not used for single-node.
        """
        # Determine num_nodes from self._num_nodes set during add_gang_scheduling_and_setup
        num_nodes = getattr(self, '_num_nodes', 1)
        env_vars = env_vars or {}

        # Extract GPU count from resources_dict
        # Remove 'CPU' as it's not needed for GPU count
        resources_dict_copy = resources_dict.copy()
        resources_dict_copy.pop('CPU', None)

        num_gpus = 0
        if resources_dict_copy:
            # Extract GPU count (similar to Ray's logic)
            num_gpus = int(list(resources_dict_copy.values())[0])

        self._add_slurm_task_impl(bash_script, num_nodes, env_vars, log_dir,
                                  task_name, stable_cluster_internal_ips,
                                  num_gpus)

    def _add_slurm_task_impl(
        self,
        bash_script: str,
        num_nodes: int,
        env_vars: Dict[str, str],
        log_dir: str,
        task_name: Optional[str] = None,
        stable_cluster_internal_ips: Optional[List[str]] = None,
        num_gpus: int = 0,
    ) -> None:
        """Add task execution using srun.

        Args:
            bash_script: Bash script to execute
            num_nodes: Number of nodes for the task
            env_vars: Environment variables to set
            log_dir: Directory for task logs
            task_name: Optional task name for logging
            stable_cluster_internal_ips: Stable IPs for multi-node coordination (future)
            num_gpus: Number of GPUs per node (for SKYPILOT_NUM_GPUS_PER_NODE)
        """
        assert self._has_prologue, 'Call add_prologue() before add_slurm_task().'

        # Add node discovery (extensible for multi-node)
        self._add_node_discovery(num_nodes, stable_cluster_internal_ips)

        # Build environment variables using helper
        sky_env_vars_dict_str = self._build_env_vars_code(env_vars, num_nodes)

        # Get rclone flush script from base class
        rclone_flush_script = self._get_rclone_flush_script()

        # Get log path code (extensible for multi-node)
        log_path_code = self._get_log_path_code(log_dir, num_nodes)

        self._code += [
            sky_env_vars_dict_str,
            log_path_code,
            textwrap.dedent(f"""\
            script = {bash_script!r}
            script = 'export SKYPILOT_NODE_RANK=$SLURM_PROCID; ' + script
            rclone_flush_script = {rclone_flush_script!r}

            # If run_fn is registered, call it to generate the script dynamically
            if run_fn is not None:
                # For Slurm: pass gang_scheduling_id=0 and node_ips
                script = run_fn(0, node_ips)

            if script is not None:
                script += rclone_flush_script
                sky_env_vars_dict['{constants.SKYPILOT_NUM_GPUS_PER_NODE}'] = {num_gpus}

                # Wrap script with srun to execute within sbatch allocation
                # srun distributes across nodes automatically (single-node now, multi-node ready)
                # Note: srun automatically inherits GPU allocation from sbatch (via --jobid).
                # CUDA_VISIBLE_DEVICES is set by Slurm at the sbatch level and inherited here.
                # No need to specify --gres again since we're using all allocated GPUs.
                srun_script = f'srun --unbuffered --jobid={self._slurm_job_id} --nodes={num_nodes} --ntasks-per-node=1 bash -c {{shlex.quote(script)}}'

                result = run_bash_command_with_log_and_return_pid(
                    srun_script,
                    log_path,
                    env_vars=sky_env_vars_dict,
                    stream_logs=True,
                    with_ray=False,
                    streaming_prefix=f'{{colorama.Fore.CYAN}}(sky-cmd, pid={{{{pid}}}}){{colorama.Style.RESET_ALL}} ',
                )

                # run_bash_command_with_log_and_return_pid returns dict with 'return_code' and 'pid' keys
                returncodes = [int(result.get('return_code', 1))]
            else:
                returncodes = [0]
            """),
        ]

    def add_epilogue(self) -> None:
        """Generate epilogue that checks return codes and updates job status."""
        super().add_epilogue()
