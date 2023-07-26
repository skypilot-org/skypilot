"""Backend: runs on cloud virtual machines, managed by Ray."""
import ast
import copy
import enum
import getpass
import inspect
import math
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import typing
from typing import Dict, Iterable, List, Optional, Tuple, Union, Set

import colorama
import filelock

import sky
from sky import backends
from sky import clouds
from sky import cloud_stores
from sky import exceptions
from sky import global_user_state
from sky import provision as provision_api
from sky import resources as resources_lib
from sky import sky_logging
from sky import optimizer
from sky import skypilot_config
from sky import spot as spot_lib
from sky import status_lib
from sky import task as task_lib
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.backends import backend_utils
from sky.backends import onprem_utils
from sky.backends import wheel_utils
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.usage import usage_lib
from sky.utils import common_utils
from sky.utils import command_runner
from sky.utils import log_utils
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import tpu_utils
from sky.utils import ux_utils
from sky.skylet.providers.scp.node_provider import SCPNodeProvider, SCPError

if typing.TYPE_CHECKING:
    from sky import dag

Path = str

SKY_REMOTE_APP_DIR = backend_utils.SKY_REMOTE_APP_DIR
SKY_REMOTE_WORKDIR = constants.SKY_REMOTE_WORKDIR

logger = sky_logging.init_logger(__name__)

_PATH_SIZE_MEGABYTES_WARN_THRESHOLD = 256

# Timeout (seconds) for provision progress: if in this duration no new nodes
# are launched, abort and failover.
_NODES_LAUNCHING_PROGRESS_TIMEOUT = {
    clouds.AWS: 90,
    clouds.Azure: 90,
    clouds.GCP: 240,
    clouds.Lambda: 150,
    clouds.IBM: 160,
    clouds.Local: 90,
    clouds.OCI: 300,
}

# Time gap between retries after failing to provision in all possible places.
# Used only if --retry-until-up is set.
_RETRY_UNTIL_UP_INIT_GAP_SECONDS = 60

# The maximum retry count for fetching IP address.
_FETCH_IP_MAX_ATTEMPTS = 3

_TEARDOWN_FAILURE_MESSAGE = (
    f'\n{colorama.Fore.RED}Failed to terminate '
    '{cluster_name}. {extra_reason}'
    'If you want to ignore this error and remove the cluster '
    'from the status table, use `sky down --purge`.'
    f'{colorama.Style.RESET_ALL}\n'
    '**** STDOUT ****\n'
    '{stdout}\n'
    '**** STDERR ****\n'
    '{stderr}')

_TEARDOWN_PURGE_WARNING = (
    f'{colorama.Fore.YELLOW}'
    'WARNING: Received non-zero exit code from {reason}. '
    'Make sure resources are manually deleted.\n'
    'Details: {details}'
    f'{colorama.Style.RESET_ALL}')

_RSYNC_NOT_FOUND_MESSAGE = (
    '`rsync` command is not found in the specified image. '
    'Please use an image with rsync installed.')

_TPU_NOT_FOUND_ERROR = 'ERROR: (gcloud.compute.tpus.delete) NOT_FOUND'

_CTRL_C_TIP_MESSAGE = ('INFO: Tip: use Ctrl-C to exit log streaming '
                       '(task will not be killed).')

_MAX_RAY_UP_RETRY = 5

# Number of retries for getting zones.
_MAX_GET_ZONE_RETRY = 3

_JOB_ID_PATTERN = re.compile(r'Job ID: ([0-9]+)')

# Path to the monkey-patched ray up script.
# We don't do import then __file__ because that script needs to be filled in
# (so import would fail).
_RAY_UP_WITH_MONKEY_PATCHED_HASH_LAUNCH_CONF_PATH = (
    pathlib.Path(sky.__file__).resolve().parent / 'backends' /
    'monkey_patches' / 'monkey_patch_ray_up.py')

# Restart skylet when the version does not match to keep the skylet up-to-date.
_MAYBE_SKYLET_RESTART_CMD = 'python3 -m sky.skylet.attempt_skylet'


def _get_cluster_config_template(cloud):
    cloud_to_template = {
        clouds.AWS: 'aws-ray.yml.j2',
        clouds.Azure: 'azure-ray.yml.j2',
        clouds.GCP: 'gcp-ray.yml.j2',
        clouds.Lambda: 'lambda-ray.yml.j2',
        clouds.IBM: 'ibm-ray.yml.j2',
        clouds.Local: 'local-ray.yml.j2',
        clouds.SCP: 'scp-ray.yml.j2',
        clouds.OCI: 'oci-ray.yml.j2',
    }
    return cloud_to_template[type(cloud)]


def write_ray_up_script_with_patched_launch_hash_fn(
    cluster_config_path: str,
    ray_up_kwargs: Dict[str, bool],
) -> str:
    """Writes a Python script that runs `ray up` with our launch hash func.

    Our patched launch hash has one difference from the non-patched version: it
    does not include any `ssh_proxy_command` under `auth` as part of the hash
    calculation.
    """
    with open(_RAY_UP_WITH_MONKEY_PATCHED_HASH_LAUNCH_CONF_PATH, 'r') as f:
        ray_up_no_restart_script = f.read().format(
            ray_yaml_path=repr(cluster_config_path),
            ray_up_kwargs=ray_up_kwargs)
    with tempfile.NamedTemporaryFile('w',
                                     prefix='skypilot_ray_up_',
                                     suffix='.py',
                                     delete=False) as f:
        f.write(ray_up_no_restart_script)
        logger.debug(f'`ray up` script: {f.name}')
    return f.name


class RayCodeGen:
    """Code generator of a Ray program that executes a sky.Task.

    Usage:

      >> codegen = RayCodegen()
      >> codegen.add_prologue()

      >> codegen.add_ray_task(...)
      >> codegen.add_ray_task(...)

      >> codegen.add_epilogue()
      >> code = codegen.build()
    """

    def __init__(self):
        # Code generated so far, to be joined via '\n'.
        self._code = []
        # Guard method calling order.
        self._has_prologue = False
        self._has_epilogue = False

        # For n nodes gang scheduling.
        self._has_gang_scheduling = False
        self._num_nodes = 0

        self._has_register_run_fn = False

        # job_id
        # Job ID is used to identify the job (also this generated code).
        # It is a int automatically generated by the DB on the cluster
        # and monotonically increasing starting from 1.
        # To generate the job ID, we use the following logic:
        #   code = job_lib.JobLibCodeGen.add_job(username,
        #                                              run_timestamp)
        #   job_id = get_output(run_on_cluster(code))
        self.job_id = None

    def add_prologue(self, job_id: int, is_local: bool = False) -> None:
        assert not self._has_prologue, 'add_prologue() called twice?'
        self._has_prologue = True
        self.job_id = job_id
        self.is_local = is_local
        # Should use 'auto' or 'ray://<internal_head_ip>:10001' rather than
        # 'ray://localhost:10001', or 'ray://127.0.0.1:10001', for public cloud.
        # Otherwise, ray will fail to get the placement group because of a bug
        # in ray job.
        # TODO(mluo): Check why 'auto' not working with on-prem cluster and
        # whether the placement group issue also occurs in on-prem cluster.
        ray_address = 'ray://localhost:10001' if is_local else 'auto'
        self._code = [
            textwrap.dedent(f"""\
            import getpass
            import hashlib
            import io
            import os
            import pathlib
            import sys
            import selectors
            import subprocess
            import tempfile
            import textwrap
            import time
            from typing import Dict, List, Optional, Tuple, Union

            import ray
            import ray.util as ray_util

            from sky.skylet import autostop_lib
            from sky.skylet import constants
            from sky.skylet import job_lib
            from sky.utils import log_utils

            SKY_REMOTE_WORKDIR = {constants.SKY_REMOTE_WORKDIR!r}

            kwargs = dict()
            # Only set the `_temp_dir` to SkyPilot's ray cluster directory when the directory
            # exists for backward compatibility for the VM launched before #1790.
            if os.path.exists({constants.SKY_REMOTE_RAY_TEMPDIR!r}):
                kwargs['_temp_dir'] = {constants.SKY_REMOTE_RAY_TEMPDIR!r}
            ray.init(
                address={ray_address!r},
                namespace='__sky__{job_id}__',
                log_to_driver=True,
                **kwargs
            )
            run_fn = None
            futures = []
            """),
            # FIXME: This is a hack to make sure that the functions can be found
            # by ray.remote. This should be removed once we have a better way to
            # specify dependencies for ray.
            inspect.getsource(log_lib._ProcessingArgs),  # pylint: disable=protected-access
            inspect.getsource(log_lib._handle_io_stream),  # pylint: disable=protected-access
            inspect.getsource(log_lib.process_subprocess_stream),
            inspect.getsource(log_lib.run_with_log),
            inspect.getsource(log_lib.make_task_bash_script),
            inspect.getsource(log_lib.add_ray_env_vars),
            inspect.getsource(log_lib.run_bash_command_with_log),
            'run_bash_command_with_log = ray.remote(run_bash_command_with_log)',
        ]
        # Currently, the codegen program is/can only be submitted to the head
        # node, due to using job_lib for updating job statuses, and using
        # autostop_lib here.
        self._code.append(
            # Use hasattr to handle backward compatibility.
            # TODO(zongheng): remove in ~1-2 minor releases (currently 0.2.x).
            textwrap.dedent("""\
              if hasattr(autostop_lib, 'set_last_active_time_to_now'):
                  autostop_lib.set_last_active_time_to_now()
            """))
        self._code += [
            f'job_lib.set_status({job_id!r}, job_lib.JobStatus.PENDING)',
        ]

    def add_gang_scheduling_placement_group_and_setup(
        self,
        num_nodes: int,
        accelerator_dict: Optional[Dict[str, float]],
        stable_cluster_internal_ips: List[str],
        setup_cmd: Optional[str] = None,
        setup_log_path: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
    ) -> None:
        """Create the gang scheduling placement group for a Task.

        cluster_ips_sorted is used to ensure that the SKY_NODE_RANK environment
        variable is assigned in a deterministic order whenever a new task is
        added.
        """
        assert self._has_prologue, (
            'Call add_prologue() before '
            'add_gang_scheduling_placement_group_and_setup().')
        self._has_gang_scheduling = True
        self._num_nodes = num_nodes

        # Set CPU to avoid ray hanging the resources allocation
        # for remote functions, since the task will request 1 CPU
        # by default.
        bundles = [{
            'CPU': backend_utils.DEFAULT_TASK_CPU_DEMAND
        } for _ in range(num_nodes)]

        if accelerator_dict is not None:
            acc_name = list(accelerator_dict.keys())[0]
            acc_count = list(accelerator_dict.values())[0]
            gpu_dict = {'GPU': acc_count}
            # gpu_dict should be empty when the accelerator is not GPU.
            # FIXME: This is a hack to make sure that we do not reserve
            # GPU when requesting TPU.
            if 'tpu' in acc_name.lower():
                gpu_dict = {}
            for bundle in bundles:
                bundle.update({
                    **accelerator_dict,
                    # Set the GPU to avoid ray hanging the resources allocation
                    **gpu_dict,
                })

        self._code += [
            textwrap.dedent(f"""\
                pg = ray_util.placement_group({json.dumps(bundles)}, 'STRICT_SPREAD')
                plural = 's' if {num_nodes} > 1 else ''
                node_str = f'{num_nodes} node{{plural}}'

                message = {_CTRL_C_TIP_MESSAGE!r} + '\\n'
                message += f'INFO: Waiting for task resources on {{node_str}}. This will block if the cluster is full.'
                print(message,
                      file=sys.stderr,
                      flush=True)
                # FIXME: This will print the error message from autoscaler if
                # it is waiting for other task to finish. We should hide the
                # error message.
                ray.get(pg.ready())
                print('INFO: All task resources reserved.',
                      file=sys.stderr,
                      flush=True)
                """)
        ]

        job_id = self.job_id
        if setup_cmd is not None:
            self._code += [
                textwrap.dedent(f"""\
                setup_cmd = {setup_cmd!r}
                _SETUP_CPUS = 0.0001
                # The setup command will be run as a ray task with num_cpus=_SETUP_CPUS as the
                # requirement; this means Ray will set CUDA_VISIBLE_DEVICES to an empty string.
                # We unset it so that user setup command may properly use this env var.
                setup_cmd = 'unset CUDA_VISIBLE_DEVICES; ' + setup_cmd
                job_lib.set_status({job_id!r}, job_lib.JobStatus.SETTING_UP)

                # The schedule_step should be called after the job status is set to non-PENDING,
                # otherwise, the scheduler will think the current job is not submitted yet, and
                # skip the scheduling step.
                job_lib.scheduler.schedule_step()

                total_num_nodes = len(ray.nodes())
                setup_bundles = [{{"CPU": _SETUP_CPUS}} for _ in range(total_num_nodes)]
                setup_pg = ray.util.placement_group(setup_bundles, strategy='STRICT_SPREAD')
                setup_workers = [run_bash_command_with_log \\
                    .options(
                        name='setup',
                        num_cpus=_SETUP_CPUS,
                        scheduling_strategy=ray.util.scheduling_strategies.PlacementGroupSchedulingStrategy(
                            placement_group=setup_pg,
                            placement_group_bundle_index=i)
                    ) \\
                    .remote(
                        setup_cmd,
                        os.path.expanduser({setup_log_path!r}),
                        getpass.getuser(),
                        job_id={self.job_id},
                        env_vars={envs!r},
                        stream_logs=True,
                        with_ray=True,
                        use_sudo={self.is_local},
                    ) for i in range(total_num_nodes)]
                setup_returncodes = ray.get(setup_workers)
                if sum(setup_returncodes) != 0:
                    job_lib.set_status({self.job_id!r}, job_lib.JobStatus.FAILED_SETUP)
                    # This waits for all streaming logs to finish.
                    time.sleep(1)
                    print('ERROR: {colorama.Fore.RED}Job {self.job_id}\\'s setup failed with '
                        'return code list:{colorama.Style.RESET_ALL}',
                        setup_returncodes,
                        file=sys.stderr,
                        flush=True)
                    # Need this to set the job status in ray job to be FAILED.
                    sys.exit(1)
                """)
            ]

        self._code.append(f'job_lib.set_job_started({self.job_id!r})')
        if setup_cmd is None:
            # Need to call schedule_step() to make sure the scheduler
            # schedule the next pending job.
            self._code.append('job_lib.scheduler.schedule_step()')

        # Export IP and node rank to the environment variables.
        self._code += [
            textwrap.dedent(f"""\
                @ray.remote
                def check_ip():
                    return ray.util.get_node_ip_address()
                gang_scheduling_id_to_ip = ray.get([
                    check_ip.options(
                            num_cpus={backend_utils.DEFAULT_TASK_CPU_DEMAND},
                            scheduling_strategy=ray.util.scheduling_strategies.PlacementGroupSchedulingStrategy(
                                placement_group=pg,
                                placement_group_bundle_index=i
                            )).remote()
                    for i in range(pg.bundle_count)
                ])
                print('INFO: Reserved IPs:', gang_scheduling_id_to_ip)

                cluster_ips_to_node_id = {{ip: i for i, ip in enumerate({stable_cluster_internal_ips!r})}}
                job_ip_rank_list = sorted(gang_scheduling_id_to_ip, key=cluster_ips_to_node_id.get)
                job_ip_rank_map = {{ip: i for i, ip in enumerate(job_ip_rank_list)}}
                job_ip_list_str = '\\n'.join(job_ip_rank_list)
                """),
        ]

    def register_run_fn(self, run_fn: str, run_fn_name: str) -> None:
        """Register the run function to be run on the remote cluster.

        Args:
            run_fn: The run function to be run on the remote cluster.
        """
        assert self._has_gang_scheduling, (
            'Call add_gang_scheduling_placement_group_and_setup() '
            'before register_run_fn().')
        assert not self._has_register_run_fn, (
            'register_run_fn() called twice?')
        self._has_register_run_fn = True

        self._code += [
            run_fn,
            f'run_fn = {run_fn_name}',
        ]

    def add_ray_task(self,
                     bash_script: Optional[str],
                     task_name: Optional[str],
                     job_run_id: Optional[str],
                     ray_resources_dict: Optional[Dict[str, float]],
                     log_dir: str,
                     env_vars: Optional[Dict[str, str]] = None,
                     gang_scheduling_id: int = 0,
                     use_sudo: bool = False) -> None:
        """Generates code for a ray remote task that runs a bash command."""
        assert self._has_gang_scheduling, (
            'Call add_gang_scheduling_placement_group_and_setup() before '
            'add_ray_task().')
        assert (not self._has_register_run_fn or
                bash_script is None), ('bash_script should '
                                       'be None when run_fn is registered.')
        # Build remote_task.options(...)
        #   resources=...
        #   num_gpus=...
        options = []
        options.append(f'num_cpus={backend_utils.DEFAULT_TASK_CPU_DEMAND}')

        num_gpus = 0.0
        if ray_resources_dict is not None:
            assert len(ray_resources_dict) == 1, \
                ('There can only be one type of accelerator per instance.'
                 f' Found: {ray_resources_dict}.')
            num_gpus = list(ray_resources_dict.values())[0]
            options.append(f'resources={json.dumps(ray_resources_dict)}')

            resources_key = list(ray_resources_dict.keys())[0]
            if 'tpu' not in resources_key.lower():
                # `num_gpus` should be empty when the accelerator is not GPU.
                # FIXME: use a set of GPU types, instead of 'tpu' in the key.

                # Passing this ensures that the Ray remote task gets
                # CUDA_VISIBLE_DEVICES set correctly.  If not passed, that flag
                # would be force-set to empty by Ray.
                options.append(f'num_gpus={num_gpus}')
        options.append(
            'scheduling_strategy=ray.util.scheduling_strategies.PlacementGroupSchedulingStrategy('  # pylint: disable=line-too-long
            'placement_group=pg, '
            f'placement_group_bundle_index={gang_scheduling_id})')

        sky_env_vars_dict_str = [
            textwrap.dedent("""\
            sky_env_vars_dict = {}
            sky_env_vars_dict['SKYPILOT_NODE_IPS'] = job_ip_list_str
            # Environment starting with `SKY_` is deprecated.
            sky_env_vars_dict['SKY_NODE_IPS'] = job_ip_list_str
            """)
        ]

        if env_vars is not None:
            sky_env_vars_dict_str.extend(f'sky_env_vars_dict[{k!r}] = {v!r}'
                                         for k, v in env_vars.items())
        if job_run_id is not None:
            sky_env_vars_dict_str += [
                f'sky_env_vars_dict[{constants.TASK_ID_ENV_VAR!r}]'
                f' = {job_run_id!r}',
                # TODO(zhwu): remove this deprecated env var in later release
                # (after 0.5).
                f'sky_env_vars_dict[{constants.TASK_ID_ENV_VAR_DEPRECATED!r}]'
                f' = {job_run_id!r}'
            ]
        sky_env_vars_dict_str = '\n'.join(sky_env_vars_dict_str)

        options_str = ', '.join(options)
        logger.debug('Added Task with options: '
                     f'{options_str}')
        self._code += [
            sky_env_vars_dict_str,
            textwrap.dedent(f"""\
        script = {bash_script!r}
        if run_fn is not None:
            script = run_fn({gang_scheduling_id}, gang_scheduling_id_to_ip)


        if script is not None:
            sky_env_vars_dict['SKYPILOT_NUM_GPUS_PER_NODE'] = {int(math.ceil(num_gpus))!r}
            # Environment starting with `SKY_` is deprecated.
            sky_env_vars_dict['SKY_NUM_GPUS_PER_NODE'] = {int(math.ceil(num_gpus))!r}

            ip = gang_scheduling_id_to_ip[{gang_scheduling_id!r}]
            rank = job_ip_rank_map[ip]

            if len(cluster_ips_to_node_id) == 1: # Single-node task on single-node cluter
                name_str = '{task_name},' if {task_name!r} != None else 'task,'
                log_path = os.path.expanduser(os.path.join({log_dir!r}, 'run.log'))
            else: # Single-node or multi-node task on multi-node cluster
                idx_in_cluster = cluster_ips_to_node_id[ip]
                if cluster_ips_to_node_id[ip] == 0:
                    node_name = 'head'
                else:
                    node_name = f'worker{{idx_in_cluster}}'
                name_str = f'{{node_name}}, rank={{rank}},'
                log_path = os.path.expanduser(os.path.join({log_dir!r}, f'{{rank}}-{{node_name}}.log'))
            sky_env_vars_dict['SKYPILOT_NODE_RANK'] = rank
            # Environment starting with `SKY_` is deprecated.
            sky_env_vars_dict['SKY_NODE_RANK'] = rank

            sky_env_vars_dict['SKYPILOT_INTERNAL_JOB_ID'] = {self.job_id}
            # Environment starting with `SKY_` is deprecated.
            sky_env_vars_dict['SKY_INTERNAL_JOB_ID'] = {self.job_id}

            futures.append(run_bash_command_with_log \\
                    .options(name=name_str, {options_str}) \\
                    .remote(
                        script,
                        log_path,
                        getpass.getuser(),
                        job_id={self.job_id},
                        env_vars=sky_env_vars_dict,
                        stream_logs=True,
                        with_ray=True,
                        use_sudo={use_sudo},
                    ))""")
        ]

    def add_epilogue(self) -> None:
        """Generates code that waits for all tasks, then exits."""
        assert self._has_prologue, 'Call add_prologue() before add_epilogue().'
        assert not self._has_epilogue, 'add_epilogue() called twice?'
        self._has_epilogue = True

        self._code += [
            textwrap.dedent(f"""\
            returncodes = ray.get(futures)
            if sum(returncodes) != 0:
                job_lib.set_status({self.job_id!r}, job_lib.JobStatus.FAILED)
                # This waits for all streaming logs to finish.
                job_lib.scheduler.schedule_step()
                time.sleep(0.5)
                print('ERROR: {colorama.Fore.RED}Job {self.job_id} failed with '
                      'return code list:{colorama.Style.RESET_ALL}',
                      returncodes,
                      file=sys.stderr,
                      flush=True)
                # Need this to set the job status in ray job to be FAILED.
                sys.exit(1)
            else:
                sys.stdout.flush()
                sys.stderr.flush()
                job_lib.set_status({self.job_id!r}, job_lib.JobStatus.SUCCEEDED)
                # This waits for all streaming logs to finish.
                job_lib.scheduler.schedule_step()
                time.sleep(0.5)
            """)
        ]

    def build(self) -> str:
        """Returns the entire generated program."""
        assert self._has_epilogue, 'Call add_epilogue() before build().'
        return '\n'.join(self._code)


class RetryingVmProvisioner(object):
    """A provisioner that retries different cloud/regions/zones."""

    class ToProvisionConfig:
        """Resources to be provisioned."""

        def __init__(
                self, cluster_name: str, resources: resources_lib.Resources,
                num_nodes: int,
                prev_cluster_status: Optional[status_lib.ClusterStatus]
        ) -> None:
            assert cluster_name is not None, 'cluster_name must be specified.'
            self.cluster_name = cluster_name
            self.resources = resources
            self.num_nodes = num_nodes
            self.prev_cluster_status = prev_cluster_status

    class GangSchedulingStatus(enum.Enum):
        """Enum for gang scheduling status."""
        CLUSTER_READY = 0
        GANG_FAILED = 1
        HEAD_FAILED = 2

    def __init__(self, log_dir: str, dag: 'dag.Dag',
                 optimize_target: 'optimizer.OptimizeTarget',
                 requested_features: Set[clouds.CloudImplementationFeatures],
                 local_wheel_path: pathlib.Path, wheel_hash: str):
        self._blocked_resources: Set[resources_lib.Resources] = set()

        self.log_dir = os.path.expanduser(log_dir)
        self._dag = dag
        self._optimize_target = optimize_target
        self._requested_features = requested_features
        self._local_wheel_path = local_wheel_path
        self._wheel_hash = wheel_hash

    def _update_blocklist_on_gcp_error(
            self, launchable_resources: 'resources_lib.Resources',
            region: 'clouds.Region', zones: Optional[List['clouds.Zone']],
            stdout: str, stderr: str):

        del region  # unused
        style = colorama.Style
        assert zones and len(zones) == 1, zones
        zone = zones[0]
        splits = stderr.split('\n')
        exception_list = [s for s in splits if s.startswith('Exception: ')]
        httperror_str = [
            s for s in splits
            if s.startswith('googleapiclient.errors.HttpError: ')
        ]
        if len(exception_list) == 1:
            # Parse structured response {'errors': [...]}.
            exception_str = exception_list[0][len('Exception: '):]
            try:
                exception_dict = ast.literal_eval(exception_str)
            except Exception as e:
                if 'wait_ready timeout exceeded' in exception_str:
                    # This error seems to occur when the provisioning process
                    # went through partially (e.g., for spot, initial
                    # provisioning succeeded, but while waiting for ssh/setting
                    # up it got preempted).
                    logger.error('Got the following exception, continuing: '
                                 f'{exception_list[0]}')
                    self._blocked_resources.add(
                        launchable_resources.copy(zone=zone.name))
                    return
                raise RuntimeError(
                    f'Failed to parse exception: {exception_str}') from e
            # TPU VM returns a different structured response.
            if 'errors' not in exception_dict:
                exception_dict = {'errors': [exception_dict]}
            for error in exception_dict['errors']:
                code = error['code']
                message = error['message']
                logger.warning(f'Got return code {code} in {zone.name} '
                               f'{style.DIM}(message: {message})'
                               f'{style.RESET_ALL}')
                if code == 'QUOTA_EXCEEDED':
                    if '\'GPUS_ALL_REGIONS\' exceeded' in message:
                        # Global quota.  All regions in GCP will fail.  Ex:
                        # Quota 'GPUS_ALL_REGIONS' exceeded.  Limit: 1.0
                        # globally.
                        # This skip is only correct if we implement "first
                        # retry the region/zone of an existing cluster with the
                        # same name" correctly.
                        self._blocked_resources.add(
                            launchable_resources.copy(region=None, zone=None))
                    else:
                        # Per region.  Ex: Quota 'CPUS' exceeded.  Limit: 24.0
                        # in region us-west1.
                        self._blocked_resources.add(
                            launchable_resources.copy(zone=None))
                elif code in [
                        'ZONE_RESOURCE_POOL_EXHAUSTED',
                        'ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS',
                        'UNSUPPORTED_OPERATION'
                ]:  # Per zone.
                    # Return codes can be found at https://cloud.google.com/compute/docs/troubleshooting/troubleshooting-vm-creation # pylint: disable=line-too-long
                    # However, UNSUPPORTED_OPERATION is observed empirically
                    # when VM is preempted during creation.  This seems to be
                    # not documented by GCP.
                    self._blocked_resources.add(
                        launchable_resources.copy(zone=zone.name))
                elif code in ['RESOURCE_NOT_READY']:
                    # This code is returned when the VM is still STOPPING.
                    self._blocked_resources.add(
                        launchable_resources.copy(zone=zone.name))
                elif code in [8, 9]:
                    # Error code 8 means TPU resources is out of
                    # capacity. Example:
                    # {'code': 8, 'message': 'There is no more capacity in the zone "europe-west4-a"; you can try in another zone where Cloud TPU Nodes are offered (see https://cloud.google.com/tpu/docs/regions) [EID: 0x1bc8f9d790be9142]'} # pylint: disable=line-too-long
                    # Error code 9 means TPU resources is insufficient reserved
                    # capacity. Example:
                    # {'code': 9, 'message': 'Insufficient reserved capacity. Contact customer support to increase your reservation. [EID: 0x2f8bc266e74261a]'} # pylint: disable=line-too-long
                    self._blocked_resources.add(
                        launchable_resources.copy(zone=zone.name))
                elif code == 'RESOURCE_NOT_FOUND':
                    # https://github.com/skypilot-org/skypilot/issues/1797
                    # In the inner provision loop we have used retries to
                    # recover but failed. This indicates this zone is most
                    # likely out of capacity. The provision loop will terminate
                    # any potentially live VMs before moving onto the next
                    # zone.
                    self._blocked_resources.add(
                        launchable_resources.copy(zone=zone.name))
                else:
                    assert False, error
        elif len(httperror_str) >= 1:
            logger.info(f'Got {httperror_str[0]}')
            if ('Requested disk size cannot be smaller than the image size'
                    in httperror_str[0]):
                logger.info('Skipping all regions due to disk size issue.')
                self._blocked_resources.add(
                    launchable_resources.copy(region=None, zone=None))
            elif ('Policy update access denied.' in httperror_str[0] or
                  'IAM_PERMISSION_DENIED' in httperror_str[0]):
                logger.info(
                    'Skipping all regions due to service account not '
                    'having the required permissions and the user '
                    'account does not have enough permission to '
                    'update it. Please contact your administrator and '
                    'check out: https://skypilot.readthedocs.io/en/latest/cloud-setup/cloud-permissions.html#gcp\n'  # pylint: disable=line-too-long
                    f'Details: {httperror_str[0]}')
                self._blocked_resources.add(
                    launchable_resources.copy(region=None, zone=None))

            else:
                # Parse HttpError for unauthorized regions. Example:
                # googleapiclient.errors.HttpError: <HttpError 403 when requesting ... returned "Location us-east1-d is not found or access is unauthorized.". # pylint: disable=line-too-long
                # Details: "Location us-east1-d is not found or access is
                # unauthorized.">
                self._blocked_resources.add(
                    launchable_resources.copy(zone=zone.name))
        else:
            # No such structured error response found.
            assert not exception_list, stderr
            if 'was not found' in stderr:
                # Example: The resource
                # 'projects/<id>/zones/zone/acceleratorTypes/nvidia-tesla-v100'
                # was not found.
                logger.warning(f'Got \'resource not found\' in {zone.name}.')
                self._blocked_resources.add(
                    launchable_resources.copy(zone=zone.name))
            elif 'rsync: command not found' in stderr:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(_RSYNC_NOT_FOUND_MESSAGE)
            else:
                logger.info('====== stdout ======')
                for s in stdout.split('\n'):
                    print(s)
                logger.info('====== stderr ======')
                for s in splits:
                    print(s)

                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError('Errors occurred during provision; '
                                       'check logs above.')

    def _update_blocklist_on_aws_error(
            self, launchable_resources: 'resources_lib.Resources',
            region: 'clouds.Region', zones: Optional[List['clouds.Zone']],
            stdout: str, stderr: str):
        assert launchable_resources.is_launchable()
        assert zones is not None, 'AWS should always have zones.'

        style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            # 'An error occurred': boto3 errors
            # 'SKYPILOT_ERROR_NO_NODES_LAUNCHED': skypilot's changes to the AWS
            #    node provider; for errors prior to provisioning like VPC
            #    setup.
            if 'An error occurred' in s or
            'SKYPILOT_ERROR_NO_NODES_LAUNCHED: ' in s
        ]
        # Need to handle boto3 printing error but its retry succeeded:
        #   error occurred (Unsupported) .. not supported in your requested
        #   Availability Zone (us-west-2d)...retrying
        #   --> it automatically succeeded in another zone
        #   --> failed in [4/7] Running initialization commands due to user cmd
        # In this case, we should error out.
        head_node_up = any(
            line.startswith('<1/1> Setting up head node')
            for line in stdout_splits + stderr_splits)
        if not errors or head_node_up:
            if 'rsync: command not found' in stderr:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(_RSYNC_NOT_FOUND_MESSAGE)
            # TODO: Got transient 'Failed to create security group' that goes
            # away after a few minutes.  Should we auto retry other regions, or
            # let the user retry.
            logger.info('====== stdout ======')
            for s in stdout_splits:
                print(s)
            logger.info('====== stderr ======')
            for s in stderr_splits:
                print(s)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Errors occurred during provision; '
                                   'check logs above.')

        # Fill in the zones field in the Region.
        region_with_zones_list = clouds.AWS.regions_with_offering(
            launchable_resources.instance_type,
            launchable_resources.accelerators,
            launchable_resources.use_spot,
            region.name,
            zone=None)
        assert len(region_with_zones_list) == 1, region_with_zones_list
        region_with_zones = region_with_zones_list[0]
        assert region_with_zones.zones is not None, region_with_zones
        if set(zones) == set(region_with_zones.zones):
            # The underlying AWS NodeProvider will try all specified zones of a
            # region. (Each boto3 request takes one zone.)
            logger.warning(f'Got error(s) in all zones of {region.name}:')
        else:
            zones_str = ', '.join(z.name for z in zones)
            logger.warning(f'Got error(s) in {zones_str}:')
        messages = '\n\t'.join(errors)
        logger.warning(f'{style.DIM}\t{messages}{style.RESET_ALL}')
        for zone in zones:
            self._blocked_resources.add(
                launchable_resources.copy(zone=zone.name))

    def _update_blocklist_on_azure_error(
            self, launchable_resources: 'resources_lib.Resources',
            region: 'clouds.Region', zones: Optional[List['clouds.Zone']],
            stdout: str, stderr: str):
        del zones  # Unused.
        # The underlying ray autoscaler will try all zones of a region at once.
        style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            if ('Exception Details:' in s.strip() or 'InvalidTemplateDeployment'
                in s.strip() or '(ReadOnlyDisabledSubscription)' in s.strip())
        ]
        if not errors:
            if 'rsync: command not found' in stderr:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(_RSYNC_NOT_FOUND_MESSAGE)
            logger.info('====== stdout ======')
            for s in stdout_splits:
                print(s)
            logger.info('====== stderr ======')
            for s in stderr_splits:
                print(s)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Errors occurred during provision; '
                                   'check logs above.')

        logger.warning(f'Got error(s) in {region.name}:')
        messages = '\n\t'.join(errors)
        logger.warning(f'{style.DIM}\t{messages}{style.RESET_ALL}')
        if any('(ReadOnlyDisabledSubscription)' in s for s in errors):
            self._blocked_resources.add(
                resources_lib.Resources(cloud=clouds.Azure()))
        else:
            self._blocked_resources.add(launchable_resources.copy(zone=None))

    def _update_blocklist_on_lambda_error(
            self, launchable_resources: 'resources_lib.Resources',
            region: 'clouds.Region', zones: Optional[List['clouds.Zone']],
            stdout: str, stderr: str):
        del zones  # Unused.
        style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            if 'LambdaCloudError:' in s.strip()
        ]
        if not errors:
            if 'rsync: command not found' in stderr:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(_RSYNC_NOT_FOUND_MESSAGE)
            logger.info('====== stdout ======')
            for s in stdout_splits:
                print(s)
            logger.info('====== stderr ======')
            for s in stderr_splits:
                print(s)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Errors occurred during provision; '
                                   'check logs above.')

        logger.warning(f'Got error(s) in {region.name}:')
        messages = '\n\t'.join(errors)
        logger.warning(f'{style.DIM}\t{messages}{style.RESET_ALL}')
        self._blocked_resources.add(launchable_resources.copy(zone=None))

        # Sometimes, LambdaCloudError will list available regions.
        for e in errors:
            if e.find('Regions with capacity available:') != -1:
                for r in clouds.Lambda.regions():
                    if e.find(r.name) == -1:
                        self._blocked_resources.add(
                            launchable_resources.copy(region=r.name, zone=None))

    def _update_blocklist_on_scp_error(
            self, launchable_resources: 'resources_lib.Resources', region,
            zones, stdout, stderr):
        del zones  # Unused.
        style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            if 'SCPError:' in s.strip()
        ]
        if not errors:
            if 'rsync: command not found' in stderr:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(_RSYNC_NOT_FOUND_MESSAGE)
            logger.info('====== stdout ======')
            for s in stdout_splits:
                print(s)
            logger.info('====== stderr ======')
            for s in stderr_splits:
                print(s)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Errors occurred during provision; '
                                   'check logs above.')

        logger.warning(f'Got error(s) in {region.name}:')
        messages = '\n\t'.join(errors)
        logger.warning(f'{style.DIM}\t{messages}{style.RESET_ALL}')
        self._blocked_resources.add(launchable_resources.copy(zone=None))

        # Sometimes, SCPError will list available regions.
        for e in errors:
            if e.find('Regions with capacity available:') != -1:
                for r in clouds.SCP.regions():
                    if e.find(r.name) == -1:
                        self._blocked_resources.add(
                            launchable_resources.copy(region=r.name, zone=None))

    def _update_blocklist_on_ibm_error(
            self, launchable_resources: 'resources_lib.Resources',
            region: 'clouds.Region', zones: Optional[List['clouds.Zone']],
            stdout: str, stderr: str):

        style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            if 'ERR' in s.strip() or 'PANIC' in s.strip()
        ]
        if not errors:
            if 'rsync: command not found' in stderr:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(_RSYNC_NOT_FOUND_MESSAGE)
            logger.info('====== stdout ======')
            for s in stdout_splits:
                print(s)
            logger.info('====== stderr ======')
            for s in stderr_splits:
                print(s)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Errors occurred during provision; '
                                   'check logs above.')
        logger.warning(f'Got error(s) on IBM cluster, in {region.name}:')
        messages = '\n\t'.join(errors)
        logger.warning(f'{style.DIM}\t{messages}{style.RESET_ALL}')

        for zone in zones:  # type: ignore[union-attr]
            self._blocked_resources.add(
                launchable_resources.copy(zone=zone.name))

    def _update_blocklist_on_local_error(
            self, launchable_resources: 'resources_lib.Resources',
            region: 'clouds.Region', zones: Optional[List['clouds.Zone']],
            stdout: str, stderr: str):
        del zones  # Unused.
        style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            if 'ERR' in s.strip() or 'PANIC' in s.strip()
        ]
        if not errors:
            if 'rsync: command not found' in stderr:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(_RSYNC_NOT_FOUND_MESSAGE)
            logger.info('====== stdout ======')
            for s in stdout_splits:
                print(s)
            logger.info('====== stderr ======')
            for s in stderr_splits:
                print(s)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    'Errors occurred during launching of cluster services; '
                    'check logs above.')
        logger.warning('Got error(s) on local cluster:')
        messages = '\n\t'.join(errors)
        logger.warning(f'{style.DIM}\t{messages}{style.RESET_ALL}')
        self._blocked_resources.add(
            launchable_resources.copy(region=region.name, zone=None))

    # Apr, 2023 by Hysun(hysun.he@oracle.com): Added support for OCI
    def _update_blocklist_on_oci_error(
            self, launchable_resources: 'resources_lib.Resources',
            region: 'clouds.Region', zones: Optional[List['clouds.Zone']],
            stdout: str, stderr: str):

        style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            if ('VcnSubnetNotFound' in s.strip()) or
            ('oci.exceptions.ServiceError' in s.strip() and
             ('NotAuthorizedOrNotFound' in s.strip() or 'CannotParseRequest' in
              s.strip() or 'InternalError' in s.strip() or
              'LimitExceeded' in s.strip() or 'NotAuthenticated' in s.strip()))
        ]
        if not errors:
            if 'rsync: command not found' in stderr:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(_RSYNC_NOT_FOUND_MESSAGE)
            logger.info('====== stdout ======')
            for s in stdout_splits:
                print(s)
            logger.info('====== stderr ======')
            for s in stderr_splits:
                print(s)
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Errors occurred during provision; '
                                   'check logs above.')

        logger.warning(f'Got error(s) in {region.name}:')
        messages = '\n\t'.join(errors)
        logger.warning(f'{style.DIM}\t{messages}{style.RESET_ALL}')

        if zones is not None:
            for zone in zones:
                self._blocked_resources.add(
                    launchable_resources.copy(zone=zone.name))

    def _update_blocklist_on_error(
            self, launchable_resources: 'resources_lib.Resources',
            region: 'clouds.Region', zones: Optional[List['clouds.Zone']],
            stdout: Optional[str], stderr: Optional[str]) -> bool:
        """Handles cloud-specific errors and updates the block list.

        This parses textual stdout/stderr because we don't directly use the
        underlying clouds' SDKs.  If we did that, we could catch proper
        exceptions instead.

        Returns:
          definitely_no_nodes_launched: bool, True if definitely no nodes
            launched (e.g., due to VPC errors we have never sent the provision
            request), False otherwise.
        """
        assert launchable_resources.region == region.name, (
            launchable_resources, region)
        if stdout is None:
            # Gang scheduling failure (head node is definitely up, but some
            # workers' provisioning failed).  Simply block the zones.
            assert stderr is None, stderr
            if zones is not None:
                for zone in zones:
                    self._blocked_resources.add(
                        launchable_resources.copy(zone=zone.name))
            return False  # definitely_no_nodes_launched
        assert stdout is not None and stderr is not None, (stdout, stderr)

        # TODO(zongheng): refactor into Cloud interface?
        handlers = {
            clouds.AWS: self._update_blocklist_on_aws_error,
            clouds.Azure: self._update_blocklist_on_azure_error,
            clouds.GCP: self._update_blocklist_on_gcp_error,
            clouds.Lambda: self._update_blocklist_on_lambda_error,
            clouds.IBM: self._update_blocklist_on_ibm_error,
            clouds.SCP: self._update_blocklist_on_scp_error,
            clouds.Local: self._update_blocklist_on_local_error,
            clouds.OCI: self._update_blocklist_on_oci_error,
        }
        cloud = launchable_resources.cloud
        cloud_type = type(cloud)
        if cloud_type not in handlers:
            raise NotImplementedError(
                f'Cloud {cloud} unknown, or has not added '
                'support for parsing and handling provision failures.')
        handler = handlers[cloud_type]
        handler(launchable_resources, region, zones, stdout, stderr)

        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        # Determining whether head node launch *may* have been requested based
        # on outputs is tricky. We are conservative here by choosing an "early
        # enough" output line in the following:
        # https://github.com/ray-project/ray/blob/03b6bc7b5a305877501110ec04710a9c57011479/python/ray/autoscaler/_private/commands.py#L704-L737  # pylint: disable=line-too-long
        # This is okay, because we mainly want to use the return value of this
        # func to skip cleaning up never-launched clusters that encountered VPC
        # errors; their launch should not have printed any such outputs.
        head_node_launch_may_have_been_requested = any(
            'Acquiring an up-to-date head node' in line
            for line in stdout_splits + stderr_splits)
        # If head node request has definitely not been sent (this happens when
        # there are errors during node provider "bootstrapping", e.g.,
        # VPC-not-found errors), then definitely no nodes are launched.
        definitely_no_nodes_launched = (
            not head_node_launch_may_have_been_requested)

        return definitely_no_nodes_launched

    def _yield_zones(
        self, to_provision: resources_lib.Resources, num_nodes: int,
        cluster_name: str,
        prev_cluster_status: Optional[status_lib.ClusterStatus]
    ) -> Iterable[Optional[List[clouds.Zone]]]:
        """Yield zones within the given region to try for provisioning.

        Yields:
            Zones to try for provisioning within the given to_provision.region.
              - None means the cloud does not support zones, but the region does
                offer the requested resources (so the outer loop should issue a
                request to that region).
              - Non-empty list means the cloud supports zones, and the zones
                do offer the requested resources. If a list is yielded, it is
                guaranteed to be non-empty.
              - Nothing yielded means the region does not offer the requested
                resources.
        """
        assert (to_provision.cloud is not None and
                to_provision.region is not None and to_provision.instance_type
                is not None), (to_provision,
                               'cloud, region and instance_type must have been '
                               'set by optimizer')
        cloud = to_provision.cloud
        region = clouds.Region(to_provision.region)
        zones = None

        def _get_previously_launched_zones() -> Optional[List[clouds.Zone]]:
            # When the cluster exists, the to_provision should have been set
            # to the previous cluster's resources.
            zones = [
                clouds.Zone(name=to_provision.zone),
            ] if to_provision.zone is not None else None
            if zones is None:
                # Reuse the zone field in the ray yaml as the
                # prev_resources.zone field may not be set before the previous
                # cluster is launched.
                handle = global_user_state.get_handle_from_cluster_name(
                    cluster_name)
                assert isinstance(handle, CloudVmRayResourceHandle), (
                    'handle should be CloudVmRayResourceHandle (found: '
                    f'{type(handle)}) {cluster_name!r}')
                config = common_utils.read_yaml(handle.cluster_yaml)
                # This is for the case when the zone field is not set in the
                # launched resources in a previous launch (e.g., ctrl-c during
                # launch and multi-node cluster before PR #1700).
                zones_str = config.get('provider', {}).get('availability_zone')
                if zones_str is not None:
                    zones = [
                        clouds.Zone(name=zone) for zone in zones_str.split(',')
                    ]
            return zones

        if prev_cluster_status is not None:
            # If the cluster is previously launched, we should relaunch in the
            # same region and zone.
            zones = _get_previously_launched_zones()

            if prev_cluster_status != status_lib.ClusterStatus.UP:
                logger.info(
                    f'Cluster {cluster_name!r} (status: '
                    f'{prev_cluster_status.value}) was previously launched '
                    f'in {cloud} {region.name}. Relaunching in that region.')
            # TODO(zhwu): The cluster being killed by cloud provider should
            # be tested whether re-launching a cluster killed spot instance
            # will recover the data.
            yield zones

            # TODO(zhwu): update the following logics, since we have added
            # the support for refreshing the cluster status from the cloud
            # provider.
            # If it reaches here: the cluster status in the database gets
            # set to INIT, since a launch request was issued but failed.
            #
            # Cluster with status UP can reach here, if it was killed by the
            # cloud provider and no available resources in that region to
            # relaunch, which can happen to spot instance.
            if prev_cluster_status == status_lib.ClusterStatus.UP:
                message = (
                    f'Failed to connect to the cluster {cluster_name!r}. '
                    'It is possibly killed by cloud provider or manually '
                    'in the cloud provider console. To remove the cluster '
                    f'please run: sky down {cluster_name}')
                # Reset to UP (rather than keeping it at INIT), as INIT
                # mode will enable failover to other regions, causing
                # data lose.
                # TODO(zhwu): This is set to UP to be more conservative,
                # we may need to confirm whether the cluster is down in all
                # cases.
                global_user_state.set_cluster_status(
                    cluster_name, status_lib.ClusterStatus.UP)
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesUnavailableError(message,
                                                               no_failover=True)

            # Check the *previous* cluster status. If the cluster is previously
            # stopped, we should not retry other regions, since the previously
            # attached volumes are not visible on another region.
            elif prev_cluster_status == status_lib.ClusterStatus.STOPPED:
                message = (
                    'Failed to acquire resources to restart the stopped '
                    f'cluster {cluster_name} in {region.name}. Please retry '
                    'again later.')

                # Reset to STOPPED (rather than keeping it at INIT), because
                # (1) the cluster is not up (2) it ensures future `sky start`
                # will disable auto-failover too.
                global_user_state.set_cluster_status(
                    cluster_name, status_lib.ClusterStatus.STOPPED)

                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesUnavailableError(message,
                                                               no_failover=True)
            assert prev_cluster_status == status_lib.ClusterStatus.INIT
            message = (f'Failed to launch cluster {cluster_name!r} '
                       f'(previous status: {prev_cluster_status.value}) '
                       f'with the original resources: {to_provision}.')
            # We attempted re-launching a previously INIT cluster with the
            # same cloud/region/resources, but failed. Here no_failover=False,
            # so we will retry provisioning it with the current requested
            # resources in the outer loop.
            #
            # This condition can be triggered for previously INIT cluster by
            # (1) launch, after answering prompt immediately ctrl-c;
            # (2) launch again.
            # After (1), the cluster exists with INIT, and may or may not be
            # live.  And if it hits here, it's definitely not alive (because
            # step (2) failed).  Hence it's ok to retry with different
            # cloud/region and with current resources.
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(message)

        # If it reaches here, it means the cluster did not exist, as all the
        # cases when the cluster exists have been handled above (either the
        # provision succeeded in the caller and no need to retry, or this
        # function raised an ResourcesUnavailableError).
        for zones in cloud.zones_provision_loop(
                region=to_provision.region,
                num_nodes=num_nodes,
                instance_type=to_provision.instance_type,
                accelerators=to_provision.accelerators,
                use_spot=to_provision.use_spot,
        ):
            if zones is None:
                yield None
            else:
                assert zones, (
                    'Either None or a non-empty list of zones should '
                    'be yielded')
                # Only retry requested region/zones or all if not specified.
                zone_names = [zone.name for zone in zones]
                if not to_provision.valid_on_region_zones(
                        region.name, zone_names):
                    continue
                if to_provision.zone is not None:
                    zones = [clouds.Zone(name=to_provision.zone)]
                yield zones

    def _try_provision_tpu(self, to_provision: resources_lib.Resources,
                           config_dict: Dict[str, str]) -> bool:
        """Returns whether the provision is successful."""
        tpu_name = config_dict['tpu_name']
        assert 'tpu-create-script' in config_dict, \
            'Expect TPU provisioning with gcloud.'
        try:
            with log_utils.safe_rich_status('[bold cyan]Provisioning TPU '
                                            f'[green]{tpu_name}[/]'):
                subprocess_utils.run(f'bash {config_dict["tpu-create-script"]}',
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
            return True
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('ascii')
            if 'ALREADY_EXISTS' in stderr:
                # FIXME: should use 'start' on stopped TPUs, replacing
                # 'create'. Or it can be in a "deleting" state. Investigate the
                # right thing to do (force kill + re-provision?).
                logger.info(
                    f'  TPU {tpu_name} already exists; skipped creation.')
                return True

            if 'RESOURCE_EXHAUSTED' in stderr:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesUnavailableError(
                        f'TPU {tpu_name} creation failed due to quota '
                        'exhaustion. Please visit '
                        'https://console.cloud.google.com/iam-admin/quotas '
                        'for more information.')

            if 'PERMISSION_DENIED' in stderr:
                logger.info('  TPUs are not available in this zone.')
                return False

            if 'no more capacity in the zone' in stderr:
                logger.info('  No more capacity in this zone.')
                return False

            if 'CloudTpu received an invalid AcceleratorType' in stderr:
                # INVALID_ARGUMENT: CloudTpu received an invalid
                # AcceleratorType, "v3-8" for zone "us-central1-c". Valid
                # values are "v2-8, ".
                tpu_type = list(to_provision.accelerators.keys())[0]
                logger.info(
                    f'  TPU type {tpu_type} is not available in this zone.')
                return False

            logger.error(stderr)
            raise e

    def _retry_zones(
        self,
        to_provision: resources_lib.Resources,
        num_nodes: int,
        requested_resources: Set[resources_lib.Resources],
        dryrun: bool,
        stream_logs: bool,
        cluster_name: str,
        cloud_user_identity: Optional[List[str]],
        prev_cluster_status: Optional[status_lib.ClusterStatus],
    ):
        """The provision retry loop."""
        style = colorama.Style
        fore = colorama.Fore
        # Get log_path name
        log_path = os.path.join(self.log_dir, 'provision.log')
        log_abs_path = os.path.abspath(log_path)
        if not dryrun:
            os.makedirs(os.path.expanduser(self.log_dir), exist_ok=True)
            os.system(f'touch {log_path}')
        tail_cmd = f'tail -n100 -f {log_path}'
        logger.info('To view detailed progress: '
                    f'{style.BRIGHT}{tail_cmd}{style.RESET_ALL}')

        # Get previous cluster status
        cluster_exists = prev_cluster_status is not None
        is_prev_cluster_healthy = prev_cluster_status in [
            status_lib.ClusterStatus.STOPPED, status_lib.ClusterStatus.UP
        ]

        assert to_provision.region is not None, (
            to_provision, 'region should have been set by the optimizer.')
        region = clouds.Region(to_provision.region)

        # Optimization - check if user has non-zero quota for
        # the instance type in the target region. If not, fail early
        # instead of trying to provision and failing later.
        try:
            need_provision = to_provision.cloud.check_quota_available(
                to_provision)

        except Exception as e:  # pylint: disable=broad-except
            need_provision = True
            logger.info(f'Error occurred when trying to check quota. '
                        f'Proceeding assuming quotas are available. Error: '
                        f'{common_utils.format_exception(e, use_bracket=True)}')

        if not need_provision:
            # if quota is found to be zero, raise exception and skip to
            # the next region
            if to_provision.use_spot:
                instance_descriptor = 'spot'
            else:
                instance_descriptor = 'on-demand'
            raise exceptions.ResourcesUnavailableError(
                f'{colorama.Fore.YELLOW}Found no quota for '
                f'{to_provision.instance_type} {instance_descriptor} '
                f'instances in region {to_provision.region} '
                f'in {to_provision.cloud}. '
                f'{colorama.Style.RESET_ALL}'
                f'To request quotas, check the instruction: '
                f'https://skypilot.readthedocs.io/en/latest/cloud-setup/quota.html.'  # pylint: disable=line-too-long
            )

        for zones in self._yield_zones(to_provision, num_nodes, cluster_name,
                                       prev_cluster_status):
            # Filter out zones that are blocked, if any.
            # This optimize the provision loop by skipping zones that are
            # indicated to be unavailable from previous provision attempts.
            # It can happen for the provisioning on GCP, as the
            # yield_region_zones will return zones from a region one by one,
            # but the optimizer that does the filtering will not be involved
            # until the next region.
            if zones is not None:
                remaining_unblocked_zones = copy.deepcopy(zones)
                for zone in zones:
                    for blocked_resources in self._blocked_resources:
                        if to_provision.copy(
                                region=region.name,
                                zone=zone.name).should_be_blocked_by(
                                    blocked_resources):
                            remaining_unblocked_zones.remove(zone)
                            break
                if not remaining_unblocked_zones:
                    # Skip the region if all zones are blocked.
                    continue
                zones = remaining_unblocked_zones

            if zones is None:
                # For clouds that don't have a zone concept or cloud
                # provisioners that do not support zone-based provisioning
                # (e.g., Azure, Lambda).
                zone_str = ''
            else:
                zone_str = ','.join(z.name for z in zones)
                zone_str = f' ({zone_str})'
            try:
                config_dict = backend_utils.write_cluster_config(
                    to_provision,
                    num_nodes,
                    _get_cluster_config_template(to_provision.cloud),
                    cluster_name,
                    self._local_wheel_path,
                    self._wheel_hash,
                    region=region,
                    zones=zones,
                    dryrun=dryrun,
                    keep_launch_fields_in_existing_config=cluster_exists)
            except exceptions.ResourcesUnavailableError as e:
                # Failed due to catalog issue, e.g. image not found.
                logger.info(
                    f'Failed to find catalog in region {region.name}: {e}')
                continue
            if dryrun:
                return config_dict
            cluster_config_file = config_dict['ray']

            # Record early, so if anything goes wrong, 'sky status' will show
            # the cluster name and users can appropriately 'sky down'.  It also
            # means a second 'sky launch -c <name>' will attempt to reuse.
            handle = CloudVmRayResourceHandle(
                cluster_name=cluster_name,
                cluster_yaml=cluster_config_file,
                launched_nodes=num_nodes,
                # OK for this to be shown in CLI as status == INIT.
                launched_resources=to_provision.copy(region=region.name),
                tpu_create_script=config_dict.get('tpu-create-script'),
                tpu_delete_script=config_dict.get('tpu-delete-script'))
            usage_lib.messages.usage.update_final_cluster_status(
                status_lib.ClusterStatus.INIT)

            # This sets the status to INIT (even for a normal, UP cluster).
            global_user_state.add_or_update_cluster(
                cluster_name,
                cluster_handle=handle,
                requested_resources=requested_resources,
                ready=False,
            )

            global_user_state.set_owner_identity_for_cluster(
                cluster_name, cloud_user_identity)

            tpu_name = config_dict.get('tpu_name')
            if tpu_name is not None:
                logger.info(
                    f'{colorama.Style.BRIGHT}Provisioning TPU on '
                    f'{to_provision.cloud} '
                    f'{region.name}{colorama.Style.RESET_ALL}{zone_str}')

                success = self._try_provision_tpu(to_provision, config_dict)
                if not success:
                    continue

            logging_info = {
                'cluster_name': cluster_name,
                'region_name': region.name,
                'zone_str': zone_str,
            }
            status, stdout, stderr, head_ip = self._gang_schedule_ray_up(
                to_provision.cloud, cluster_config_file, handle, log_abs_path,
                stream_logs, logging_info, to_provision.use_spot)

            if status == self.GangSchedulingStatus.CLUSTER_READY:
                if cluster_exists:
                    # Guard against the case where there's an existing cluster
                    # with ray runtime messed up (e.g., manually killed) by (1)
                    # querying ray status (2) restarting ray if needed.
                    #
                    # The above 'ray up' will not restart it automatically due
                    # to 'ray up # --no-restart' flag.
                    #
                    # NOTE: this is performance sensitive and has been observed
                    # to take 9s. Only do this for existing clusters, not
                    # freshly launched ones (which should have ray runtime
                    # started).
                    self._ensure_cluster_ray_started(handle, log_abs_path)

                cluster_name = config_dict['cluster_name']
                config_dict['launched_resources'] = to_provision.copy(
                    region=region.name)
                config_dict['launched_nodes'] = num_nodes
                # Optimizations: head_ip and zones may or may not be None. In
                # the latter case, the caller doesn't need to query them again.
                config_dict['head_ip'] = head_ip
                config_dict['zones'] = zones
                plural = '' if num_nodes == 1 else 's'
                if not isinstance(to_provision.cloud, clouds.Local):
                    logger.info(f'{fore.GREEN}Successfully provisioned or found'
                                f' existing VM{plural}.{style.RESET_ALL}')
                return config_dict

            # The cluster is not ready. We must perform error recording and/or
            # cleanup.

            # If cluster was previously UP or STOPPED, stop it; otherwise
            # terminate.
            # FIXME(zongheng): terminating a potentially live cluster is
            # scary. Say: users have an existing cluster that got into INIT, do
            # sky launch, somehow failed, then we may be terminating it here.
            terminate_or_stop = not is_prev_cluster_healthy
            definitely_no_nodes_launched = False
            if status == self.GangSchedulingStatus.HEAD_FAILED:
                # ray up failed for the head node.
                definitely_no_nodes_launched = self._update_blocklist_on_error(
                    to_provision, region, zones, stdout, stderr)
            else:
                # gang scheduling failed.
                assert status == self.GangSchedulingStatus.GANG_FAILED, status
                # The stdout/stderr of ray up is not useful here, since
                # head node is successfully provisioned.
                definitely_no_nodes_launched = self._update_blocklist_on_error(
                    to_provision, region, zones=zones, stdout=None, stderr=None)
                # GANG_FAILED means head is up, workers failed.
                assert definitely_no_nodes_launched is False, (
                    definitely_no_nodes_launched)

                # Only log the errors for GANG_FAILED, since HEAD_FAILED may
                # not have created any resources (it can happen however) and
                # HEAD_FAILED can happen in "normal" failover cases.
                logger.error('*** Failed provisioning the cluster. ***')
                terminate_str = ('Terminating'
                                 if terminate_or_stop else 'Stopping')
                logger.error(f'*** {terminate_str} the failed cluster. ***')

            # If these conditions hold, it *should* be safe to skip the cleanup
            # action. This is a UX optimization.
            #
            # We want to skip mainly for VPC/subnets errors thrown during node
            # provider bootstrapping: if users encountered "No VPC with name
            # 'xxx' is found in <region>.", then going ahead to down the
            # non-existent cluster will itself print out a (caught, harmless)
            # error with the same message.  This was found to be
            # confusing. Thus we skip termination.
            skip_cleanup = not cluster_exists and definitely_no_nodes_launched
            if skip_cleanup:
                continue

            # There may exist partial nodes (e.g., head node) so we must
            # terminate or stop before moving on to other regions.
            #
            # NOTE: even HEAD_FAILED could've left a live head node there,
            # so we must terminate/stop here too. E.g., node is up, and ray
            # autoscaler proceeds to setup commands, which may fail:
            #   ERR updater.py:138 -- New status: update-failed
            CloudVmRayBackend().teardown_no_lock(handle,
                                                 terminate=terminate_or_stop)

        if to_provision.zone is not None:
            message = (
                f'Failed to acquire resources in {to_provision.zone}. '
                'Try changing resource requirements or use another zone.')
        elif to_provision.region is not None:
            # For public clouds, provision.region is always set.
            message = ('Failed to acquire resources in all zones in '
                       f'{to_provision.region}. Try changing resource '
                       'requirements or use another region.')
        else:
            message = (f'Failed to acquire resources in {to_provision.cloud}. '
                       'Try changing resource requirements or use another '
                       'cloud provider.')
        # Do not failover to other clouds if the cluster was previously
        # UP or STOPPED, since the user can have some data on the cluster.
        raise exceptions.ResourcesUnavailableError(
            message, no_failover=is_prev_cluster_healthy)

    def _tpu_pod_setup(self, cluster_yaml: str,
                       cluster_handle: 'backends.CloudVmRayResourceHandle'):
        """Completes setup and start Ray cluster on TPU VM Pod nodes.

        This is a workaround for Ray Autoscaler where `ray up` does not
        run setup or launch ray cluster on TPU VM Pod nodes.
        """
        ssh_credentials = backend_utils.ssh_credential_from_yaml(cluster_yaml)
        all_ips = cluster_handle.external_ips(use_cached_ips=False)
        num_tpu_devices = tpu_utils.get_num_tpu_devices(
            cluster_handle.launched_resources)
        if all_ips is None or len(all_ips) != num_tpu_devices:
            raise RuntimeError(
                f'Nodes IPs: {all_ips} does not'
                f'match number of TPU devices: {num_tpu_devices}.')

        # Get the private IP of head node for connecting Ray cluster.
        head_runner = command_runner.SSHCommandRunner(all_ips[0],
                                                      **ssh_credentials)
        cmd_str = 'python3 -c \"import ray; print(ray._private.services.get_node_ip_address())\"'  # pylint: disable=line-too-long
        rc, stdout, stderr = head_runner.run(cmd_str,
                                             require_outputs=True,
                                             stream_logs=False)
        subprocess_utils.handle_returncode(
            rc,
            cmd_str,
            'Failed to get private IP from head node.',
            stderr=stdout + stderr)
        head_ip_private = stdout.strip()

        ray_config = common_utils.read_yaml(cluster_yaml)
        worker_start_ray_commands = [f'echo "export RAY_HEAD_IP={head_ip_private}" >> ~/.bashrc && source ~/.bashrc']  # pylint: disable=line-too-long
        worker_start_ray_commands += ray_config['worker_start_ray_commands']

        # Setup TPU VM Pod workers and launch Ray cluster.
        onprem_utils.do_filemounts_and_setup_on_local_workers(
            cluster_yaml,
            worker_ips=all_ips[1:],
            extra_setup_cmds=worker_start_ray_commands)

    @timeline.event
    def _gang_schedule_ray_up(
            self, to_provision_cloud: clouds.Cloud, cluster_config_file: str,
            cluster_handle: 'backends.CloudVmRayResourceHandle',
            log_abs_path: str, stream_logs: bool, logging_info: dict,
            use_spot: bool
    ) -> Tuple[GangSchedulingStatus, str, str, Optional[str]]:
        """Provisions a cluster via 'ray up' and wait until fully provisioned.

        Returns:
          (GangSchedulingStatus; stdout; stderr; optional head_ip).
        """
        # FIXME(zhwu,zongheng): ray up on multiple nodes ups the head node then
        # waits for all workers; turn it into real gang scheduling.
        # FIXME: refactor code path to remove use of stream_logs
        del stream_logs

        def ray_up():
            # Runs `ray up <kwargs>` with our monkey-patched launch hash
            # calculation. See the monkey patch file for why.
            #
            # NOTE: --no-restart solves the following bug.  Without it, if 'ray
            # up' (sky launch) twice on a cluster with >1 node, the worker node
            # gets disconnected/killed by ray autoscaler; the whole task will
            # just freeze.  (Doesn't affect 1-node clusters.)  With this flag,
            # ray processes no longer restart and this bug doesn't show.
            # Downside is existing tasks on the cluster will keep running
            # (which may be ok with the semantics of 'sky launch' twice).
            # Tracked in https://github.com/ray-project/ray/issues/20402.
            # Ref: https://github.com/ray-project/ray/blob/releases/2.4.0/python/ray/autoscaler/sdk/sdk.py#L16-L49  # pylint: disable=line-too-long
            script_path = write_ray_up_script_with_patched_launch_hash_fn(
                cluster_config_file, ray_up_kwargs={'no_restart': True})

            # Redirect stdout/err to the file and streaming (if stream_logs).
            # With stdout/err redirected, 'ray up' will have no color and
            # different order from directly running in the console. The
            # `--log-style` and `--log-color` flags do not work. To reproduce,
            # `ray up --log-style pretty --log-color true | tee tmp.out`.
            returncode, stdout, stderr = log_lib.run_with_log(
                [sys.executable, script_path],
                log_abs_path,
                stream_logs=False,
                start_streaming_at='Shared connection to',
                line_processor=log_utils.RayUpLineProcessor(),
                # Reduce BOTO_MAX_RETRIES from 12 to 5 to avoid long hanging
                # time during 'ray up' if insufficient capacity occurs.
                env=dict(
                    os.environ,
                    BOTO_MAX_RETRIES='5',
                    # Use environment variables to disable the ray usage collection
                    # (to avoid overheads and potential issues with the usage)
                    # as sdk does not take the argument for disabling the usage
                    # collection.
                    RAY_USAGE_STATS_ENABLED='0'),
                require_outputs=True,
                # Disable stdin to avoid ray outputs mess up the terminal with
                # misaligned output when multithreading/multiprocessing are used
                # Refer to: https://github.com/ray-project/ray/blob/d462172be7c5779abf37609aed08af112a533e1e/python/ray/autoscaler/_private/subprocess_output_util.py#L264  # pylint: disable=line-too-long
                stdin=subprocess.DEVNULL)
            return returncode, stdout, stderr

        region_name = logging_info['region_name']
        zone_str = logging_info['zone_str']
        style = colorama.Style
        if isinstance(to_provision_cloud, clouds.Local):
            cluster_name = logging_info['cluster_name']
            logger.info(f'{style.BRIGHT}Launching on local cluster '
                        f'{cluster_name!r}.')
        else:
            logger.info(f'{style.BRIGHT}Launching on {to_provision_cloud} '
                        f'{region_name}{style.RESET_ALL}{zone_str}')
        start = time.time()

        # Edge case: /tmp/ray does not exist, so autoscaler can't create/store
        # cluster lock and cluster state.
        os.makedirs('/tmp/ray', exist_ok=True)

        # Launch the cluster with ray up

        # Retry if the any of the following happens:
        # 1. Failed due to timeout when fetching head node for Azure.
        # 2. Failed due to file mounts, because it is probably has too
        # many ssh connections and can be fixed by retrying.
        # This is required when using custom image for GCP.
        def need_ray_up(
                ray_up_return_value: Optional[Tuple[int, str, str]]) -> bool:

            # Indicates the first ray up.
            if ray_up_return_value is None:
                return True

            returncode, stdout, stderr = ray_up_return_value
            if returncode == 0:
                return False

            if isinstance(to_provision_cloud, clouds.Azure):
                if 'Failed to invoke the Azure CLI' in stderr:
                    logger.info(
                        'Retrying head node provisioning due to Azure CLI '
                        'issues.')
                    return True
                if ('Head node fetch timed out. Failed to create head node.'
                        in stderr):
                    logger.info(
                        'Retrying head node provisioning due to head fetching '
                        'timeout.')
                    return True

            if isinstance(to_provision_cloud, clouds.GCP):
                if ('Quota exceeded for quota metric \'List requests\' and '
                        'limit \'List requests per minute\'' in stderr):
                    logger.info(
                        'Retrying due to list request rate limit exceeded.')
                    return True

                # https://github.com/skypilot-org/skypilot/issues/1797
                # "The resource 'projects/xxx/zones/us-central1-b/instances/ray-yyy-head-<hash>-compute' was not found" # pylint: disable=line-too-long
                pattern = (r'\'code\': \'RESOURCE_NOT_FOUND\'.*The resource'
                           r'.*instances\/.*-compute\' was not found')
                result = re.search(pattern, stderr)
                if result is not None:
                    # Retry. Unlikely will succeed if it's due to no capacity.
                    logger.info(
                        'Retrying due to the possibly flaky RESOURCE_NOT_FOUND '
                        'error.')
                    return True

            if 'rsync: command not found' in stderr:
                logger.info('Skipping retry due to `rsync` not found in '
                            'the specified image.')
                return False

            if ('Processing file mounts' in stdout and
                    'Running setup commands' not in stdout and
                    'Failed to setup head node.' in stderr):
                logger.info(
                    'Retrying runtime setup due to ssh connection issue.')
                return True

            if ('ConnectionResetError: [Errno 54] Connection reset by peer'
                    in stderr):
                logger.info('Retrying due to Connection reset by peer.')
                return True
            return False

        retry_cnt = 0
        ray_up_return_value = None
        # 5 seconds to 180 seconds. We need backoff for e.g., rate limit per
        # minute errors.
        backoff = common_utils.Backoff(initial_backoff=5,
                                       max_backoff_factor=180 // 5)
        while (retry_cnt < _MAX_RAY_UP_RETRY and
               need_ray_up(ray_up_return_value)):
            retry_cnt += 1
            if retry_cnt > 1:
                sleep = backoff.current_backoff()
                logger.info(
                    'Retrying launching in {:.1f} seconds.'.format(sleep))
                time.sleep(sleep)
            ray_up_return_value = ray_up()

        assert ray_up_return_value is not None
        returncode, stdout, stderr = ray_up_return_value

        logger.debug(f'`ray up` takes {time.time() - start:.1f} seconds with '
                     f'{retry_cnt} retries.')
        if returncode != 0:
            return self.GangSchedulingStatus.HEAD_FAILED, stdout, stderr, None

        resources = cluster_handle.launched_resources
        if tpu_utils.is_tpu_vm_pod(resources):
            logger.info(f'{style.BRIGHT}Setting up TPU VM Pod workers...'
                        f'{style.RESET_ALL}')
            self._tpu_pod_setup(cluster_config_file, cluster_handle)

        # Only 1 node or head node provisioning failure.
        if cluster_handle.launched_nodes == 1 and returncode == 0:
            # Optimization: Try parse head ip from 'ray up' stdout.
            # Last line looks like: 'ssh ... <user>@<public head_ip>\n'
            position = stdout.rfind('@')
            # Use a regex to extract the IP address.
            ip_list = re.findall(backend_utils.IP_ADDR_REGEX,
                                 stdout[position + 1:])
            # If something's wrong. Ok to not return a head_ip.
            head_ip = None
            if len(ip_list) == 1:
                head_ip = ip_list[0]
            return (self.GangSchedulingStatus.CLUSTER_READY, stdout, stderr,
                    head_ip)

        # All code below is handling num_nodes > 1.

        provision_str = 'Successfully provisioned or found existing head VM.'
        if isinstance(to_provision_cloud, clouds.Local):
            provision_str = 'Successfully connected to head node.'

        logger.info(f'{style.BRIGHT}{provision_str} '
                    f'Waiting for workers.{style.RESET_ALL}')

        # Special handling is needed for the local case. This is due to a Ray
        # autoscaler bug, where filemounting and setup does not run on worker
        # nodes. Hence, this method here replicates what the Ray autoscaler
        # would do were it for public cloud.
        if isinstance(to_provision_cloud, clouds.Local):
            onprem_utils.do_filemounts_and_setup_on_local_workers(
                cluster_config_file)

        # FIXME(zongheng): the below requires ray processes are up on head. To
        # repro it failing: launch a 2-node cluster, log into head and ray
        # stop, then launch again.
        cluster_ready = backend_utils.wait_until_ray_cluster_ready(
            cluster_config_file,
            cluster_handle.launched_nodes,
            log_path=log_abs_path,
            nodes_launching_progress_timeout=_NODES_LAUNCHING_PROGRESS_TIMEOUT[
                type(to_provision_cloud)],
            is_local_cloud=isinstance(to_provision_cloud, clouds.Local))
        if cluster_ready:
            cluster_status = self.GangSchedulingStatus.CLUSTER_READY
            # ray up --no-restart again with upscaling_speed=0 after cluster is
            # ready to ensure cluster will not scale up after preemption (spot).
            # Skip for non-spot as this takes extra time to provision (~1min).
            if use_spot:
                ray_config = common_utils.read_yaml(cluster_config_file)
                ray_config['upscaling_speed'] = 0
                common_utils.dump_yaml(cluster_config_file, ray_config)
                start = time.time()
                returncode, stdout, stderr = ray_up()
                logger.debug(
                    f'Upscaling reset takes {time.time() - start} seconds.')
                if returncode != 0:
                    return (self.GangSchedulingStatus.GANG_FAILED, stdout,
                            stderr, None)
        else:
            cluster_status = self.GangSchedulingStatus.GANG_FAILED

        # Do not need stdout/stderr if gang scheduling failed.
        # gang_succeeded = False, if head OK, but workers failed.
        return cluster_status, '', '', None

    def _ensure_cluster_ray_started(self, handle: 'CloudVmRayResourceHandle',
                                    log_abs_path) -> None:
        """Ensures ray processes are up on a just-provisioned cluster."""
        if handle.launched_nodes > 1:
            # FIXME(zongheng): this has NOT been tested with multinode
            # clusters; mainly because this function will not be reached in
            # that case.  See #140 for details.  If it were reached, the
            # following logic might work:
            #   - get all node ips
            #   - for all nodes: ray stop
            #   - ray up --restart-only
            return
        backend = CloudVmRayBackend()

        returncode = backend.run_on_head(
            handle, backend_utils.RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND)
        if returncode == 0:
            return
        launched_resources = handle.launched_resources
        # Ray cluster should already be running if the system admin has setup
        # Ray.
        if isinstance(launched_resources.cloud, clouds.Local):
            raise RuntimeError(
                'The command `ray status` errored out on the head node '
                'of the local cluster. Check if ray[default]==2.4.0 '
                'is installed or running correctly.')
        backend.run_on_head(handle, 'ray stop')

        # Runs `ray up <kwargs>` with our monkey-patched launch hash
        # calculation. See the monkey patch file for why.
        script_path = write_ray_up_script_with_patched_launch_hash_fn(
            handle.cluster_yaml, ray_up_kwargs={'restart_only': True})
        log_lib.run_with_log(
            [sys.executable, script_path],
            log_abs_path,
            stream_logs=False,
            # Use environment variables to disable the ray usage collection
            # (to avoid overheads and potential issues with the usage)
            # as sdk does not take the argument for disabling the usage
            # collection.
            env=dict(os.environ, RAY_USAGE_STATS_ENABLED='0'),
            # Disable stdin to avoid ray outputs mess up the terminal with
            # misaligned output when multithreading/multiprocessing is used.
            # Refer to: https://github.com/ray-project/ray/blob/d462172be7c5779abf37609aed08af112a533e1e/python/ray/autoscaler/_private/subprocess_output_util.py#L264 # pylint: disable=line-too-long
            stdin=subprocess.DEVNULL)

    @timeline.event
    def provision_with_retries(
        self,
        task: task_lib.Task,
        to_provision_config: ToProvisionConfig,
        dryrun: bool,
        stream_logs: bool,
    ):
        """Provision with retries for all launchable resources."""
        cluster_name = to_provision_config.cluster_name
        to_provision = to_provision_config.resources
        num_nodes = to_provision_config.num_nodes
        prev_cluster_status = to_provision_config.prev_cluster_status
        launchable_retries_disabled = (self._dag is None or
                                       self._optimize_target is None)

        failover_history: List[Exception] = list()

        style = colorama.Style
        # Retrying launchable resources.
        while True:
            try:
                # Recheck cluster name as the 'except:' block below may
                # change the cloud assignment.
                to_provision.cloud.check_cluster_name_is_valid(cluster_name)
                if dryrun:
                    cloud_user = None
                else:
                    cloud_user = to_provision.cloud.get_current_user_identity()
                # Skip if to_provision.cloud does not support requested features
                to_provision.cloud.check_features_are_supported(
                    self._requested_features)

                config_dict = self._retry_zones(
                    to_provision,
                    num_nodes,
                    requested_resources=task.resources,
                    dryrun=dryrun,
                    stream_logs=stream_logs,
                    cluster_name=cluster_name,
                    cloud_user_identity=cloud_user,
                    prev_cluster_status=prev_cluster_status)
                if dryrun:
                    return
            except (exceptions.InvalidClusterNameError,
                    exceptions.NotSupportedError,
                    exceptions.CloudUserIdentityError) as e:
                # InvalidClusterNameError: cluster name is invalid,
                # NotSupportedError: cloud does not support requested features,
                # CloudUserIdentityError: cloud user identity is invalid.
                # The exceptions above should be applicable to the whole
                # cloud, so we do add the cloud to the blocked resources.
                logger.warning(common_utils.format_exception(e))
                self._blocked_resources.add(
                    resources_lib.Resources(cloud=to_provision.cloud))
                failover_history.append(e)
            except exceptions.ResourcesUnavailableError as e:
                failover_history.append(e)
                if e.no_failover:
                    raise e.with_failover_history(failover_history)
                if launchable_retries_disabled:
                    logger.warning(
                        'DAG and optimize_target needs to be registered first '
                        'to enable cross-cloud retry. '
                        'To fix, call backend.register_info(dag=dag, '
                        'optimize_target=sky.OptimizeTarget.COST)')
                    raise e.with_failover_history(failover_history)

                logger.warning(common_utils.format_exception(e))
            else:
                # Provisioning succeeded.
                break

            if to_provision.zone is None:
                region_or_zone_str = str(to_provision.region)
            else:
                region_or_zone_str = str(to_provision.zone)
            logger.warning(f'\n{style.BRIGHT}Provision failed for {num_nodes}x '
                           f'{to_provision} in {region_or_zone_str}. '
                           f'Trying other locations (if any).{style.RESET_ALL}')
            if prev_cluster_status is None:
                # Add failed resources to the blocklist, only when it
                # is in fallback mode.
                self._blocked_resources.add(to_provision)
            else:
                # If we reach here, it means that the existing cluster must have
                # a previous status of INIT, because other statuses (UP,
                # STOPPED) will not trigger the failover due to `no_failover`
                # flag; see _yield_zones(). Also, the cluster should have been
                # terminated by _retry_zones().
                assert (prev_cluster_status == status_lib.ClusterStatus.INIT
                       ), prev_cluster_status
                assert global_user_state.get_handle_from_cluster_name(
                    cluster_name) is None, cluster_name
                logger.info('Retrying provisioning with requested resources '
                            f'{task.num_nodes}x {task.resources}')
                # Retry with the current, potentially "smaller" resources:
                # to_provision == the current new resources (e.g., V100:1),
                # which may be "smaller" than the original (V100:8).
                # num_nodes is not part of a Resources so must be updated
                # separately.
                num_nodes = task.num_nodes
                prev_cluster_status = None

            # Set to None so that sky.optimize() will assign a new one
            # (otherwise will skip re-optimizing this task).
            # TODO: set all remaining tasks' best_resources to None.
            task.best_resources = None
            try:
                self._dag = sky.optimize(
                    self._dag,
                    minimize=self._optimize_target,
                    blocked_resources=self._blocked_resources)
            except exceptions.ResourcesUnavailableError as e:
                # Optimizer failed to find a feasible resources for the task,
                # either because the previous failovers have blocked all the
                # possible resources or the requested resources is too
                # restrictive. If we reach here, our failover logic finally
                # ends here.
                raise e.with_failover_history(failover_history)
            to_provision = task.best_resources
            assert task in self._dag.tasks, 'Internal logic error.'
            assert to_provision is not None, task
        return config_dict


class CloudVmRayResourceHandle(backends.backend.ResourceHandle):
    """A pickle-able tuple of:

    - (required) Cluster name.
    - (required) Path to a cluster.yaml file.
    - (optional) A cached head node public IP.  Filled in after a
        successful provision().
    - (optional) A cached stable list of (internal IP, external IP) tuples
        for all nodes in a cluster. Filled in after successful task execution.
    - (optional) Launched num nodes
    - (optional) Launched resources
    - (optional) If TPU(s) are managed, a path to a deletion script.
    """
    _VERSION = 3

    def __init__(self,
                 *,
                 cluster_name: str,
                 cluster_yaml: str,
                 launched_nodes: int,
                 launched_resources: resources_lib.Resources,
                 stable_internal_external_ips: Optional[List[Tuple[
                     str, str]]] = None,
                 tpu_create_script: Optional[str] = None,
                 tpu_delete_script: Optional[str] = None) -> None:
        self._version = self._VERSION
        self.cluster_name = cluster_name
        self._cluster_yaml = cluster_yaml.replace(os.path.expanduser('~'), '~',
                                                  1)
        # List of (internal_ip, external_ip) tuples for all the nodes
        # in the cluster, sorted by the external ips.
        self.stable_internal_external_ips = stable_internal_external_ips
        self.launched_nodes = launched_nodes
        self.launched_resources = launched_resources
        self.tpu_create_script = tpu_create_script
        self.tpu_delete_script = tpu_delete_script
        self._maybe_make_local_handle()

    def __repr__(self):
        return (f'ResourceHandle('
                f'\n\tcluster_name={self.cluster_name},'
                f'\n\thead_ip={self.head_ip},'
                '\n\tstable_internal_external_ips='
                f'{self.stable_internal_external_ips},'
                '\n\tcluster_yaml='
                f'{self.cluster_yaml}, '
                f'\n\tlaunched_resources={self.launched_nodes}x '
                f'{self.launched_resources}, '
                f'\n\ttpu_create_script={self.tpu_create_script}, '
                f'\n\ttpu_delete_script={self.tpu_delete_script})')

    def get_cluster_name(self):
        return self.cluster_name

    def _maybe_make_local_handle(self):
        """Adds local handle for the local cloud case.

        For public cloud, the first time sky launch is ran, task resources
        = cluster resources. For the local cloud case, sky launch is ran,
        task resources != cluster resources; hence, this method is needed
        to correct this.
        """
        self.local_handle = None
        local_file = os.path.expanduser(
            onprem_utils.SKY_USER_LOCAL_CONFIG_PATH.format(self.cluster_name))
        # Local cluster case requires several modifications:
        #   1) Create local_handle to store local cluster IPs and
        #      custom accelerators for each node.
        #   2) Replace launched_resources to represent a generic local
        #      node (without accelerator specifications).
        #   3) Replace launched_nodes to represent the total nodes in the
        #      local cluster.
        if os.path.isfile(local_file):
            config = onprem_utils.get_local_cluster_config_or_error(
                self.cluster_name)
            self.local_handle = {}
            cluster_config = config['cluster']
            auth_config = config['auth']
            ips = cluster_config['ips']
            local_region = clouds.Local.regions()[0].name
            # Convert existing ResourceHandle fields to specify local
            # cluster resources.
            self.launched_resources = resources_lib.Resources(
                cloud=clouds.Local(), region=local_region)
            self.launched_nodes = len(ips)
            self.local_handle['ips'] = ips
            cluster_accs = onprem_utils.get_local_cluster_accelerators(
                ips, auth_config)
            self.local_handle['cluster_resources'] = [
                resources_lib.Resources(
                    cloud=clouds.Local(),
                    accelerators=acc_dict if acc_dict else None,
                    region=local_region) for acc_dict in cluster_accs
            ]

    def _update_cluster_region(self):
        if self.launched_resources.region is not None:
            return

        config = common_utils.read_yaml(self.cluster_yaml)
        provider = config['provider']
        cloud = self.launched_resources.cloud
        if cloud.is_same_cloud(clouds.Azure()):
            region = provider['location']
        elif cloud.is_same_cloud(clouds.GCP()) or cloud.is_same_cloud(
                clouds.AWS()):
            region = provider['region']
        elif cloud.is_same_cloud(clouds.Local()):
            # There is only 1 region for Local cluster, 'Local'.
            local_regions = clouds.Local.regions()
            region = local_regions[0].name

        self.launched_resources = self.launched_resources.copy(region=region)

    def _update_stable_cluster_ips(self, max_attempts: int = 1) -> None:
        cluster_external_ips = backend_utils.get_node_ips(
            self.cluster_yaml,
            self.launched_nodes,
            handle=self,
            head_ip_max_attempts=max_attempts,
            worker_ip_max_attempts=max_attempts,
            get_internal_ips=False)

        if self.external_ips() == cluster_external_ips:
            # Optimization: If the cached external IPs are the same as the
            # retrieved external IPs, then we can skip retrieving internal
            # IPs since the cached IPs are up-to-date.
            return

        is_cluster_aws = (self.launched_resources is not None and
                          isinstance(self.launched_resources.cloud, clouds.AWS))
        if is_cluster_aws and skypilot_config.get_nested(
                keys=('aws', 'use_internal_ips'), default_value=False):
            # Optimization: if we know use_internal_ips is True (currently
            # only exposed for AWS), then our AWS NodeProvider is
            # guaranteed to pick subnets that will not assign public IPs,
            # thus the first list of IPs returned above are already private
            # IPs. So skip the second query.
            cluster_internal_ips = list(cluster_external_ips)
        else:
            cluster_internal_ips = backend_utils.get_node_ips(
                self.cluster_yaml,
                self.launched_nodes,
                handle=self,
                head_ip_max_attempts=max_attempts,
                worker_ip_max_attempts=max_attempts,
                get_internal_ips=True)

        assert len(cluster_external_ips) == len(cluster_internal_ips), (
            f'Cluster {self.cluster_name!r}:'
            f'Expected same number of internal IPs {cluster_internal_ips}'
            f' and external IPs {cluster_external_ips}.')

        internal_external_ips = list(
            zip(cluster_internal_ips, cluster_external_ips))

        # Ensure head node is the first element, then sort based on the
        # external IPs for stableness
        stable_internal_external_ips = [internal_external_ips[0]] + sorted(
            internal_external_ips[1:], key=lambda x: x[1])
        self.stable_internal_external_ips = stable_internal_external_ips

    def internal_ips(self,
                     max_attempts: int = _FETCH_IP_MAX_ATTEMPTS,
                     use_cached_ips: bool = True) -> Optional[List[str]]:
        if not use_cached_ips:
            self._update_stable_cluster_ips(max_attempts=max_attempts)
        if self.stable_internal_external_ips is not None:
            return [ips[0] for ips in self.stable_internal_external_ips]
        return None

    def external_ips(self,
                     max_attempts: int = _FETCH_IP_MAX_ATTEMPTS,
                     use_cached_ips: bool = True) -> Optional[List[str]]:
        if not use_cached_ips:
            self._update_stable_cluster_ips(max_attempts=max_attempts)
        if self.stable_internal_external_ips is not None:
            return [ips[1] for ips in self.stable_internal_external_ips]
        return None

    def get_hourly_price(self) -> float:
        hourly_cost = (self.launched_resources.get_cost(3600) *
                       self.launched_nodes)
        return hourly_cost

    @property
    def cluster_yaml(self):
        return os.path.expanduser(self._cluster_yaml)

    @property
    def head_ip(self):
        external_ips = self.external_ips()
        if external_ips is not None:
            return external_ips[0]
        return None

    def __setstate__(self, state):
        self._version = self._VERSION

        version = state.pop('_version', None)
        if version is None:
            version = -1
            state.pop('cluster_region', None)
        if version < 2:
            state['_cluster_yaml'] = state.pop('cluster_yaml')
        if version < 3:
            head_ip = state.pop('head_ip', None)
            state['stable_internal_external_ips'] = None

        self.__dict__.update(state)

        # Because the _update_stable_cluster_ips function uses the handle,
        # we call it on the current instance after the state is updated
        if version < 3 and head_ip is not None:
            try:
                self._update_stable_cluster_ips()
            except exceptions.FetchIPError:
                # This occurs when an old cluster from was autostopped,
                # so the head IP in the database is not updated.
                pass

        self._update_cluster_region()


class CloudVmRayBackend(backends.Backend['CloudVmRayResourceHandle']):
    """Backend: runs on cloud virtual machines, managed by Ray.

    Changing this class may also require updates to:
      * Cloud providers' templates under config/
      * Cloud providers' implementations under clouds/
    """

    NAME = 'cloudvmray'

    # Backward compatibility, with the old name of the handle.
    ResourceHandle = CloudVmRayResourceHandle  # pylint: disable=invalid-name

    def __init__(self):
        self.run_timestamp = backend_utils.get_run_timestamp()
        # NOTE: do not expanduser() here, as this '~/...' path is used for
        # remote as well to be expanded on the remote side.
        self.log_dir = os.path.join(constants.SKY_LOGS_DIRECTORY,
                                    self.run_timestamp)
        # Do not make directories to avoid create folder for commands that
        # do not need it (`sky status`, `sky logs` ...)
        # os.makedirs(self.log_dir, exist_ok=True)

        self._dag = None
        self._optimize_target = None
        self._requested_features = set()

        # Command for running the setup script. It is only set when the
        # setup needs to be run outside the self._setup() and as part of
        # a job (--detach-setup).
        self._setup_cmd = None

    # --- Implementation of Backend APIs ---

    def register_info(self, **kwargs) -> None:
        self._dag = kwargs.pop('dag', self._dag)
        self._optimize_target = kwargs.pop(
            'optimize_target',
            self._optimize_target) or optimizer.OptimizeTarget.COST
        self._requested_features = kwargs.pop('requested_features',
                                              self._requested_features)
        assert len(kwargs) == 0, f'Unexpected kwargs: {kwargs}'

    def check_resources_fit_cluster(self, handle: CloudVmRayResourceHandle,
                                    task: task_lib.Task):
        """Check if resources requested by the task fit the cluster.

        The resources requested by the task should be smaller than the existing
        cluster.

        Raises:
            exceptions.ResourcesMismatchError: If the resources in the task
                does not match the existing cluster.
        """
        assert len(task.resources) == 1, task.resources

        launched_resources = handle.launched_resources
        task_resources = list(task.resources)[0]
        cluster_name = handle.cluster_name
        usage_lib.messages.usage.update_cluster_resources(
            handle.launched_nodes, launched_resources)
        record = global_user_state.get_cluster_from_name(cluster_name)
        if record is not None:
            usage_lib.messages.usage.update_cluster_status(record['status'])

        # Backward compatibility: the old launched_resources without region info
        # was handled by ResourceHandle._update_cluster_region.
        assert launched_resources.region is not None, handle

        mismatch_str = (f'To fix: specify a new cluster name, or down the '
                        f'existing cluster first: sky down {cluster_name}')
        if hasattr(handle, 'local_handle') and handle.local_handle is not None:
            launched_resources = handle.local_handle['cluster_resources']
            usage_lib.messages.usage.update_local_cluster_resources(
                launched_resources)
            mismatch_str = ('To fix: use accelerators/number of nodes that can '
                            'be satisfied by the local cluster')
        # Requested_resources <= actual_resources.
        # Special handling for local cloud case, which assumes a cluster can
        # be heterogeneous. Here, launched_resources is a list of custom
        # accelerators per node, and Resources.less_demanding_than determines
        # how many nodes satisfy task resource requirements.
        if not (task.num_nodes <= handle.launched_nodes and
                task_resources.less_demanding_than(
                    launched_resources, requested_num_nodes=task.num_nodes)):
            if (task_resources.region is not None and
                    task_resources.region != launched_resources.region):
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesMismatchError(
                        'Task requested resources in region '
                        f'{task_resources.region!r}, but the existing cluster '
                        f'is in region {launched_resources.region!r}.')
            if (task_resources.zone is not None and
                    task_resources.zone != launched_resources.zone):
                zone_str = (f'is in zone {launched_resources.zone!r}.'
                            if launched_resources.zone is not None else
                            'does not have zone specified.')
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesMismatchError(
                        'Task requested resources in zone '
                        f'{task_resources.zone!r}, but the existing cluster '
                        f'{zone_str}')
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesMismatchError(
                    'Requested resources do not match the existing cluster.\n'
                    f'  Requested:\t{task.num_nodes}x {task_resources} \n'
                    f'  Existing:\t{handle.launched_nodes}x '
                    f'{handle.launched_resources}\n'
                    f'{mismatch_str}')

    def _provision(
            self,
            task: task_lib.Task,
            to_provision: Optional[resources_lib.Resources],
            dryrun: bool,
            stream_logs: bool,
            cluster_name: str,
            retry_until_up: bool = False) -> Optional[CloudVmRayResourceHandle]:
        """Provisions using 'ray up'.

        Raises:
            exceptions.ClusterOwnerIdentityMismatchError: if the cluster
                'cluster_name' exists and is owned by another user.
            exceptions.InvalidClusterNameError: if the cluster name is invalid.
            exceptions.ResourcesMismatchError: if the requested resources
                do not match the existing cluster.
            exceptions.ResourcesUnavailableError: if the requested resources
                cannot be satisfied. The failover_history of the exception
                will be set as at least 1 exception from either our pre-checks
                (e.g., cluster name invalid) or a region/zone throwing
                resource unavailability.
            exceptions.CommandError: any ssh command error.
            RuntimeErorr: raised when 'rsync' is not installed.
            # TODO(zhwu): complete the list of exceptions.
        """
        # FIXME: ray up for Azure with different cluster_names will overwrite
        # each other.
        # When rsync is not installed in the user's machine, Ray will
        # silently retry to up the node for _MAX_RAY_UP_RETRY number
        # of times. This is time consuming so we fail early.
        backend_utils.check_rsync_installed()
        # Check if the cluster is owned by the current user. Raise
        # exceptions.ClusterOwnerIdentityMismatchError
        backend_utils.check_owner_identity(cluster_name)
        lock_path = os.path.expanduser(
            backend_utils.CLUSTER_STATUS_LOCK_PATH.format(cluster_name))
        with timeline.FileLockEvent(lock_path):
            to_provision_config = RetryingVmProvisioner.ToProvisionConfig(
                cluster_name,
                to_provision,
                task.num_nodes,
                prev_cluster_status=None)
            # Try to launch the exiting cluster first
            to_provision_config = self._check_existing_cluster(
                task, to_provision, cluster_name, dryrun)
            assert to_provision_config.resources is not None, (
                'to_provision should not be None', to_provision_config)

            prev_cluster_status = to_provision_config.prev_cluster_status
            usage_lib.messages.usage.update_cluster_resources(
                to_provision_config.num_nodes, to_provision_config.resources)
            usage_lib.messages.usage.update_cluster_status(prev_cluster_status)

            # TODO(suquark): once we have sky on PyPI, we should directly
            # install sky from PyPI.
            # NOTE: can take ~2s.
            with timeline.Event('backend.provision.wheel_build'):
                # TODO(suquark): once we have sky on PyPI, we should directly
                # install sky from PyPI.
                local_wheel_path, wheel_hash = wheel_utils.build_sky_wheel()
            backoff = common_utils.Backoff(_RETRY_UNTIL_UP_INIT_GAP_SECONDS)
            attempt_cnt = 1
            while True:
                # For on-demand instances, RetryingVmProvisioner will retry
                # within the given region first, then optionally retry on all
                # other clouds and regions (if backend.register_info()
                # has been called).
                # For spot instances, each provisioning request is made for a
                # single zone and the provisioner will retry on all other
                # clouds, regions, and zones.
                # See optimizer.py#_make_launchables_for_valid_region_zones()
                # for detailed reasons.

                # After this "round" of optimization across clouds, provisioning
                # may still have not succeeded. This while loop will then kick
                # in if retry_until_up is set, which will kick off new "rounds"
                # of optimization infinitely.
                try:
                    provisioner = RetryingVmProvisioner(
                        self.log_dir, self._dag, self._optimize_target,
                        self._requested_features, local_wheel_path, wheel_hash)
                    config_dict = provisioner.provision_with_retries(
                        task, to_provision_config, dryrun, stream_logs)
                    break
                except exceptions.ResourcesUnavailableError as e:
                    # Do not remove the stopped cluster from the global state
                    # if failed to start.
                    if e.no_failover:
                        error_message = str(e)
                    else:
                        # Clean up the cluster's entry in `sky status`.
                        global_user_state.remove_cluster(cluster_name,
                                                         terminate=True)
                        usage_lib.messages.usage.update_final_cluster_status(
                            None)
                        error_message = (
                            'Failed to provision all possible launchable '
                            'resources.'
                            f' Relax the task\'s resource requirements: '
                            f'{task.num_nodes}x {list(task.resources)[0]}')
                    if retry_until_up:
                        logger.error(error_message)
                        # Sleep and retry.
                        gap_seconds = backoff.current_backoff()
                        plural = 's' if attempt_cnt > 1 else ''
                        logger.info(
                            f'{colorama.Style.BRIGHT}=== Retry until up ==='
                            f'{colorama.Style.RESET_ALL}\n'
                            f'Retrying provisioning after {gap_seconds:.0f}s '
                            '(exponential backoff with random jittering). '
                            f'Already tried {attempt_cnt} attempt{plural}.')
                        attempt_cnt += 1
                        time.sleep(gap_seconds)
                        continue
                    error_message += (
                        '\nTo keep retrying until the cluster is up, use the '
                        '`--retry-until-up` flag.')
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.ResourcesUnavailableError(
                            error_message,
                            failover_history=e.failover_history) from None
            if dryrun:
                record = global_user_state.get_cluster_from_name(cluster_name)
                return record['handle'] if record is not None else None
            cluster_config_file = config_dict['ray']

            handle = CloudVmRayResourceHandle(
                cluster_name=cluster_name,
                cluster_yaml=cluster_config_file,
                launched_nodes=config_dict['launched_nodes'],
                launched_resources=config_dict['launched_resources'],
                # TPU.
                tpu_create_script=config_dict.get('tpu-create-script'),
                tpu_delete_script=config_dict.get('tpu-delete-script'))

            ip_list = handle.external_ips(max_attempts=_FETCH_IP_MAX_ATTEMPTS,
                                          use_cached_ips=False)
            assert ip_list is not None, handle

            if 'tpu_name' in config_dict:
                self._set_tpu_name(handle, config_dict['tpu_name'])

            # Get actual zone info and save it into handle.
            # NOTE: querying zones is expensive, observed 1node GCP >=4s.
            zones = config_dict['zones']
            if zones is not None and len(zones) == 1:  # zones is None for Azure
                # Optimization for if the provision request was for 1 zone
                # (currently happens only for GCP since it uses per-zone
                # provisioning), then we know the exact zone already.
                handle.launched_resources = handle.launched_resources.copy(
                    zone=zones[0].name)
            else:
                get_zone_cmd = (
                    handle.launched_resources.cloud.get_zone_shell_cmd())
                if get_zone_cmd is not None:
                    ssh_credentials = backend_utils.ssh_credential_from_yaml(
                        handle.cluster_yaml)
                    runners = command_runner.SSHCommandRunner.make_runner_list(
                        ip_list, **ssh_credentials)

                    def _get_zone(runner):
                        retry_count = 0
                        backoff = common_utils.Backoff(initial_backoff=1,
                                                       max_backoff_factor=3)
                        while True:
                            returncode, stdout, stderr = runner.run(
                                get_zone_cmd,
                                require_outputs=True,
                                stream_logs=False)
                            if returncode == 0:
                                break
                            retry_count += 1
                            if retry_count <= _MAX_GET_ZONE_RETRY:
                                time.sleep(backoff.current_backoff())
                                continue
                        subprocess_utils.handle_returncode(
                            returncode,
                            get_zone_cmd,
                            f'Failed to get zone for {cluster_name!r}',
                            stderr=stderr,
                            stream_logs=stream_logs)
                        return stdout.strip()

                    zones = subprocess_utils.run_in_parallel(_get_zone, runners)
                    if len(set(zones)) == 1:
                        # zone will be checked during Resources cls
                        # initialization.
                        handle.launched_resources = (
                            handle.launched_resources.copy(zone=zones[0]))
                    # If the number of zones > 1, nodes in the cluster are
                    # launched in different zones (legacy clusters before
                    # #1700), leave the zone field of handle.launched_resources
                    # to None.
            self._update_after_cluster_provisioned(handle, task,
                                                   prev_cluster_status, ip_list,
                                                   lock_path)
            return handle

    def _update_after_cluster_provisioned(
            self, handle: CloudVmRayResourceHandle, task: task_lib.Task,
            prev_cluster_status: Optional[status_lib.ClusterStatus],
            ip_list: List[str], lock_path: str) -> None:
        usage_lib.messages.usage.update_cluster_resources(
            handle.launched_nodes, handle.launched_resources)
        usage_lib.messages.usage.update_final_cluster_status(
            status_lib.ClusterStatus.UP)

        # For backward compatibility and robustness of skylet, it is restarted
        with log_utils.safe_rich_status('Updating remote skylet'):
            self.run_on_head(handle, _MAYBE_SKYLET_RESTART_CMD)

        # Update job queue to avoid stale jobs (when restarted), before
        # setting the cluster to be ready.
        if prev_cluster_status == status_lib.ClusterStatus.INIT:
            # update_status will query the ray job status for all INIT /
            # PENDING / RUNNING jobs for the real status, since we do not
            # know the actual previous status of the cluster.
            job_owner = onprem_utils.get_job_owner(handle.cluster_yaml)
            cmd = job_lib.JobLibCodeGen.update_status(job_owner)
            with log_utils.safe_rich_status('[bold cyan]Preparing Job Queue'):
                returncode, _, stderr = self.run_on_head(handle,
                                                         cmd,
                                                         require_outputs=True)
            subprocess_utils.handle_returncode(returncode, cmd,
                                               'Failed to update job status.',
                                               stderr)
        if prev_cluster_status == status_lib.ClusterStatus.STOPPED:
            # Safely set all the previous jobs to FAILED since the cluster
            # is restarted
            # An edge case here due to racing:
            # 1. A job finishes RUNNING, but right before it update itself
            # to SUCCEEDED, the cluster is STOPPED by `sky stop`.
            # 2. On next `sky start`, it gets reset to FAILED.
            cmd = job_lib.JobLibCodeGen.fail_all_jobs_in_progress()
            returncode, stdout, stderr = self.run_on_head(handle,
                                                          cmd,
                                                          require_outputs=True)
            subprocess_utils.handle_returncode(
                returncode, cmd,
                'Failed to set previously in-progress jobs to FAILED',
                stdout + stderr)

        with timeline.Event('backend.provision.post_process'):
            global_user_state.add_or_update_cluster(
                handle.cluster_name,
                handle,
                task.resources,
                ready=True,
            )
            usage_lib.messages.usage.update_final_cluster_status(
                status_lib.ClusterStatus.UP)
            auth_config = common_utils.read_yaml(handle.cluster_yaml)['auth']
            backend_utils.SSHConfigHelper.add_cluster(handle.cluster_name,
                                                      ip_list, auth_config)

            common_utils.remove_file_if_exists(lock_path)

    def _sync_workdir(self, handle: CloudVmRayResourceHandle,
                      workdir: Path) -> None:
        # Even though provision() takes care of it, there may be cases where
        # this function is called in isolation, without calling provision(),
        # e.g., in CLI.  So we should rerun rsync_up.
        fore = colorama.Fore
        style = colorama.Style
        ip_list = handle.external_ips()
        assert ip_list is not None, 'external_ips is not cached in handle'
        full_workdir = os.path.abspath(os.path.expanduser(workdir))

        # These asserts have been validated at Task construction time.
        assert os.path.exists(full_workdir), f'{full_workdir} does not exist'
        if os.path.islink(full_workdir):
            logger.warning(
                f'{fore.YELLOW}Workdir {workdir!r} is a symlink. '
                f'Symlink contents are not uploaded.{style.RESET_ALL}')
        else:
            assert os.path.isdir(
                full_workdir), f'{full_workdir} should be a directory.'

        # Raise warning if directory is too large
        dir_size = backend_utils.path_size_megabytes(full_workdir)
        if dir_size >= _PATH_SIZE_MEGABYTES_WARN_THRESHOLD:
            logger.warning(
                f'{fore.YELLOW}The size of workdir {workdir!r} '
                f'is {dir_size} MB. Try to keep workdir small or use '
                '.gitignore to exclude large files, as large sizes will slow '
                f'down rsync.{style.RESET_ALL}')

        log_path = os.path.join(self.log_dir, 'workdir_sync.log')

        ssh_credentials = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)

        # TODO(zhwu): refactor this with backend_utils.parallel_cmd_with_rsync
        runners = command_runner.SSHCommandRunner.make_runner_list(
            ip_list, **ssh_credentials)

        def _sync_workdir_node(runner: command_runner.SSHCommandRunner) -> None:
            runner.rsync(
                source=workdir,
                target=SKY_REMOTE_WORKDIR,
                up=True,
                log_path=log_path,
                stream_logs=False,
            )

        num_nodes = handle.launched_nodes
        plural = 's' if num_nodes > 1 else ''
        logger.info(
            f'{fore.CYAN}Syncing workdir (to {num_nodes} node{plural}): '
            f'{style.BRIGHT}{workdir}{style.RESET_ALL}'
            f' -> '
            f'{style.BRIGHT}{SKY_REMOTE_WORKDIR}{style.RESET_ALL}')
        os.makedirs(os.path.expanduser(self.log_dir), exist_ok=True)
        os.system(f'touch {log_path}')
        tail_cmd = f'tail -n100 -f {log_path}'
        logger.info('To view detailed progress: '
                    f'{style.BRIGHT}{tail_cmd}{style.RESET_ALL}')
        with log_utils.safe_rich_status('[bold cyan]Syncing[/]'):
            subprocess_utils.run_in_parallel(_sync_workdir_node, runners)

    def _sync_file_mounts(
        self,
        handle: CloudVmRayResourceHandle,
        all_file_mounts: Dict[Path, Path],
        storage_mounts: Dict[Path, storage_lib.Storage],
    ) -> None:
        """Mounts all user files to the remote nodes."""
        self._execute_file_mounts(handle, all_file_mounts)
        self._execute_storage_mounts(handle, storage_mounts)

    def _setup(self, handle: CloudVmRayResourceHandle, task: task_lib.Task,
               detach_setup: bool) -> None:
        start = time.time()
        style = colorama.Style
        fore = colorama.Fore

        if task.setup is None:
            return

        setup_script = log_lib.make_task_bash_script(task.setup,
                                                     env_vars=task.envs)
        with tempfile.NamedTemporaryFile('w', prefix='sky_setup_') as f:
            f.write(setup_script)
            f.flush()
            setup_sh_path = f.name
            setup_file = os.path.basename(setup_sh_path)
            # Sync the setup script up and run it.
            ip_list = handle.external_ips()
            assert ip_list is not None, 'external_ips is not cached in handle'
            ssh_credentials = backend_utils.ssh_credential_from_yaml(
                handle.cluster_yaml)
            # Disable connection sharing for setup script to avoid old
            # connections being reused, which may cause stale ssh agent
            # forwarding.
            ssh_credentials.pop('ssh_control_name')
            runners = command_runner.SSHCommandRunner.make_runner_list(
                ip_list, **ssh_credentials)

            # Need this `-i` option to make sure `source ~/.bashrc` work
            setup_cmd = f'/bin/bash -i /tmp/{setup_file} 2>&1'

            def _setup_node(runner: command_runner.SSHCommandRunner) -> None:
                runner.rsync(source=setup_sh_path,
                             target=f'/tmp/{setup_file}',
                             up=True,
                             stream_logs=False)
                if detach_setup:
                    return
                setup_log_path = os.path.join(self.log_dir,
                                              f'setup-{runner.ip}.log')
                returncode = runner.run(
                    setup_cmd,
                    log_path=setup_log_path,
                    process_stream=False,
                )

                def error_message() -> str:
                    # Use the function to avoid tailing the file in success case
                    try:
                        last_10_lines = subprocess.run(
                            [
                                'tail', '-n10',
                                os.path.expanduser(setup_log_path)
                            ],
                            stdout=subprocess.PIPE,
                            check=True).stdout.decode('utf-8')
                    except subprocess.CalledProcessError:
                        last_10_lines = None

                    err_msg = (
                        f'Failed to setup with return code {returncode}. '
                        f'Check the details in log: {setup_log_path}')
                    if last_10_lines:
                        err_msg += (
                            f'\n\n{colorama.Fore.RED}'
                            '****** START Last lines of setup output ******'
                            f'{colorama.Style.RESET_ALL}\n'
                            f'{last_10_lines}'
                            f'{colorama.Fore.RED}'
                            '******* END Last lines of setup output *******'
                            f'{colorama.Style.RESET_ALL}')
                    return err_msg

                subprocess_utils.handle_returncode(returncode=returncode,
                                                   command=setup_cmd,
                                                   error_msg=error_message)

            num_nodes = len(ip_list)
            plural = 's' if num_nodes > 1 else ''
            if not detach_setup:
                logger.info(
                    f'{fore.CYAN}Running setup on {num_nodes} node{plural}.'
                    f'{style.RESET_ALL}')
            subprocess_utils.run_in_parallel(_setup_node, runners)

        if detach_setup:
            # Only set this when setup needs to be run outside the self._setup()
            # as part of a job (--detach-setup).
            self._setup_cmd = setup_cmd
            return
        logger.info(f'{fore.GREEN}Setup completed.{style.RESET_ALL}')
        end = time.time()
        logger.debug(f'Setup took {end - start} seconds.')

    def _exec_code_on_head(
        self,
        handle: CloudVmRayResourceHandle,
        codegen: str,
        job_id: int,
        executable: str,
        detach_run: bool = False,
        spot_dag: Optional['dag.Dag'] = None,
    ) -> None:
        """Executes generated code on the head node."""
        style = colorama.Style
        fore = colorama.Fore
        ssh_credentials = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)
        runner = command_runner.SSHCommandRunner(handle.head_ip,
                                                 **ssh_credentials)
        with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
            fp.write(codegen)
            fp.flush()
            script_path = os.path.join(SKY_REMOTE_APP_DIR, f'sky_job_{job_id}')
            # We choose to sync code + exec, because the alternative of 'ray
            # submit' may not work as it may use system python (python2) to
            # execute the script.  Happens for AWS.
            runner.rsync(source=fp.name,
                         target=script_path,
                         up=True,
                         stream_logs=False)
        remote_log_dir = self.log_dir
        remote_log_path = os.path.join(remote_log_dir, 'run.log')

        assert executable == 'python3', executable
        cd = f'cd {SKY_REMOTE_WORKDIR}'

        ray_job_id = job_lib.make_ray_job_id(job_id,
                                             ssh_credentials['ssh_user'])
        if isinstance(handle.launched_resources.cloud, clouds.Local):
            # Ray Multitenancy is unsupported.
            # (Git Issue) https://github.com/ray-project/ray/issues/6800
            # Temporary workaround - wrap the run command in a script, and
            # execute it as the specified user.
            executable = onprem_utils.get_python_executable(handle.cluster_name)
            ray_command = (f'{cd} && {executable} -u {script_path} '
                           f'> {remote_log_path} 2>&1')
            job_submit_cmd = self._setup_and_create_job_cmd_on_local_head(
                handle, ray_command, ray_job_id)
        else:
            job_submit_cmd = (
                'RAY_DASHBOARD_PORT=$(python -c "from sky.skylet import job_lib; print(job_lib.get_job_submission_port())" 2> /dev/null || echo 8265);'  # pylint: disable=line-too-long
                f'{cd} && ray job submit '
                '--address=http://127.0.0.1:$RAY_DASHBOARD_PORT '
                f'--submission-id {ray_job_id} --no-wait '
                f'"{executable} -u {script_path} > {remote_log_path} 2>&1"')

            mkdir_code = (f'{cd} && mkdir -p {remote_log_dir} && '
                          f'touch {remote_log_path}')
            code = job_lib.JobLibCodeGen.queue_job(job_id, job_submit_cmd)

            job_submit_cmd = mkdir_code + ' && ' + code

            if spot_dag is not None:
                # Add the spot job to spot queue table.
                spot_codegen = spot_lib.SpotCodeGen()
                spot_code = spot_codegen.set_pending(job_id, spot_dag)
                # Set the spot job to PENDING state to make sure that this spot
                # job appears in the `sky spot queue`, when there are already 16
                # controller process jobs running on the controller VM with 8
                # CPU cores.
                # The spot job should be set to PENDING state *after* the
                # controller process job has been queued, as our skylet on spot
                # controller will set the spot job in FAILED state if the
                # controller process job does not exist.
                # We cannot set the spot job to PENDING state in the codegen for
                # the controller process job, as it will stay in the job pending
                # table and not be executed until there is an empty slot.
                job_submit_cmd = job_submit_cmd + ' && ' + spot_code

        returncode, stdout, stderr = self.run_on_head(handle,
                                                      job_submit_cmd,
                                                      stream_logs=False,
                                                      require_outputs=True)

        if 'has no attribute' in stdout:
            # Happens when someone calls `sky exec` but remote is outdated
            # necessicating calling `sky launch`
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'{colorama.Fore.RED}SkyPilot runtime is stale on the '
                    'remote cluster. To update, run: sky launch -c '
                    f'{handle.cluster_name}{colorama.Style.RESET_ALL}')

        subprocess_utils.handle_returncode(returncode,
                                           job_submit_cmd,
                                           f'Failed to submit job {job_id}.',
                                           stderr=stdout + stderr)

        logger.info('Job submitted with Job ID: '
                    f'{style.BRIGHT}{job_id}{style.RESET_ALL}')

        try:
            if not detach_run:
                if handle.cluster_name == spot_lib.SPOT_CONTROLLER_NAME:
                    self.tail_spot_logs(handle, job_id)
                else:
                    # Sky logs. Not using subprocess.run since it will make the
                    # ssh keep connected after ctrl-c.
                    self.tail_logs(handle, job_id)
        finally:
            name = handle.cluster_name
            if name == spot_lib.SPOT_CONTROLLER_NAME:
                logger.info(
                    f'{fore.CYAN}Spot Job ID: '
                    f'{style.BRIGHT}{job_id}{style.RESET_ALL}'
                    '\nTo cancel the job:\t\t'
                    f'{backend_utils.BOLD}sky spot cancel {job_id}'
                    f'{backend_utils.RESET_BOLD}'
                    '\nTo stream job logs:\t\t'
                    f'{backend_utils.BOLD}sky spot logs {job_id}'
                    f'{backend_utils.RESET_BOLD}'
                    f'\nTo stream controller logs:\t'
                    f'{backend_utils.BOLD}sky spot logs --controller {job_id}'
                    f'{backend_utils.RESET_BOLD}'
                    '\nTo view all spot jobs:\t\t'
                    f'{backend_utils.BOLD}sky spot queue'
                    f'{backend_utils.RESET_BOLD}'
                    '\nTo view the spot job dashboard:\t'
                    f'{backend_utils.BOLD}sky spot dashboard'
                    f'{backend_utils.RESET_BOLD}')
            else:
                logger.info(f'{fore.CYAN}Job ID: '
                            f'{style.BRIGHT}{job_id}{style.RESET_ALL}'
                            '\nTo cancel the job:\t'
                            f'{backend_utils.BOLD}sky cancel {name} {job_id}'
                            f'{backend_utils.RESET_BOLD}'
                            '\nTo stream job logs:\t'
                            f'{backend_utils.BOLD}sky logs {name} {job_id}'
                            f'{backend_utils.RESET_BOLD}'
                            '\nTo view the job queue:\t'
                            f'{backend_utils.BOLD}sky queue {name}'
                            f'{backend_utils.RESET_BOLD}')

    def _setup_and_create_job_cmd_on_local_head(
        self,
        handle: CloudVmRayResourceHandle,
        ray_command: str,
        ray_job_id: str,
    ):
        """Generates and prepares job submission code for local clusters."""
        ssh_credentials = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)
        ssh_user = ssh_credentials['ssh_user']
        runner = command_runner.SSHCommandRunner(handle.head_ip,
                                                 **ssh_credentials)
        remote_log_dir = self.log_dir
        with tempfile.NamedTemporaryFile('w', prefix='sky_local_app_') as fp:
            fp.write(ray_command)
            fp.flush()
            run_file = os.path.basename(fp.name)
            remote_run_file = f'/tmp/sky_local/{run_file}'
            # Ensures remote_run_file directory is created.
            runner.run(f'mkdir -p {os.path.dirname(remote_run_file)}',
                       stream_logs=False)
            # We choose to sync code + exec, so that Ray job submission API will
            # work for the multitenant case.
            runner.rsync(source=fp.name,
                         target=remote_run_file,
                         up=True,
                         stream_logs=False)
        runner.run(f'mkdir -p {remote_log_dir}; chmod a+rwx {remote_run_file}',
                   stream_logs=False)
        switch_user_cmd = job_lib.make_job_command_with_user_switching(
            ssh_user, remote_run_file)
        switch_user_cmd = ' '.join(switch_user_cmd)
        job_submit_cmd = (
            'ray job submit '
            '--address='
            f'http://127.0.0.1:{constants.SKY_REMOTE_RAY_DASHBOARD_PORT} '
            f'--submission-id {ray_job_id} '
            f'--no-wait -- {switch_user_cmd}')
        return job_submit_cmd

    def _add_job(self, handle: CloudVmRayResourceHandle,
                 job_name: Optional[str], resources_str: str) -> int:
        username = getpass.getuser()
        code = job_lib.JobLibCodeGen.add_job(job_name, username,
                                             self.run_timestamp, resources_str)
        returncode, job_id_str, stderr = self.run_on_head(handle,
                                                          code,
                                                          stream_logs=False,
                                                          require_outputs=True,
                                                          separate_stderr=True)
        # TODO(zhwu): this sometimes will unexpectedly fail, we can add
        # retry for this, after we figure out the reason.
        subprocess_utils.handle_returncode(returncode, code,
                                           'Failed to fetch job id.', stderr)
        try:
            job_id_match = _JOB_ID_PATTERN.search(job_id_str)
            if job_id_match is not None:
                job_id = int(job_id_match.group(1))
            else:
                # For backward compatibility.
                job_id = int(job_id_str)
        except ValueError as e:
            logger.error(stderr)
            raise ValueError(f'Failed to parse job id: {job_id_str}; '
                             f'Returncode: {returncode}') from e
        return job_id

    def _execute(
        self,
        handle: CloudVmRayResourceHandle,
        task: task_lib.Task,
        detach_run: bool,
        dryrun: bool = False,
    ) -> None:
        if task.run is None:
            logger.info('Run commands not specified or empty.')
            return
        # Check the task resources vs the cluster resources. Since `sky exec`
        # will not run the provision and _check_existing_cluster
        self.check_resources_fit_cluster(handle, task)

        resources_str = backend_utils.get_task_resources_str(task)

        if dryrun:
            logger.info(f'Dryrun complete. Would have run:\n{task}')
            return

        job_id = self._add_job(handle, task.name, resources_str)

        is_tpu_vm_pod = tpu_utils.is_tpu_vm_pod(handle.launched_resources)
        # Case: task_lib.Task(run, num_nodes=N) or TPU VM Pods
        if task.num_nodes > 1 or is_tpu_vm_pod:
            self._execute_task_n_nodes(handle, task, job_id, detach_run)
        else:
            # Case: task_lib.Task(run, num_nodes=1)
            self._execute_task_one_node(handle, task, job_id, detach_run)

    def _post_execute(self, handle: CloudVmRayResourceHandle,
                      down: bool) -> None:
        fore = colorama.Fore
        style = colorama.Style
        name = handle.cluster_name
        if name == spot_lib.SPOT_CONTROLLER_NAME or down:
            return
        stop_str = ('\nTo stop the cluster:'
                    f'\t{backend_utils.BOLD}sky stop {name}'
                    f'{backend_utils.RESET_BOLD}')
        if isinstance(handle.launched_resources.cloud, clouds.Local):
            stop_str = ''
        logger.info(f'\n{fore.CYAN}Cluster name: '
                    f'{style.BRIGHT}{name}{style.RESET_ALL}'
                    '\nTo log into the head VM:\t'
                    f'{backend_utils.BOLD}ssh {name}'
                    f'{backend_utils.RESET_BOLD}'
                    '\nTo submit a job:'
                    f'\t\t{backend_utils.BOLD}sky exec {name} yaml_file'
                    f'{backend_utils.RESET_BOLD}'
                    f'{stop_str}'
                    '\nTo teardown the cluster:'
                    f'\t{backend_utils.BOLD}sky down {name}'
                    f'{backend_utils.RESET_BOLD}')
        if handle.tpu_delete_script is not None:
            logger.info('Tip: `sky down` will delete launched TPU(s) too.')

    def _teardown_ephemeral_storage(self, task: task_lib.Task) -> None:
        storage_mounts = task.storage_mounts
        if storage_mounts is not None:
            for _, storage in storage_mounts.items():
                if not storage.persistent:
                    storage.delete()

    def _teardown(self,
                  handle: CloudVmRayResourceHandle,
                  terminate: bool,
                  purge: bool = False):
        """Tear down/ Stop the cluster.

        Args:
            handle: The handle to the cluster.
            terminate: Terminate or stop the cluster.
            purge: Purge the cluster record from the cluster table, even if
                the teardown fails.
        Raises:
            exceptions.ClusterOwnerIdentityMismatchError: If the cluster is
                owned by another user.
            exceptions.CloudUserIdentityError: if we fail to get the current
                user identity.
            RuntimeError: If the cluster fails to be terminated/stopped.
        """
        cluster_name = handle.cluster_name
        # Check if the cluster is owned by the current user. Raise
        # exceptions.ClusterOwnerIdentityMismatchError
        yellow = colorama.Fore.YELLOW
        reset = colorama.Style.RESET_ALL
        is_identity_mismatch_and_purge = False
        try:
            backend_utils.check_owner_identity(cluster_name)
        except exceptions.ClusterOwnerIdentityMismatchError as e:
            if purge:
                logger.error(e)
                verbed = 'terminated' if terminate else 'stopped'
                logger.warning(
                    f'{yellow}Purge (-p/--purge) is set, ignoring the '
                    f'identity mismatch error and removing '
                    f'the cluser record from cluster table.{reset}\n{yellow}It '
                    'is the user\'s responsibility to ensure that this '
                    f'cluster is actually {verbed} on the cloud.{reset}')
                is_identity_mismatch_and_purge = True
            else:
                raise

        lock_path = os.path.expanduser(
            backend_utils.CLUSTER_STATUS_LOCK_PATH.format(cluster_name))

        try:
            # TODO(mraheja): remove pylint disabling when filelock
            # version updated
            # pylint: disable=abstract-class-instantiated
            with filelock.FileLock(
                    lock_path,
                    backend_utils.CLUSTER_STATUS_LOCK_TIMEOUT_SECONDS):
                self.teardown_no_lock(
                    handle,
                    terminate,
                    purge,
                    # When --purge is set and we already see an ID mismatch
                    # error, we skip the refresh codepath. This is because
                    # refresh checks current user identity can throw
                    # ClusterOwnerIdentityMismatchError. The argument/flag
                    # `purge` should bypass such ID mismatch errors.
                    refresh_cluster_status=not is_identity_mismatch_and_purge)
            if terminate:
                common_utils.remove_file_if_exists(lock_path)
        except filelock.Timeout as e:
            raise RuntimeError(
                f'Cluster {cluster_name!r} is locked by {lock_path}. '
                'Check to see if it is still being launched.') from e

    # --- CloudVMRayBackend Specific APIs ---

    def get_job_status(
        self,
        handle: CloudVmRayResourceHandle,
        job_ids: Optional[List[int]] = None,
        stream_logs: bool = True
    ) -> Dict[Optional[int], Optional[job_lib.JobStatus]]:
        code = job_lib.JobLibCodeGen.get_job_status(job_ids)
        returncode, stdout, stderr = self.run_on_head(handle,
                                                      code,
                                                      stream_logs=stream_logs,
                                                      require_outputs=True,
                                                      separate_stderr=True)
        subprocess_utils.handle_returncode(returncode, code,
                                           'Failed to get job status.', stderr)
        statuses = job_lib.load_statuses_payload(stdout)
        return statuses

    def cancel_jobs(self, handle: CloudVmRayResourceHandle,
                    jobs: Optional[List[int]]):
        job_owner = onprem_utils.get_job_owner(handle.cluster_yaml)
        code = job_lib.JobLibCodeGen.cancel_jobs(job_owner, jobs)

        # All error messages should have been redirected to stdout.
        returncode, stdout, _ = self.run_on_head(handle,
                                                 code,
                                                 stream_logs=False,
                                                 require_outputs=True)
        subprocess_utils.handle_returncode(
            returncode, code,
            f'Failed to cancel jobs on cluster {handle.cluster_name}.', stdout)

    def sync_down_logs(
            self,
            handle: CloudVmRayResourceHandle,
            job_ids: Optional[List[str]],
            local_dir: str = constants.SKY_LOGS_DIRECTORY) -> Dict[str, str]:
        """Sync down logs for the given job_ids.

        Returns:
            A dictionary mapping job_id to log path.
        """
        code = job_lib.JobLibCodeGen.get_run_timestamp_with_globbing(job_ids)
        returncode, run_timestamps, stderr = self.run_on_head(
            handle,
            code,
            stream_logs=False,
            require_outputs=True,
            separate_stderr=True)
        subprocess_utils.handle_returncode(returncode, code,
                                           'Failed to sync logs.', stderr)
        run_timestamps = common_utils.decode_payload(run_timestamps)
        if not run_timestamps:
            logger.info(f'{colorama.Fore.YELLOW}'
                        'No matching log directories found'
                        f'{colorama.Style.RESET_ALL}')
            return {}

        job_ids = list(run_timestamps.keys())
        run_timestamps = list(run_timestamps.values())
        remote_log_dirs = [
            os.path.join(constants.SKY_LOGS_DIRECTORY, run_timestamp)
            for run_timestamp in run_timestamps
        ]
        local_log_dirs = [
            os.path.expanduser(os.path.join(local_dir, run_timestamp))
            for run_timestamp in run_timestamps
        ]

        style = colorama.Style
        fore = colorama.Fore
        for job_id, log_dir in zip(job_ids, local_log_dirs):
            logger.info(f'{fore.CYAN}Job {job_id} logs: {log_dir}'
                        f'{style.RESET_ALL}')

        ip_list = handle.external_ips()
        assert ip_list is not None, 'external_ips is not cached in handle'
        ssh_credentials = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)
        runners = command_runner.SSHCommandRunner.make_runner_list(
            ip_list, **ssh_credentials)

        def _rsync_down(args) -> None:
            """Rsync down logs from remote nodes.

            Args:
                args: A tuple of (runner, local_log_dir, remote_log_dir)
            """
            (runner, local_log_dir, remote_log_dir) = args
            try:
                os.makedirs(local_log_dir, exist_ok=True)
                runner.rsync(
                    source=f'{remote_log_dir}/*',
                    target=local_log_dir,
                    up=False,
                    stream_logs=False,
                )
            except exceptions.CommandError as e:
                if e.returncode == exceptions.RSYNC_FILE_NOT_FOUND_CODE:
                    # Raised by rsync_down. Remote log dir may not exist, since
                    # the job can be run on some part of the nodes.
                    logger.debug(f'{runner.ip} does not have the tasks/*.')
                else:
                    raise

        parallel_args = [[runner, *item]
                         for item in zip(local_log_dirs, remote_log_dirs)
                         for runner in runners]
        subprocess_utils.run_in_parallel(_rsync_down, parallel_args)
        return dict(zip(job_ids, local_log_dirs))

    def tail_logs(self,
                  handle: CloudVmRayResourceHandle,
                  job_id: Optional[int],
                  spot_job_id: Optional[int] = None,
                  follow: bool = True) -> int:
        job_owner = onprem_utils.get_job_owner(handle.cluster_yaml)
        code = job_lib.JobLibCodeGen.tail_logs(job_owner,
                                               job_id,
                                               spot_job_id=spot_job_id,
                                               follow=follow)
        if job_id is None and spot_job_id is None:
            logger.info(
                'Job ID not provided. Streaming the logs of the latest job.')

        # With the stdin=subprocess.DEVNULL, the ctrl-c will not directly
        # kill the process, so we need to handle it manually here.
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, backend_utils.interrupt_handler)
            signal.signal(signal.SIGTSTP, backend_utils.stop_handler)
        try:
            returncode = self.run_on_head(
                handle,
                code,
                stream_logs=True,
                process_stream=False,
                # Allocate a pseudo-terminal to disable output buffering.
                # Otherwise, there may be 5 minutes delay in logging.
                ssh_mode=command_runner.SshMode.INTERACTIVE,
                # Disable stdin to avoid ray outputs mess up the terminal with
                # misaligned output in multithreading/multiprocessing.
                # Refer to: https://github.com/ray-project/ray/blob/d462172be7c5779abf37609aed08af112a533e1e/python/ray/autoscaler/_private/subprocess_output_util.py#L264 # pylint: disable=line-too-long
                stdin=subprocess.DEVNULL,
            )
        except SystemExit as e:
            returncode = e.code
        return returncode

    def tail_spot_logs(self,
                       handle: CloudVmRayResourceHandle,
                       job_id: Optional[int] = None,
                       job_name: Optional[str] = None,
                       follow: bool = True) -> None:
        # if job_name is not None, job_id should be None
        assert job_name is None or job_id is None, (job_name, job_id)
        if job_name is not None:
            code = spot_lib.SpotCodeGen.stream_logs_by_name(job_name, follow)
        else:
            code = spot_lib.SpotCodeGen.stream_logs_by_id(job_id, follow)

        # With the stdin=subprocess.DEVNULL, the ctrl-c will not directly
        # kill the process, so we need to handle it manually here.
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, backend_utils.interrupt_handler)
            signal.signal(signal.SIGTSTP, backend_utils.stop_handler)

        # Refer to the notes in tail_logs.
        self.run_on_head(
            handle,
            code,
            stream_logs=True,
            process_stream=False,
            ssh_mode=command_runner.SshMode.INTERACTIVE,
            stdin=subprocess.DEVNULL,
        )

    def teardown_no_lock(self,
                         handle: CloudVmRayResourceHandle,
                         terminate: bool,
                         purge: bool = False,
                         post_teardown_cleanup: bool = True,
                         refresh_cluster_status: bool = True) -> None:
        """Teardown the cluster without acquiring the cluster status lock.

        NOTE: This method should not be called without holding the cluster
        status lock already.

        refresh_cluster_status is only used internally in the status refresh
        process, and should not be set to False in other cases.

        Raises:
            RuntimeError: If the cluster fails to be terminated/stopped.
        """
        if refresh_cluster_status:
            prev_cluster_status, _ = (
                backend_utils.refresh_cluster_status_handle(
                    handle.cluster_name, acquire_per_cluster_status_lock=False))
        else:
            record = global_user_state.get_cluster_from_name(
                handle.cluster_name)
            prev_cluster_status = record[
                'status'] if record is not None else None
        if prev_cluster_status is None:
            # When the cluster is not in the cluster table, we guarantee that
            # all related resources / cache / config are cleaned up, i.e. it
            # is safe to skip and return True.
            ux_utils.console_newline()
            logger.warning(
                f'Cluster {handle.cluster_name!r} is already terminated. '
                'Skipped.')
            return
        log_path = os.path.join(os.path.expanduser(self.log_dir),
                                'teardown.log')
        log_abs_path = os.path.abspath(log_path)
        cloud = handle.launched_resources.cloud
        config = common_utils.read_yaml(handle.cluster_yaml)
        cluster_name = handle.cluster_name

        # Avoid possibly unbound warnings. Code below must overwrite these vars:
        returncode = 0
        stdout = ''
        stderr = ''

        # Use the new provisioner for AWS.
        if isinstance(cloud, (clouds.AWS, clouds.GCP)):
            # Stop the ray autoscaler first to avoid the head node trying to
            # re-launch the worker nodes, during the termination of the
            # cluster.
            try:
                # We do not check the return code, since Ray returns
                # non-zero return code when calling Ray stop,
                # even when the command was executed successfully.
                self.run_on_head(handle, 'ray stop --force')
            except RuntimeError:
                # This error is expected if the previous cluster IP is
                # failed to be found,
                # i.e., the cluster is already stopped/terminated.
                if prev_cluster_status == status_lib.ClusterStatus.UP:
                    logger.warning(
                        'Failed to take down Ray autoscaler on the head node. '
                        'It might be because the cluster\'s head node has '
                        'already been terminated. It is fine to skip this.')
            try:
                if terminate:
                    provision_api.terminate_instances(
                        repr(cloud),
                        cluster_name,
                        provider_config=config['provider'])
                else:
                    provision_api.stop_instances(
                        repr(cloud),
                        cluster_name,
                        provider_config=config['provider'])
            except Exception as e:  # pylint: disable=broad-except
                if purge:
                    logger.warning(
                        _TEARDOWN_PURGE_WARNING.format(
                            reason='stopping/terminating cluster nodes',
                            details=common_utils.format_exception(
                                e, use_bracket=True)))
                else:
                    raise

            if post_teardown_cleanup:
                self.post_teardown_cleanup(handle, terminate, purge)
            return

        if terminate and isinstance(cloud, clouds.Azure):
            # Here we handle termination of Azure by ourselves instead of Ray
            # autoscaler.
            resource_group = config['provider']['resource_group']
            terminate_cmd = f'az group delete -y --name {resource_group}'
            with log_utils.safe_rich_status(f'[bold cyan]Terminating '
                                            f'[green]{cluster_name}'):
                returncode, stdout, stderr = log_lib.run_with_log(
                    terminate_cmd,
                    log_abs_path,
                    shell=True,
                    stream_logs=False,
                    require_outputs=True)

        elif (isinstance(cloud, clouds.IBM) and terminate and
              prev_cluster_status == status_lib.ClusterStatus.STOPPED):
            # pylint: disable= W0622 W0703 C0415
            from sky.adaptors import ibm
            from sky.skylet.providers.ibm.vpc_provider import IBMVPCProvider

            config_provider = common_utils.read_yaml(
                handle.cluster_yaml)['provider']
            region = config_provider['region']
            cluster_name = handle.cluster_name
            search_client = ibm.search_client()
            vpc_found = False
            # pylint: disable=unsubscriptable-object
            vpcs_filtered_by_tags_and_region = search_client.search(
                query=f'type:vpc AND tags:{cluster_name} AND region:{region}',
                fields=['tags', 'region', 'type'],
                limit=1000).get_result()['items']
            vpc_id = None
            try:
                # pylint: disable=line-too-long
                vpc_id = vpcs_filtered_by_tags_and_region[0]['crn'].rsplit(
                    ':', 1)[-1]
                vpc_found = True
            except Exception:
                logger.critical('failed to locate vpc for ibm cloud')
                returncode = -1

            if vpc_found:
                # # pylint: disable=line-too-long E1136
                # Delete VPC and it's associated resources
                vpc_provider = IBMVPCProvider(
                    config_provider['resource_group_id'], region, cluster_name)
                vpc_provider.delete_vpc(vpc_id, region)
                # successfully removed cluster as no exception was raised
                returncode = 0

        elif terminate and isinstance(cloud, clouds.SCP):
            config['provider']['cache_stopped_nodes'] = not terminate
            provider = SCPNodeProvider(config['provider'], handle.cluster_name)
            try:
                if not os.path.exists(provider.metadata.path):
                    raise SCPError('SKYPILOT_ERROR_NO_NODES_LAUNCHED: '
                                   'Metadata file does not exist.')

                with open(provider.metadata.path, 'r') as f:
                    metadata = json.load(f)
                    node_id = next(iter(metadata.values())).get(
                        'creation', {}).get('virtualServerId', None)
                    provider.terminate_node(node_id)
                returncode = 0
            except SCPError as e:
                returncode = 1
                stdout = ''
                stderr = str(e)

        # Apr, 2023 by Hysun(hysun.he@oracle.com): Added support for OCI
        # May, 2023 by Hysun: Allow terminate INIT cluster which may have
        # some instances provisioning in background but not completed.
        elif (isinstance(cloud, clouds.OCI) and terminate and
              prev_cluster_status in (status_lib.ClusterStatus.STOPPED,
                                      status_lib.ClusterStatus.INIT)):
            region = config['provider']['region']

            # pylint: disable=import-outside-toplevel
            from sky.skylet.providers.oci.query_helper import oci_query_helper
            from ray.autoscaler.tags import TAG_RAY_CLUSTER_NAME

            # 0: All terminated successfully, failed count otherwise
            returncode = oci_query_helper.terminate_instances_by_tags(
                {TAG_RAY_CLUSTER_NAME: cluster_name}, region)

            # To avoid undefined local variables error.
            stdout = stderr = ''
        else:
            config['provider']['cache_stopped_nodes'] = not terminate
            with tempfile.NamedTemporaryFile('w',
                                             prefix='sky_',
                                             delete=False,
                                             suffix='.yml') as f:
                common_utils.dump_yaml(f.name, config)
                f.flush()

                teardown_verb = 'Terminating' if terminate else 'Stopping'
                with log_utils.safe_rich_status(f'[bold cyan]{teardown_verb} '
                                                f'[green]{cluster_name}'):
                    # FIXME(zongheng): support retries. This call can fail for
                    # example due to GCP returning list requests per limit
                    # exceeded.
                    returncode, stdout, stderr = log_lib.run_with_log(
                        ['ray', 'down', '-y', f.name],
                        log_abs_path,
                        stream_logs=False,
                        require_outputs=True,
                        # Disable stdin to avoid ray outputs mess up the
                        # terminal with misaligned output when multithreading/
                        # multiprocessing are used.
                        # Refer to: https://github.com/ray-project/ray/blob/d462172be7c5779abf37609aed08af112a533e1e/python/ray/autoscaler/_private/subprocess_output_util.py#L264 # pylint: disable=line-too-long
                        stdin=subprocess.DEVNULL)
        if returncode != 0:
            if purge:
                logger.warning(
                    _TEARDOWN_PURGE_WARNING.format(
                        reason='stopping/terminating cluster nodes',
                        details=stderr))
            # 'TPU must be specified.': This error returns when we call "gcloud
            #   delete" with an empty VM list where no instance exists. Safe to
            #   ignore it and do cleanup locally. TODO(wei-lin): refactor error
            #   handling mechanism.
            #
            # 'SKYPILOT_ERROR_NO_NODES_LAUNCHED': this indicates nodes are
            #   never launched and the errors are related to pre-launch
            #   configurations (such as VPC not found). So it's safe & good UX
            #   to not print a failure message.
            #
            # '(ResourceGroupNotFound)': this indicates the resource group on
            #   Azure is not found. That means the cluster is already deleted
            #   on the cloud. So it's safe & good UX to not print a failure
            #   message.
            elif ('TPU must be specified.' not in stderr and
                  'SKYPILOT_ERROR_NO_NODES_LAUNCHED: ' not in stderr and
                  '(ResourceGroupNotFound)' not in stderr):
                raise RuntimeError(
                    _TEARDOWN_FAILURE_MESSAGE.format(
                        extra_reason='',
                        cluster_name=handle.cluster_name,
                        stdout=stdout,
                        stderr=stderr))

        # No need to clean up if the cluster is already terminated
        # (i.e., prev_status is None), as the cleanup has already been done
        # if the cluster is removed from the status table.
        if post_teardown_cleanup:
            self.post_teardown_cleanup(handle, terminate, purge)

    def post_teardown_cleanup(self,
                              handle: CloudVmRayResourceHandle,
                              terminate: bool,
                              purge: bool = False) -> None:
        """Cleanup local configs/caches and delete TPUs after teardown.

        This method will handle the following cleanup steps:
        * Deleting the TPUs;
        * Removing ssh configs for the cluster;
        * Updating the local state of the cluster;
        * Removing the terminated cluster's scripts and ray yaml files.

        Raises:
            RuntimeError: If it fails to delete the TPU.
        """
        log_path = os.path.join(os.path.expanduser(self.log_dir),
                                'teardown.log')
        log_abs_path = os.path.abspath(log_path)

        if (handle.tpu_delete_script is not None and
                os.path.exists(handle.tpu_delete_script)):
            with log_utils.safe_rich_status('[bold cyan]Terminating TPU...'):
                tpu_rc, tpu_stdout, tpu_stderr = log_lib.run_with_log(
                    ['bash', handle.tpu_delete_script],
                    log_abs_path,
                    stream_logs=False,
                    require_outputs=True)
            if tpu_rc != 0:
                if _TPU_NOT_FOUND_ERROR in tpu_stderr:
                    logger.info('TPU not found. '
                                'It should have been deleted already.')
                elif purge:
                    logger.warning(
                        _TEARDOWN_PURGE_WARNING.format(
                            reason='stopping/terminating TPU',
                            details=tpu_stderr))
                else:
                    raise RuntimeError(
                        _TEARDOWN_FAILURE_MESSAGE.format(
                            extra_reason='It is caused by TPU failure.',
                            cluster_name=handle.cluster_name,
                            stdout=tpu_stdout,
                            stderr=tpu_stderr))
        if (terminate and handle.launched_resources.is_image_managed is True):
            # Delete the image when terminating a "cloned" cluster, i.e.,
            # whose image is created by SkyPilot (--clone-disk-from)
            logger.debug(f'Deleting image {handle.launched_resources.image_id}')
            cluster_resources = handle.launched_resources
            cluster_cloud = cluster_resources.cloud
            image_dict = cluster_resources.image_id
            assert cluster_cloud is not None, cluster_resources
            assert image_dict is not None and len(image_dict) == 1
            image_id = list(image_dict.values())[0]
            try:
                cluster_cloud.delete_image(image_id,
                                           handle.launched_resources.region)
            except exceptions.CommandError as e:
                logger.warning(
                    f'Failed to delete cloned image {image_id}. Please '
                    'remove it manually to avoid image leakage. Details: '
                    f'{common_utils.format_exception(e, use_bracket=True)}')

        # The cluster file must exist because the cluster_yaml will only
        # be removed after the cluster entry in the database is removed.
        config = common_utils.read_yaml(handle.cluster_yaml)
        auth_config = config['auth']
        backend_utils.SSHConfigHelper.remove_cluster(handle.cluster_name,
                                                     handle.head_ip,
                                                     auth_config)

        global_user_state.remove_cluster(handle.cluster_name,
                                         terminate=terminate)

        if terminate:
            # Clean up TPU creation/deletion scripts
            if handle.tpu_delete_script is not None:
                assert handle.tpu_create_script is not None
                common_utils.remove_file_if_exists(handle.tpu_create_script)
                common_utils.remove_file_if_exists(handle.tpu_delete_script)

            # Clean up generated config
            # No try-except is needed since Ray will fail to teardown the
            # cluster if the cluster_yaml is missing.
            common_utils.remove_file_if_exists(handle.cluster_yaml)

    def set_autostop(self,
                     handle: CloudVmRayResourceHandle,
                     idle_minutes_to_autostop: Optional[int],
                     down: bool = False,
                     stream_logs: bool = True) -> None:
        if idle_minutes_to_autostop is not None:
            code = autostop_lib.AutostopCodeGen.set_autostop(
                idle_minutes_to_autostop, self.NAME, down)
            returncode, _, stderr = self.run_on_head(handle,
                                                     code,
                                                     require_outputs=True,
                                                     stream_logs=stream_logs)
            subprocess_utils.handle_returncode(returncode,
                                               code,
                                               'Failed to set autostop',
                                               stderr=stderr,
                                               stream_logs=stream_logs)
            global_user_state.set_cluster_autostop_value(
                handle.cluster_name, idle_minutes_to_autostop, down)

    def is_definitely_autostopping(self,
                                   handle: CloudVmRayResourceHandle,
                                   stream_logs: bool = True) -> bool:
        """Check if the cluster is autostopping.

        Returns:
            True if the cluster is definitely autostopping. It is possible
            that the cluster is still autostopping when False is returned,
            due to errors like transient network issues.
        """
        if handle.head_ip is None:
            # The head node of the cluster is not UP or in an abnormal state.
            # We cannot check if the cluster is autostopping.
            return False
        code = autostop_lib.AutostopCodeGen.is_autostopping()
        returncode, stdout, stderr = self.run_on_head(handle,
                                                      code,
                                                      require_outputs=True,
                                                      stream_logs=stream_logs)

        if returncode == 0:
            return common_utils.decode_payload(stdout)
        logger.debug(f'Failed to check if cluster is autostopping: {stderr}')
        return False

    # TODO(zhwu): Refactor this to a CommandRunner class, so different backends
    # can support its own command runner.
    @timeline.event
    def run_on_head(
        self,
        handle: CloudVmRayResourceHandle,
        cmd: str,
        *,
        port_forward: Optional[List[int]] = None,
        log_path: str = '/dev/null',
        stream_logs: bool = False,
        ssh_mode: command_runner.SshMode = command_runner.SshMode.
        NON_INTERACTIVE,
        under_remote_workdir: bool = False,
        require_outputs: bool = False,
        separate_stderr: bool = False,
        process_stream: bool = True,
        **kwargs,
    ) -> Union[int, Tuple[int, str, str]]:
        """Runs 'cmd' on the cluster's head node.

        Args:
            handle: The ResourceHandle to the cluster.
            cmd: The command to run.

            Advanced options:

            port_forward: A list of ports to forward.
            log_path: The path to the log file.
            stream_logs: Whether to stream the logs to stdout/stderr.
            ssh_mode: The mode to use for ssh.
                See command_runner.SSHCommandRunner.SSHMode for more details.
            under_remote_workdir: Whether to run the command under the remote
                workdir ~/sky_workdir.
            require_outputs: Whether to return the stdout and stderr of the
                command.
            separate_stderr: Whether to separate stderr from stdout.
            process_stream: Whether to post-process the stdout/stderr of the
                command, such as replacing or skipping lines on the fly. If
                enabled, lines are printed only when '\r' or '\n' is found.
        """
        head_ip = backend_utils.get_head_ip(handle, _FETCH_IP_MAX_ATTEMPTS)
        ssh_credentials = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)
        runner = command_runner.SSHCommandRunner(head_ip, **ssh_credentials)
        if under_remote_workdir:
            cmd = f'cd {SKY_REMOTE_WORKDIR} && {cmd}'

        return runner.run(
            cmd,
            port_forward=port_forward,
            log_path=log_path,
            process_stream=process_stream,
            stream_logs=stream_logs,
            ssh_mode=ssh_mode,
            require_outputs=require_outputs,
            separate_stderr=separate_stderr,
            **kwargs,
        )

    # --- Utilities ---

    @timeline.event
    def _check_existing_cluster(
            self,
            task: task_lib.Task,
            to_provision: Optional[resources_lib.Resources],
            cluster_name: str,
            dryrun: bool = False) -> RetryingVmProvisioner.ToProvisionConfig:
        """Checks if the cluster exists and returns the provision config.

        Raises:
            exceptions.ResourcesMismatchError: If the resources in the task
                does not match the existing cluster.
            exceptions.InvalidClusterNameError: If the cluster name is invalid.
            # TODO(zhwu): complete the list of exceptions.
        """
        record = global_user_state.get_cluster_from_name(cluster_name)
        handle_before_refresh = None if record is None else record['handle']
        status_before_refresh = None if record is None else record['status']

        prev_cluster_status, handle = (status_before_refresh,
                                       handle_before_refresh)

        if not dryrun:
            prev_cluster_status, handle = (
                backend_utils.refresh_cluster_status_handle(
                    cluster_name,
                    # We force refresh for the init status to determine the
                    # actual state of a previous cluster in INIT state.
                    #
                    # This is important for the case, where an existing cluster
                    # is transitioned into INIT state due to key interruption
                    # during launching, with the following steps:
                    # (1) launch, after answering prompt immediately ctrl-c;
                    # (2) launch again.
                    # If we don't refresh the state of the cluster and reset it
                    # back to STOPPED, our failover logic will consider it as an
                    # abnormal cluster after hitting resources capacity limit on
                    # the cloud, and will start failover. This is not desired,
                    # because the user may want to keep the data on the disk of
                    # that cluster.
                    force_refresh_statuses={status_lib.ClusterStatus.INIT},
                    acquire_per_cluster_status_lock=False,
                ))
        if prev_cluster_status is not None:
            assert handle is not None
            # Cluster already exists.
            self.check_resources_fit_cluster(handle, task)
            # Use the existing cluster.
            assert handle.launched_resources is not None, (cluster_name, handle)
            return RetryingVmProvisioner.ToProvisionConfig(
                cluster_name,
                handle.launched_resources,
                handle.launched_nodes,
                prev_cluster_status=prev_cluster_status)
        usage_lib.messages.usage.set_new_cluster()
        assert len(task.resources) == 1, task.resources
        # Use the task_cloud, because the cloud in `to_provision` can be changed
        # later during the retry.
        resources = list(task.resources)[0]
        task_cloud = (resources.cloud
                      if resources.cloud is not None else clouds.Cloud)
        task_cloud.check_cluster_name_is_valid(cluster_name)

        if to_provision is None:
            # The cluster is recently terminated either by autostop or manually
            # terminated on the cloud. We should use the previously terminated
            # resources to provision the cluster.
            assert isinstance(
                handle_before_refresh, CloudVmRayResourceHandle), (
                    f'Trying to launch cluster {cluster_name!r} recently '
                    'terminated  on the cloud, but the handle is not a '
                    f'CloudVmRayResourceHandle ({handle_before_refresh}).')
            status_before_refresh_str = None
            if status_before_refresh is not None:
                status_before_refresh_str = status_before_refresh.value

            logger.info(
                f'The cluster {cluster_name!r} (status: '
                f'{status_before_refresh_str}) was not found on the cloud: it '
                'may be autodowned, manually terminated, or its launch never '
                'succeeded. Provisioning a new cluster by using the same '
                'resources as its original launch.')
            to_provision = handle_before_refresh.launched_resources
            self.check_resources_fit_cluster(handle_before_refresh, task)

        cloud = to_provision.cloud
        if isinstance(cloud, clouds.Local):
            # The field ssh_user is specified in the cluster config file.
            ssh_user = onprem_utils.get_local_cluster_config_or_error(
                cluster_name)['auth']['ssh_user']
            logger.info(f'{colorama.Fore.CYAN}Connecting to local cluster: '
                        f'{cluster_name!r} [Username: {ssh_user}].'
                        f'{colorama.Style.RESET_ALL}\n'
                        'Run `sky status` to see existing clusters.')
        else:
            logger.info(
                f'{colorama.Fore.CYAN}Creating a new cluster: "{cluster_name}" '
                f'[{task.num_nodes}x {to_provision}].'
                f'{colorama.Style.RESET_ALL}\n'
                'Tip: to reuse an existing cluster, '
                'specify --cluster (-c). '
                'Run `sky status` to see existing clusters.')
        return RetryingVmProvisioner.ToProvisionConfig(cluster_name,
                                                       to_provision,
                                                       task.num_nodes,
                                                       prev_cluster_status=None)

    def _set_tpu_name(self, handle: CloudVmRayResourceHandle,
                      tpu_name: str) -> None:
        """Sets TPU_NAME on all nodes."""
        ip_list = handle.external_ips()
        assert ip_list is not None, 'external_ips is not cached in handle'
        ssh_credentials = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)

        runners = command_runner.SSHCommandRunner.make_runner_list(
            ip_list, **ssh_credentials)

        def _setup_tpu_name_on_node(
                runner: command_runner.SSHCommandRunner) -> None:
            cmd = (f'[[ -z $TPU_NAME ]] && echo "export TPU_NAME={tpu_name}" '
                   '>> ~/.bashrc || echo "TPU_NAME already set"')
            returncode = runner.run(cmd,
                                    log_path=os.path.join(
                                        self.log_dir, 'tpu_setup.log'))
            subprocess_utils.handle_returncode(
                returncode, cmd, 'Failed to set TPU_NAME on node.')

        subprocess_utils.run_in_parallel(_setup_tpu_name_on_node, runners)

    def _execute_file_mounts(self, handle: CloudVmRayResourceHandle,
                             file_mounts: Dict[Path, Path]):
        """Executes file mounts.

        Rsyncing local files and copying from remote stores.
        """
        # File mounts handling for remote paths possibly without write access:
        #  (1) in 'file_mounts' sections, add <prefix> to these target paths.
        #  (2) then, create symlinks from '/.../file' to '<prefix>/.../file'.
        if file_mounts is None or not file_mounts:
            return
        symlink_commands = []
        fore = colorama.Fore
        style = colorama.Style
        logger.info(f'{fore.CYAN}Processing file mounts.{style.RESET_ALL}')
        start = time.time()
        ip_list = handle.external_ips()
        assert ip_list is not None, 'external_ips is not cached in handle'
        ssh_credentials = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)
        runners = command_runner.SSHCommandRunner.make_runner_list(
            ip_list, **ssh_credentials)
        log_path = os.path.join(self.log_dir, 'file_mounts.log')

        # Check the files and warn
        for dst, src in file_mounts.items():
            if not data_utils.is_cloud_store_url(src):
                full_src = os.path.abspath(os.path.expanduser(src))
                # Checked during Task.set_file_mounts().
                assert os.path.exists(full_src), f'{full_src} does not exist.'
                src_size = backend_utils.path_size_megabytes(full_src)
                if src_size >= _PATH_SIZE_MEGABYTES_WARN_THRESHOLD:
                    logger.warning(
                        f'{fore.YELLOW}The size of file mount src {src!r} '
                        f'is {src_size} MB. Try to keep src small or use '
                        '.gitignore to exclude large files, as large sizes '
                        f'will slow down rsync. {style.RESET_ALL}')
                if os.path.islink(full_src):
                    logger.warning(
                        f'{fore.YELLOW}Source path {src!r} is a symlink. '
                        f'Symlink contents are not uploaded.{style.RESET_ALL}')

        os.makedirs(os.path.expanduser(self.log_dir), exist_ok=True)
        os.system(f'touch {log_path}')
        tail_cmd = f'tail -n100 -f {log_path}'
        logger.info('To view detailed progress: '
                    f'{style.BRIGHT}{tail_cmd}{style.RESET_ALL}')

        for dst, src in file_mounts.items():
            # TODO: room for improvement.  Here there are many moving parts
            # (download gsutil on remote, run gsutil on remote).  Consider
            # alternatives (smart_open, each provider's own sdk), a
            # data-transfer container etc.
            if not os.path.isabs(dst) and not dst.startswith('~/'):
                dst = f'{SKY_REMOTE_WORKDIR}/{dst}'
            # Sync 'src' to 'wrapped_dst', a safe-to-write "wrapped" path.
            wrapped_dst = dst
            if not dst.startswith('~/') and not dst.startswith('/tmp/'):
                # Handles the remote paths possibly without write access.
                # (1) add <prefix> to these target paths.
                wrapped_dst = backend_utils.FileMountHelper.wrap_file_mount(dst)
                cmd = backend_utils.FileMountHelper.make_safe_symlink_command(
                    source=dst, target=wrapped_dst)
                symlink_commands.append(cmd)

            if not data_utils.is_cloud_store_url(src):
                full_src = os.path.abspath(os.path.expanduser(src))

                if os.path.isfile(full_src):
                    mkdir_for_wrapped_dst = (
                        f'mkdir -p {os.path.dirname(wrapped_dst)}')
                else:
                    mkdir_for_wrapped_dst = f'mkdir -p {wrapped_dst}'

                # TODO(mluo): Fix method so that mkdir and rsync run together
                backend_utils.parallel_data_transfer_to_nodes(
                    runners,
                    source=src,
                    target=wrapped_dst,
                    cmd=mkdir_for_wrapped_dst,
                    run_rsync=True,
                    action_message='Syncing',
                    log_path=log_path,
                    stream_logs=False,
                )
                continue

            storage = cloud_stores.get_storage_from_path(src)
            if storage.is_directory(src):
                sync = storage.make_sync_dir_command(source=src,
                                                     destination=wrapped_dst)
                # It is a directory so make sure it exists.
                mkdir_for_wrapped_dst = f'mkdir -p {wrapped_dst}'
            else:
                sync = storage.make_sync_file_command(source=src,
                                                      destination=wrapped_dst)
                # It is a file so make sure *its parent dir* exists.
                mkdir_for_wrapped_dst = (
                    f'mkdir -p {os.path.dirname(wrapped_dst)}')

            download_target_commands = [
                # Ensure sync can write to wrapped_dst (e.g., '/data/').
                mkdir_for_wrapped_dst,
                # Both the wrapped and the symlink dir exist; sync.
                sync,
            ]
            command = ' && '.join(download_target_commands)
            # dst is only used for message printing.
            backend_utils.parallel_data_transfer_to_nodes(
                runners,
                source=src,
                target=dst,
                cmd=command,
                run_rsync=False,
                action_message='Syncing',
                log_path=log_path,
                stream_logs=False,
            )
        # (2) Run the commands to create symlinks on all the nodes.
        symlink_command = ' && '.join(symlink_commands)
        if symlink_command:

            def _symlink_node(runner: command_runner.SSHCommandRunner):
                returncode = runner.run(symlink_command, log_path=log_path)
                subprocess_utils.handle_returncode(
                    returncode, symlink_command,
                    'Failed to create symlinks. The target destination '
                    f'may already exist. Log: {log_path}')

            subprocess_utils.run_in_parallel(_symlink_node, runners)
        end = time.time()
        logger.debug(f'File mount sync took {end - start} seconds.')

    def _execute_storage_mounts(self, handle: CloudVmRayResourceHandle,
                                storage_mounts: Dict[Path,
                                                     storage_lib.Storage]):
        """Executes storage mounts: installing mounting tools and mounting."""
        # Process only mount mode objects here. COPY mode objects have been
        # converted to regular copy file mounts and thus have been handled
        # in the '__execute_file_mounts' method.
        storage_mounts = {
            path: storage_mount
            for path, storage_mount in storage_mounts.items()
            if storage_mount.mode == storage_lib.StorageMode.MOUNT
        }

        if not storage_mounts:
            return

        cloud = handle.launched_resources.cloud
        # TODO(romil): Support Mounting for Local (remove sudo installation)
        if isinstance(cloud, clouds.Local):
            logger.warning(
                f'{colorama.Fore.YELLOW}Sky On-prem does not support '
                f'mounting. No action will be taken.{colorama.Style.RESET_ALL}')
            return

        fore = colorama.Fore
        style = colorama.Style
        plural = 's' if len(storage_mounts) > 1 else ''
        logger.info(f'{fore.CYAN}Processing {len(storage_mounts)} '
                    f'storage mount{plural}.{style.RESET_ALL}')
        start = time.time()
        ip_list = handle.external_ips()
        assert ip_list is not None, 'external_ips is not cached in handle'
        ssh_credentials = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)
        runners = command_runner.SSHCommandRunner.make_runner_list(
            ip_list, **ssh_credentials)
        log_path = os.path.join(self.log_dir, 'storage_mounts.log')

        for dst, storage_obj in storage_mounts.items():
            if not os.path.isabs(dst) and not dst.startswith('~/'):
                dst = f'{SKY_REMOTE_WORKDIR}/{dst}'
            # Get the first store and use it to mount
            store = list(storage_obj.stores.values())[0]
            mount_cmd = store.mount_command(dst)
            src_print = (storage_obj.source
                         if storage_obj.source else storage_obj.name)
            if isinstance(src_print, list):
                src_print = ', '.join(src_print)
            try:
                backend_utils.parallel_data_transfer_to_nodes(
                    runners,
                    source=src_print,
                    target=dst,
                    cmd=mount_cmd,
                    run_rsync=False,
                    action_message='Mounting',
                    log_path=log_path,
                )
            except exceptions.CommandError as e:
                if e.returncode == exceptions.MOUNT_PATH_NON_EMPTY_CODE:
                    mount_path = (f'{colorama.Fore.RED}'
                                  f'{colorama.Style.BRIGHT}{dst}'
                                  f'{colorama.Style.RESET_ALL}')
                    error_msg = (f'Mount path {mount_path} is non-empty.'
                                 f' {mount_path} may be a standard unix '
                                 f'path or may contain files from a previous'
                                 f' task. To fix, change the mount path'
                                 f' to an empty or non-existent path.')
                    raise RuntimeError(error_msg) from None
                else:
                    # Strip the command (a big heredoc) from the exception
                    raise exceptions.CommandError(
                        e.returncode, command='to mount',
                        error_msg=e.error_msg) from None

        end = time.time()
        logger.debug(f'Storage mount sync took {end - start} seconds.')

    def _execute_task_one_node(self, handle: CloudVmRayResourceHandle,
                               task: task_lib.Task, job_id: int,
                               detach_run: bool) -> None:
        # Launch the command as a Ray task.
        log_dir = os.path.join(self.log_dir, 'tasks')

        accelerator_dict = backend_utils.get_task_demands_dict(task)
        internal_ips = handle.internal_ips()
        assert internal_ips is not None, 'internal_ips is not cached in handle'

        codegen = RayCodeGen()
        is_local = isinstance(handle.launched_resources.cloud, clouds.Local)
        codegen.add_prologue(job_id, is_local=is_local)
        codegen.add_gang_scheduling_placement_group_and_setup(
            1,
            accelerator_dict,
            stable_cluster_internal_ips=internal_ips,
            setup_cmd=self._setup_cmd,
            setup_log_path=os.path.join(log_dir, 'setup.log'),
            envs=task.envs,
        )

        if callable(task.run):
            run_fn_code = textwrap.dedent(inspect.getsource(task.run))
            run_fn_name = task.run.__name__
            codegen.register_run_fn(run_fn_code, run_fn_name)

        # If it is a managed spot job, the TASK_ID_ENV_VAR will have been
        # already set by the controller.
        job_run_id = task.envs.get(
            constants.TASK_ID_ENV_VAR,
            common_utils.get_global_job_id(self.run_timestamp,
                                           cluster_name=handle.cluster_name,
                                           job_id=str(job_id)))

        command_for_node = task.run if isinstance(task.run, str) else None
        use_sudo = isinstance(handle.launched_resources.cloud, clouds.Local)
        codegen.add_ray_task(
            bash_script=command_for_node,
            env_vars=task.envs,
            task_name=task.name,
            job_run_id=job_run_id,
            ray_resources_dict=backend_utils.get_task_demands_dict(task),
            log_dir=log_dir,
            use_sudo=use_sudo)

        codegen.add_epilogue()

        self._exec_code_on_head(handle,
                                codegen.build(),
                                job_id,
                                executable='python3',
                                detach_run=detach_run,
                                spot_dag=task.spot_dag)

    def _execute_task_n_nodes(self, handle: CloudVmRayResourceHandle,
                              task: task_lib.Task, job_id: int,
                              detach_run: bool) -> None:
        # Strategy:
        #   ray.init(...)
        #   for node:
        #     submit _run_cmd(cmd) with resource {node_i: 1}
        log_dir_base = self.log_dir
        log_dir = os.path.join(log_dir_base, 'tasks')
        accelerator_dict = backend_utils.get_task_demands_dict(task)
        internal_ips = handle.internal_ips()
        assert internal_ips is not None, 'internal_ips is not cached in handle'

        # If TPU VM Pods is used, #num_nodes should be #num_tpu_devices
        is_tpu_vm_pod = tpu_utils.is_tpu_vm_pod(handle.launched_resources)
        if is_tpu_vm_pod:
            num_actual_nodes = tpu_utils.get_num_tpu_devices(
                handle.launched_resources)
        else:
            num_actual_nodes = task.num_nodes
        assert isinstance(num_actual_nodes, int), num_actual_nodes

        codegen = RayCodeGen()
        is_local = isinstance(handle.launched_resources.cloud, clouds.Local)
        codegen.add_prologue(job_id, is_local=is_local)
        codegen.add_gang_scheduling_placement_group_and_setup(
            num_actual_nodes,
            accelerator_dict,
            stable_cluster_internal_ips=internal_ips,
            setup_cmd=self._setup_cmd,
            setup_log_path=os.path.join(log_dir, 'setup.log'),
            envs=task.envs)

        if callable(task.run):
            run_fn_code = textwrap.dedent(inspect.getsource(task.run))
            run_fn_name = task.run.__name__
            codegen.register_run_fn(run_fn_code, run_fn_name)

        # If it is a managed spot job, the TASK_ID_ENV_VAR will have been
        # already set by the controller.
        job_run_id = task.envs.get(
            constants.TASK_ID_ENV_VAR,
            common_utils.get_global_job_id(self.run_timestamp,
                                           cluster_name=handle.cluster_name,
                                           job_id=str(job_id)))

        # TODO(zhwu): The resources limitation for multi-node ray.tune and
        # horovod should be considered.
        for i in range(num_actual_nodes):
            command_for_node = task.run if isinstance(task.run, str) else None

            # Ray's per-node resources, to constrain scheduling each command to
            # the corresponding node, represented by private IPs.
            use_sudo = isinstance(handle.launched_resources.cloud, clouds.Local)
            codegen.add_ray_task(
                bash_script=command_for_node,
                env_vars=task.envs,
                task_name=task.name,
                job_run_id=job_run_id,
                ray_resources_dict=accelerator_dict,
                log_dir=log_dir,
                gang_scheduling_id=i,
                use_sudo=use_sudo,
            )

        codegen.add_epilogue()
        # TODO(zhanghao): Add help info for downloading logs.
        self._exec_code_on_head(handle,
                                codegen.build(),
                                job_id,
                                executable='python3',
                                detach_run=detach_run,
                                spot_dag=task.spot_dag)
