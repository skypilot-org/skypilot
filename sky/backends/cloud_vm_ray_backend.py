"""Backend: runs on cloud virtual machines, managed by Ray."""
import ast
import click
import contextlib
import enum
import getpass
import hashlib
import inspect
import json
import os
import re
import sys
import subprocess
import tempfile
import textwrap
import time
from typing import Dict, List, Optional, Tuple, Union

import colorama
import filelock
from rich import console as rich_console

import sky
from sky import backends
from sky import clouds
from sky import cloud_stores
from sky import dag as dag_lib
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import optimizer
from sky import resources as resources_lib
from sky import task as task_lib
from sky.backends import backend_utils
from sky.skylet import job_lib, log_lib

Dag = dag_lib.Dag
OptimizeTarget = optimizer.OptimizeTarget
Path = str
Resources = resources_lib.Resources
Task = task_lib.Task

SKY_REMOTE_APP_DIR = backend_utils.SKY_REMOTE_APP_DIR
SKY_REMOTE_WORKDIR = backend_utils.SKY_REMOTE_WORKDIR
SKY_LOGS_DIRECTORY = job_lib.SKY_LOGS_DIRECTORY
SKY_REMOTE_RAY_VERSION = backend_utils.SKY_REMOTE_RAY_VERSION
SKYLET_REMOTE_PATH = backend_utils.SKY_REMOTE_PATH

logger = sky_logging.init_logger(__name__)
console = rich_console.Console()

_PATH_SIZE_MEGABYTES_WARN_THRESHOLD = 256

# Timeout for provision a cluster and wait for it to be ready in seconds.
_NODES_LAUNCHING_PROGRESS_TIMEOUT = 30

_LOCK_FILENAME = '~/.sky/.{}.lock'
_FILELOCK_TIMEOUT_SECONDS = 10


def _check_cluster_name_is_valid(cluster_name: str) -> None:
    """Errors out on invalid cluster names not supported by cloud providers.

    Bans (including but not limited to) names that:
    - are digits-only
    - contain underscore (_)
    """
    if cluster_name is None:
        return
    # GCP errors return this exact regex.  An informal description is also at:
    # https://cloud.google.com/compute/docs/naming-resources#resource-name-format
    valid_regex = '[a-z]([-a-z0-9]{0,61}[a-z0-9])?'
    if re.fullmatch(valid_regex, cluster_name) is None:
        raise ValueError(f'Cluster name "{cluster_name}" is invalid; '
                         f'ensure it is fully matched by regex: {valid_regex}')


def _get_cluster_config_template(cloud):
    cloud_to_template = {
        clouds.AWS: 'aws-ray.yml.j2',
        clouds.Azure: 'azure-ray.yml.j2',
        clouds.GCP: 'gcp-ray.yml.j2',
    }
    return cloud_to_template[type(cloud)]


def _get_task_demands_dict(task: Task) -> Optional[Tuple[Optional[str], int]]:
    """Returns the accelerator dict of the task"""
    # TODO: CPU and other memory resources are not supported yet.
    accelerator_dict = None
    if task.best_resources is not None:
        resources = task.best_resources
    else:
        # Task may (e.g., sky launch) or may not (e.g., sky exec) have undergone
        # sky.optimize(), so best_resources may be None.
        assert len(task.resources) == 1, task.resources
        resources = list(task.resources)[0]
    if resources is not None:
        accelerator_dict = resources.accelerators
    return accelerator_dict


def _path_size_megabytes(path: str, exclude_gitignore: bool = False) -> int:
    """Returns the size of 'path' (directory or file) in megabytes.

    Args:
        path: The path to check.
        exclude_gitignore: If True, excludes files matched in .gitignore.

    Returns:
        The size of 'path' in megabytes.
    """
    if exclude_gitignore:
        try:
            # FIXME: add git index size (du -hsc .git) in this computation.
            awk_program = '{ sum += $1 } END { print sum }'
            return int(
                subprocess.check_output(
                    f'( git status --short {path} | '
                    'grep "^?" | cut -d " " -f2- '
                    f'&& git ls-files {path} ) | '
                    'xargs -n 1000 du -hsk | '
                    f'awk {awk_program!r}',
                    shell=True,
                    stderr=subprocess.DEVNULL)) // (2**10)
        except (subprocess.CalledProcessError, ValueError):
            # If git is not installed, or if the user is not in a git repo.
            # Fall back to du -shk if it is not a git repo (size does not
            # consider .gitignore).
            logger.debug('Failed to get size with .gitignore exclusion, '
                         'falling back to du -shk')
            pass
    return int(
        subprocess.check_output(['du', '-sh', '-k', path
                                ]).split()[0].decode('utf-8')) // (2**10)


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
        #   job_codegen = backend_utils.JobLibCodeGen()
        #   job_codegen.add_job(username, run_timestamp)
        #   code = job_codegen.build()
        #   job_id = get_output(run_on_cluster(code))
        self.job_id = None

    def add_prologue(self, job_id: int) -> None:
        assert not self._has_prologue, 'add_prologue() called twice?'
        self._has_prologue = True
        self.job_id = job_id
        self._code = [
            textwrap.dedent(f"""\
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

            from sky.skylet import job_lib

            SKY_REMOTE_WORKDIR = {log_lib.SKY_REMOTE_WORKDIR!r}
            job_lib.set_status({job_id!r}, job_lib.JobStatus.PENDING)

            ray.init('auto', namespace='__sky__{job_id}__', log_to_driver=True)

            run_fn = None
            futures = []"""),
            # FIXME: This is a hack to make sure that the functions can be found
            # by ray.remote. This should be removed once we have a better way to
            # specify dependencies for ray.
            inspect.getsource(log_lib.redirect_process_output),
            inspect.getsource(log_lib.run_with_log),
            inspect.getsource(log_lib.make_task_bash_script),
            inspect.getsource(log_lib.run_bash_command_with_log),
            'run_bash_command_with_log = ray.remote(run_bash_command_with_log)',
        ]

    def add_gang_scheduling_placement_group(
        self,
        num_nodes: int,
        accelerator_dict: Dict[str, int],
    ) -> None:
        """Create the gang scheduling placement group for a Task."""
        assert self._has_prologue, ('Call add_prologue() before '
                                    'add_gang_scheduling_placement_group().')
        self._has_gang_scheduling = True
        self._num_nodes = num_nodes

        # Set CPU to avoid ray hanging the resources allocation
        # for remote functions, since the task will request 1 CPU
        # by default.
        bundles = [{'CPU': 0.001} for _ in range(num_nodes)]

        if accelerator_dict is not None:
            acc_name = list(accelerator_dict.keys())[0]
            acc_count = list(accelerator_dict.values())[0]
            gpu_dict = {'GPU': acc_count}
            # gpu_dict should be empty when the accelerator is not GPU.
            # FIXME: This is a hack to make sure that we do not reserve
            # GPU when requesting TPU.
            if 'tpu' in acc_name.lower():
                gpu_dict = dict()
            for bundle in bundles:
                bundle.update({
                    **accelerator_dict,
                    # Set the GPU to avoid ray hanging the resources allocation
                    **gpu_dict,
                })

        pack_mode = 'STRICT_SPREAD'
        self._code += [
            textwrap.dedent(f"""\
                pg = ray_util.placement_group({json.dumps(bundles)}, {pack_mode!r})
                plural = 's' if {num_nodes} > 1 else ''
                node_str = f'{num_nodes} node' + plural + '.'
                print('SKY INFO: Reserving task slots on ' + node_str,
                      file=sys.stderr,
                      flush=True)
                # FIXME: This will print the error message from autoscaler if
                # it is waiting for other task to finish. We should hide the
                # error message.
                ray.get(pg.ready())
                print('SKY INFO: All task slots reserved.',
                      file=sys.stderr,
                      flush=True)
                job_lib.set_job_started({self.job_id!r})
                export_sky_env_vars = ''
                """)
        ]

        # Export IP and node rank to the environment variables.
        self._code += [
            textwrap.dedent("""\
                @ray.remote
                def check_ip():
                    return ray.util.get_node_ip_address()
                ip_list = ray.get([
                    check_ip.options(num_cpus=0,
                                     placement_group=pg,
                                     placement_group_bundle_index=i).remote()
                    for i in range(pg.bundle_count)
                ])
                print('SKY INFO: Reserved IPs:', ip_list)
                ip_list_str = '\\n'.join(ip_list)
                export_sky_env_vars = 'export SKY_NODE_IPS="' + ip_list_str + '"\\n'
                """),
        ]

    def register_run_fn(self, run_fn: str, run_fn_name: str) -> None:
        """Register the run function to be run on the remote cluster.

        Args:
            run_fn: The run function to be run on the remote cluster.
        """
        assert self._has_gang_scheduling, (
            'Call add_gang_scheduling_placement_group() '
            'before register_run_fn().')
        assert not self._has_register_run_fn, (
            'register_run_fn() called twice?')
        self._has_register_run_fn = True

        self._code += [
            run_fn,
            f'run_fn = {run_fn_name}',
        ]

    def add_ray_task(
        self,
        bash_script: str,
        task_name: Optional[str],
        ray_resources_dict: Optional[Dict[str, float]],
        log_path: str,
        gang_scheduling_id: int = 0,
    ) -> None:
        """Generates code for a ray remote task that runs a bash command."""
        assert self._has_gang_scheduling, (
            'Call add_gang_schedule_placement_group() before add_ray_task().')
        assert (not self._has_register_run_fn or
                bash_script is None), ('bash_script should '
                                       'be None when run_fn is registered.')
        # Build remote_task.options(...)
        #   name=...
        #   resources=...
        #   num_gpus=...
        name_str = f'name=\'{task_name}\''
        if task_name is None:
            # Make the task name more meaningful in ray log.
            name_str = 'name=\'task\''
        cpu_str = ', num_cpus=0'

        resources_str = ''
        num_gpus_str = ''
        if ray_resources_dict is not None:
            assert len(ray_resources_dict) == 1, \
                ('There can only be one type of accelerator per instance.'
                 f' Found: {ray_resources_dict}.')
            resources_str = f', resources={json.dumps(ray_resources_dict)}'

            # Passing this ensures that the Ray remote task gets
            # CUDA_VISIBLE_DEVICES set correctly.  If not passed, that flag
            # would be force-set to empty by Ray.
            num_gpus_str = f', num_gpus={list(ray_resources_dict.values())[0]}'
            # `num_gpus` should be empty when the accelerator is not GPU.
            # FIXME: use a set of GPU types.
            resources_key = list(ray_resources_dict.keys())[0]
            if 'tpu' in resources_key.lower():
                num_gpus_str = ''

        resources_str = ', placement_group=pg'
        resources_str += f', placement_group_bundle_index={gang_scheduling_id}'
        logger.debug('Added Task with options: '
                     f'{name_str}{cpu_str}{resources_str}{num_gpus_str}')
        self._code += [
            textwrap.dedent(f"""\
        script = {bash_script!r}
        if run_fn is not None:
            script = run_fn({gang_scheduling_id}, ip_list)

        if script is not None:
            node_export_sky_env_vars = (export_sky_env_vars +
                                        'export SKY_NODE_RANK={gang_scheduling_id}\\n')
            futures.append(run_bash_command_with_log \\
                    .options({name_str}{cpu_str}{resources_str}{num_gpus_str}) \\
                    .remote(
                        script,
                        {log_path!r},
                        export_sky_env_vars=node_export_sky_env_vars,
                        stream_logs=True,
                        with_ray=True,
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
                time.sleep(1)
                print('SKY INFO: {colorama.Fore.RED}Job {self.job_id} failed with '
                      'return code list:{colorama.Style.RESET_ALL}',
                      returncodes,
                      file=sys.stderr,
                      flush=True)
            else:
                job_lib.set_status({self.job_id!r}, job_lib.JobStatus.SUCCEEDED)
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

        def __init__(self,
                     cluster_name: str,
                     resources: Resources,
                     num_nodes: int,
                     cluster_exists: bool = False) -> None:
            assert cluster_name is not None, 'cluster_name must be specified.'
            self.cluster_name = cluster_name
            self.resources = resources
            self.num_nodes = num_nodes
            # Whether the cluster exists in the clusters table. It may be
            # actually live or down.
            self.cluster_exists = cluster_exists

    class GangSchedulingStatus(enum.Enum):
        """Enum for gang scheduling status."""
        CLUSTER_READY = 0
        GANG_FAILED = 1
        HEAD_FAILED = 2

    def __init__(self, log_dir: str, dag: Dag, optimize_target: OptimizeTarget):
        self._blocked_regions = set()
        self._blocked_zones = set()
        self._blocked_launchable_resources = set()

        self.log_dir = os.path.expanduser(log_dir)
        self._dag = dag
        self._optimize_target = optimize_target

        colorama.init()

    def _in_blocklist(self, cloud, region, zones):
        if region.name in self._blocked_regions:
            return True
        # We do not keep track of zones in Azure.
        if isinstance(cloud, clouds.Azure):
            return False
        assert zones, (cloud, region, zones)
        for zone in zones:
            if zone.name not in self._blocked_zones:
                return False
        return True

    def _clear_blocklist(self):
        self._blocked_regions.clear()
        self._blocked_zones.clear()

    def _update_blocklist_on_gcp_error(self, region, zones, stdout, stderr):
        style = colorama.Style
        assert len(zones) == 1, zones
        zone = zones[0]
        splits = stderr.split('\n')
        exception_str = [s for s in splits if s.startswith('Exception: ')]
        if len(exception_str) == 1:
            # Parse structured response {'errors': [...]}.
            exception_str = exception_str[0][len('Exception: '):]
            exception_dict = ast.literal_eval(exception_str)
            for error in exception_dict['errors']:
                code = error['code']
                message = error['message']
                logger.warning(f'Got {code} in {zone.name} '
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
                        for r, _ in clouds.GCP.region_zones_provision_loop():
                            self._blocked_regions.add(r.name)
                    else:
                        # Per region.  Ex: Quota 'CPUS' exceeded.  Limit: 24.0
                        # in region us-west1.
                        self._blocked_regions.add(region.name)
                elif code == 'ZONE_RESOURCE_POOL_EXHAUSTED':  # Per zone.
                    self._blocked_zones.add(zone.name)
                else:
                    assert False, error
        else:
            # No such structured error response found.
            assert not exception_str, stderr
            if 'was not found' in stderr:
                # Example: The resource
                # 'projects/<id>/zones/zone/acceleratorTypes/nvidia-tesla-v100'
                # was not found.
                logger.warning(f'Got \'resource not found\' in {zone.name}.')
                self._blocked_zones.add(zone.name)
            else:
                logger.info('====== stdout ======')
                for s in stdout.split('\n'):
                    print(s)
                logger.info('====== stderr ======')
                for s in splits:
                    print(s)
                raise RuntimeError('Errors occurred during provision; '
                                   'check logs above.')

    def _update_blocklist_on_aws_error(self, region, zones, stdout, stderr):
        del zones  # Unused.
        style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            if 'An error occurred' in s.strip()
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
            # TODO: Got transient 'Failed to create security group' that goes
            # away after a few minutes.  Should we auto retry other regions, or
            # let the user retry.
            logger.info('====== stdout ======')
            for s in stdout_splits:
                print(s)
            logger.info('====== stderr ======')
            for s in stderr_splits:
                print(s)
            raise RuntimeError('Errors occurred during provision; '
                               'check logs above.')
        # The underlying ray autoscaler / boto3 will try all zones of a region
        # at once.
        logger.warning(f'Got error(s) in all zones of {region.name}:')
        messages = '\n\t'.join(errors)
        logger.warning(f'{style.DIM}\t{messages}{style.RESET_ALL}')
        self._blocked_regions.add(region.name)

    def _update_blocklist_on_azure_error(self, region, zones, stdout, stderr):
        del zones  # Unused.
        # The underlying ray autoscaler will try all zones of a region at once.
        style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            if ('Exception Details:' in s.strip() or
                'InvalidTemplateDeployment' in s.strip())
        ]
        if not errors:
            logger.info('====== stdout ======')
            for s in stdout_splits:
                print(s)
            logger.info('====== stderr ======')
            for s in stderr_splits:
                print(s)
            raise RuntimeError('Errors occurred during provision; '
                               'check logs above.')

        logger.warning(f'Got error(s) in {region.name}:')
        messages = '\n\t'.join(errors)
        logger.warning(f'{style.DIM}\t{messages}{style.RESET_ALL}')
        self._blocked_regions.add(region.name)

    def _update_blocklist_on_error(self, cloud, region, zones, stdout,
                                   stderr) -> None:
        """Handles cloud-specific errors and updates the block list.

        This parses textual stdout/stderr because we don't directly use the
        underlying clouds' SDKs.  If we did that, we could catch proper
        exceptions instead.
        """
        if stdout is None:
            # Gang scheduling failure.  Simply block the region.
            assert stderr is None, stderr
            self._blocked_regions.add(region.name)
            return

        if isinstance(cloud, clouds.GCP):
            return self._update_blocklist_on_gcp_error(region, zones, stdout,
                                                       stderr)

        if isinstance(cloud, clouds.AWS):
            return self._update_blocklist_on_aws_error(region, zones, stdout,
                                                       stderr)

        if isinstance(cloud, clouds.Azure):
            return self._update_blocklist_on_azure_error(
                region, zones, stdout, stderr)
        assert False, f'Unknown cloud: {cloud}.'

    def _yield_region_zones(self, to_provision: Resources, cluster_name: str,
                            cluster_exists: bool):
        cloud = to_provision.cloud
        region = None
        zones = None
        # Try loading previously launched region/zones and try them first,
        # because we may have an existing cluster there.
        handle = global_user_state.get_handle_from_cluster_name(cluster_name)
        if handle is not None:
            try:
                config = backend_utils.read_yaml(handle.cluster_yaml)
                prev_resources = handle.launched_resources

                if prev_resources is not None and cloud.is_same_cloud(
                        prev_resources.cloud):
                    if type(cloud) in (clouds.AWS, clouds.GCP):
                        region = config['provider']['region']
                        zones = config['provider']['availability_zone']
                    elif isinstance(cloud, clouds.Azure):
                        region = config['provider']['location']
                        zones = None
                    else:
                        assert False, cloud
            except FileNotFoundError:
                # Happens if no previous cluster.yaml exists.
                pass
        if region is not None and cluster_exists:

            region = clouds.Region(name=region)
            if zones is not None:
                zones = [clouds.Zone(name=zone) for zone in zones.split(',')]
                region.set_zones(zones)
            # Get the *previous* cluster status.
            cluster_status = global_user_state.get_status_from_cluster_name(
                cluster_name)
            logger.info(
                f'Cluster {cluster_name!r} (status: {cluster_status.value}) '
                f'was previously launched in {cloud} ({region.name}). '
                'Relaunching in that region.')
            # TODO(zhwu): The cluster being killed by cloud provider should
            # be tested whether re-launching a cluster killed spot instance
            # will recover the data.
            yield (region, zones)  # Ok to yield again in the next loop.

            # Cluster with status UP can reach here, if it was killed by the
            # cloud provider and no available resources in that region to
            # relaunch, which can happen to spot instance.
            if cluster_status == global_user_state.ClusterStatus.UP:
                message = (
                    f'Failed to connect to the cluster {cluster_name!r}. '
                    'It is possibly killed by cloud provider or manually '
                    'in the cloud provider console. To remove the cluster '
                    f'please run: sky down {cluster_name}')
                logger.error(message)
                # Reset to UP (rather than keeping it at INIT), as INIT
                # mode will enable failover to other regions, causing
                # data lose.
                # TODO(zhwu): This is set to UP to be more conservative,
                # we may need to confirm whether the cluster is down in all
                # cases.
                global_user_state.set_cluster_status(
                    cluster_name, global_user_state.ClusterStatus.UP)

                raise exceptions.ResourcesUnavailableError(message,
                                                           no_retry=True)

            # If it reaches here: the cluster status gets set to INIT, since
            # a launch request was issued but failed.
            #
            # Check the *previous* cluster status. If the cluster is previously
            # stopped, we should not retry other regions, since the previously
            # attached volumes are not visible on another region.
            if cluster_status == global_user_state.ClusterStatus.STOPPED:
                message = (
                    'Failed to acquire resources to restart the stopped '
                    f'cluster {cluster_name} on {region}. Please retry again '
                    'later.')
                logger.error(message)

                # Reset to STOPPED (rather than keeping it at INIT), because
                # (1) the cluster is not up (2) it ensures future `sky start`
                # will disable auto-failover too.
                global_user_state.set_cluster_status(
                    cluster_name, global_user_state.ClusterStatus.STOPPED)

                raise exceptions.ResourcesUnavailableError(message,
                                                           no_retry=True)

            assert cluster_status == global_user_state.ClusterStatus.INIT
            message = (f'Failed to launch cluster {cluster_name!r} '
                       f'(previous status: {cluster_status.value})'
                       f'with the original resources: {to_provision}.')
            logger.error(message)
            # We attempted re-launching a previously INIT cluster with the
            # same cloud/region/resources, but failed. Here no_retry=False,
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
            raise exceptions.ResourcesUnavailableError()

        for region, zones in cloud.region_zones_provision_loop(
                instance_type=to_provision.instance_type,
                accelerators=to_provision.accelerators,
                use_spot=to_provision.use_spot,
        ):
            yield (region, zones)

    def _try_provision_tpu(self, to_provision: Resources,
                           config_dict: Dict[str, str]) -> bool:
        """Returns whether the provision is successful."""
        tpu_name = config_dict['tpu_name']
        assert 'tpu-create-script' in config_dict, \
            'Expect TPU provisioning with gcloud.'
        try:
            with console.status('[bold cyan]Provisioning TPU '
                                f'[green]{tpu_name}[/]'):
                backend_utils.run(f'bash {config_dict["tpu-create-script"]}',
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
                logger.warning(
                    f'  TPU {tpu_name} creation failed due to quota '
                    'exhaustion. Please visit '
                    'https://console.cloud.google.com/iam-admin/quotas '
                    'for more  information.')
                raise exceptions.ResourcesUnavailableError()

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

    def _retry_region_zones(self,
                            to_provision: Resources,
                            num_nodes: int,
                            dryrun: bool,
                            stream_logs: bool,
                            cluster_name: str,
                            cluster_exists: bool = False):
        """The provision retry loop."""
        style = colorama.Style
        fore = colorama.Fore
        # Get log_path name
        log_path = os.path.join(self.log_dir, 'provision.log')
        log_abs_path = os.path.abspath(log_path)
        tail_cmd = f'tail -n100 -f {log_path}'
        logger.info('To view detailed progress: '
                    f'{style.BRIGHT}{tail_cmd}{style.RESET_ALL}')

        # Get previous cluster status
        prev_cluster_status = global_user_state.get_status_from_cluster_name(
            cluster_name)

        self._clear_blocklist()
        for region, zones in self._yield_region_zones(to_provision,
                                                      cluster_name,
                                                      cluster_exists):
            if self._in_blocklist(to_provision.cloud, region, zones):
                continue
            zone_str = ','.join(
                z.name for z in zones) if zones is not None else 'all zones'
            config_dict = backend_utils.write_cluster_config(
                to_provision,
                num_nodes,
                _get_cluster_config_template(to_provision.cloud),
                region=region,
                zones=zones,
                dryrun=dryrun,
                cluster_name=cluster_name)
            if dryrun:
                return
            tpu_name = config_dict.get('tpu_name')
            if tpu_name is not None:
                logger.info(
                    f'{colorama.Style.BRIGHT}Provisioning TPU on '
                    f'{to_provision.cloud} '
                    f'{region.name}{colorama.Style.RESET_ALL} ({zone_str})')

                success = self._try_provision_tpu(to_provision, config_dict)
                if not success:
                    continue
            cluster_config_file = config_dict['ray']

            # Record early, so if anything goes wrong, 'sky status' will show
            # the cluster name and users can appropriately 'sky down'.  It also
            # means a second 'sky launch -c <name>' will attempt to reuse.
            handle = CloudVmRayBackend.ResourceHandle(
                cluster_name=cluster_name,
                cluster_yaml=cluster_config_file,
                launched_nodes=num_nodes,
                # OK for this to be shown in CLI as status == INIT.
                launched_resources=to_provision,
                tpu_create_script=config_dict.get('tpu-create-script'),
                tpu_delete_script=config_dict.get('tpu-delete-script'))
            global_user_state.add_or_update_cluster(cluster_name,
                                                    cluster_handle=handle,
                                                    ready=False)
            logging_info = {
                'cluster_name': cluster_name,
                'region_name': region.name,
                'zone_str': zone_str,
            }
            status, stdout, stderr = self._gang_schedule_ray_up(
                to_provision.cloud, num_nodes, cluster_config_file,
                log_abs_path, stream_logs, logging_info)

            # The cluster is not ready.
            if status == self.GangSchedulingStatus.CLUSTER_READY:
                # However, ray processes may not be up due to 'ray up
                # --no-restart' flag.  Ensure so.
                self._ensure_cluster_ray_started(handle, log_abs_path)

                cluster_name = config_dict['cluster_name']
                plural = '' if num_nodes == 1 else 's'
                logger.info(f'{fore.GREEN}Successfully provisioned or found'
                            f' existing VM{plural}.{style.RESET_ALL}')
                return config_dict

            # If cluster was previously UP or STOPPED, stop it; otherwise
            # terminate.
            need_terminate = prev_cluster_status not in [
                global_user_state.ClusterStatus.STOPPED,
                global_user_state.ClusterStatus.UP
            ]
            if status == self.GangSchedulingStatus.HEAD_FAILED:
                # ray up failed for the head node.
                self._update_blocklist_on_error(to_provision.cloud, region,
                                                zones, stdout, stderr)
            else:
                assert status == self.GangSchedulingStatus.GANG_FAILED, status
                # gang scheduling failed.

                logger.error('*** Failed provisioning the cluster. ***')
                # The stdout/stderr of ray up is not useful here, since
                # head node is successfully provisioned.
                self._update_blocklist_on_error(
                    to_provision.cloud,
                    region,
                    # Ignored and block region:
                    zones=None,
                    stdout=None,
                    stderr=None)
                # Only log the error message for gang scheduling failure, since
                # head_fail may not create any resources.
                terminate_str = 'Terminating' if need_terminate else 'Stopping'
                logger.error(f'*** {terminate_str} the failed cluster. ***')

                # There may exists partial nodes (e.g., head node) so we must
                # terminate before moving on to other regions or stop.
                # FIXME(zongheng): terminating a potentially live cluster
                # is scary.  Say: users have an existing cluster, do sky
                # launch, gang failed, then we are terminating it here.
                CloudVmRayBackend().teardown(handle,
                                             terminate=need_terminate,
                                             _force=True)

        message = ('Failed to acquire resources in all regions/zones'
                   f' (requested {to_provision}).'
                   ' Try changing resource requirements or use another cloud.')
        logger.error(message)
        raise exceptions.ResourcesUnavailableError()

    def _gang_schedule_ray_up(
            self, to_provision_cloud: clouds.Cloud, num_nodes: int,
            cluster_config_file: str, log_abs_path: str, stream_logs: bool,
            logging_info: dict) -> Tuple[GangSchedulingStatus, str, str]:
        """Provisions a cluster via 'ray up' and wait until fully provisioned.

        Returns:
          (GangSchedulingStatus; stdout; stderr).
        """
        # FIXME(zhwu,zongheng): ray up on multiple nodes ups the head node then
        # waits for all workers; turn it into real gang scheduling.
        # FIXME: refactor code path to remove use of stream_logs
        del stream_logs

        style = colorama.Style

        def ray_up(start_streaming_at):
            # Redirect stdout/err to the file and streaming (if stream_logs).
            # With stdout/err redirected, 'ray up' will have no color and
            # different order from directly running in the console. The
            # `--log-style` and `--log-color` flags do not work. To reproduce,
            # `ray up --log-style pretty --log-color true | tee tmp.out`.
            returncode, stdout, stderr = log_lib.run_with_log(
                # NOTE: --no-restart solves the following bug.  Without it, if
                # 'ray up' (sky launch) twice on a cluster with >1 node, the
                # worker node gets disconnected/killed by ray autoscaler; the
                # whole task will just freeze.  (Doesn't affect 1-node
                # clusters.)  With this flag, ray processes no longer restart
                # and this bug doesn't show.  Downside is existing tasks on the
                # cluster will keep running (which may be ok with the semantics
                # of 'sky launch' twice).
                # Tracked in https://github.com/ray-project/ray/issues/20402.
                ['ray', 'up', '-y', '--no-restart', cluster_config_file],
                log_abs_path,
                stream_logs=False,
                start_streaming_at=start_streaming_at,
                parse_ray_up_logs=True,
                # Reduce BOTO_MAX_RETRIES from 12 to 5 to avoid long hanging
                # time during 'ray up' if insufficient capacity occurs.
                env=dict(os.environ, BOTO_MAX_RETRIES='5'),
                require_outputs=True)
            return returncode, stdout, stderr

        config = backend_utils.read_yaml(cluster_config_file)
        file_mounts = config['file_mounts']
        if 'ssh_public_key' in config['auth']:
            # For Azure, we need to add ssh public key to VM by filemounts.
            public_key_path = config['auth']['ssh_public_key']
            file_mounts[public_key_path] = public_key_path

        region_name = logging_info['region_name']
        zone_str = logging_info['zone_str']

        logger.info(f'{colorama.Style.BRIGHT}Launching on {to_provision_cloud} '
                    f'{region_name}{colorama.Style.RESET_ALL} ({zone_str})')
        start = time.time()
        returncode, stdout, stderr = ray_up(
            start_streaming_at='Shared connection to')
        logger.debug(f'Ray up takes {time.time() - start} seconds.')

        # Only 1 node or head node provisioning failure.
        if num_nodes == 1 and returncode == 0:
            return self.GangSchedulingStatus.CLUSTER_READY, stdout, stderr
        if returncode != 0:
            return self.GangSchedulingStatus.HEAD_FAILED, stdout, stderr

        logger.info(f'{style.BRIGHT}Successfully provisioned or found'
                    f' existing head VM. Waiting for workers.{style.RESET_ALL}')

        # FIXME(zongheng): the below requires ray processes are up on head. To
        # repro it failing: launch a 2-node cluster, log into head and ray
        # stop, then launch again.
        cluster_ready = backend_utils.wait_until_ray_cluster_ready(
            cluster_config_file,
            num_nodes,
            log_path=log_abs_path,
            nodes_launching_progress_timeout=_NODES_LAUNCHING_PROGRESS_TIMEOUT)
        if cluster_ready:
            cluster_status = self.GangSchedulingStatus.CLUSTER_READY
        else:
            cluster_status = self.GangSchedulingStatus.GANG_FAILED
        # Do not need stdout/stderr if gang scheduling failed.
        # gang_succeeded = False, if head OK, but workers failed.
        return cluster_status, '', ''

    def _ensure_cluster_ray_started(self,
                                    handle: 'CloudVmRayBackend.ResourceHandle',
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
            handle,
            'ray status',
            # At this state, an erroneous cluster may not have cached
            # handle.head_ip (global_user_state.add_or_update_cluster(...,
            # ready=True)).
            use_cached_head_ip=False)
        if returncode == 0:
            return
        backend.run_on_head(handle, 'ray stop', use_cached_head_ip=False)
        log_lib.run_with_log(
            ['ray', 'up', '-y', '--restart-only', handle.cluster_yaml],
            log_abs_path,
            stream_logs=True)

    def provision_with_retries(
        self,
        task: Task,
        to_provision_config: ToProvisionConfig,
        dryrun: bool,
        stream_logs: bool,
    ):
        """Provision with retries for all launchable resources."""
        cluster_name = to_provision_config.cluster_name
        assert cluster_name is not None, 'cluster_name must be specified.'
        to_provision = to_provision_config.resources
        num_nodes = to_provision_config.num_nodes
        cluster_exists = to_provision_config.cluster_exists
        launchable_retries_disabled = (self._dag is None or
                                       self._optimize_target is None)

        style = colorama.Style
        # Retrying launchable resources.
        provision_failed = True
        while provision_failed:
            provision_failed = False
            try:
                config_dict = self._retry_region_zones(
                    to_provision,
                    num_nodes,
                    dryrun=dryrun,
                    stream_logs=stream_logs,
                    cluster_name=cluster_name,
                    cluster_exists=cluster_exists)
                if dryrun:
                    return
                config_dict['launched_resources'] = to_provision
                config_dict['launched_nodes'] = num_nodes
            except exceptions.ResourcesUnavailableError as e:
                if e.no_retry:
                    raise e
                if launchable_retries_disabled:
                    logger.warning(
                        'DAG and optimize_target needs to be registered first '
                        'to enable cross-cloud retry. '
                        'To fix, call backend.register_info(dag=dag, '
                        'optimize_target=sky.OptimizeTarget.COST)')
                    raise e
                provision_failed = True
                logger.warning(
                    f'\n{style.BRIGHT}Provision failed for {num_nodes}x '
                    f'{to_provision}. Trying other launchable resources '
                    f'(if any)...{style.RESET_ALL}')
                if not cluster_exists:
                    # Add failed resources to the blocklist, only when it
                    # is in fallback mode.
                    self._blocked_launchable_resources.add(to_provision)
                else:
                    logger.info(
                        'Retrying provisioning with requested resources '
                        f'{task.num_nodes}x {task.resources}')
                    # Retry with the current, potentially "smaller" resources:
                    # to_provision == the current new resources (e.g., V100:1),
                    # which may be "smaller" than the original (V100:8).
                    # num_nodes is not part of a Resources so must be updated
                    # separately.
                    num_nodes = task.num_nodes
                    cluster_exists = False

                # Set to None so that sky.optimize() will assign a new one
                # (otherwise will skip re-optimizing this task).
                # TODO: set all remaining tasks' best_resources to None.
                task.best_resources = None
                # raise_error has to be True to make sure remove_cluster
                # is called if provisioning fails.
                self._dag = sky.optimize(self._dag,
                                         minimize=self._optimize_target,
                                         blocked_launchable_resources=self.
                                         _blocked_launchable_resources,
                                         raise_error=True)
                to_provision = task.best_resources
                assert task in self._dag.tasks, 'Internal logic error.'
                assert to_provision is not None, task
        return config_dict


class CloudVmRayBackend(backends.Backend):
    """Backend: runs on cloud virtual machines, managed by Ray.

    Changing this class may also require updates to:
      * Cloud providers' templates under config/
      * Cloud providers' implementations under clouds/
    """

    NAME = 'cloudvmray'

    class ResourceHandle(object):
        """A pickle-able tuple of:

        - (required) Cluster name.
        - (required) Path to a cluster.yaml file.
        - (optional) A cached head node public IP.  Filled in after a
            successful provision().
        - (optional) Launched num nodes
        - (optional) Launched resources
        - (optional) If TPU(s) are managed, a path to a deletion script.
        """

        def __init__(self,
                     *,
                     cluster_name: str,
                     cluster_yaml: str,
                     head_ip: Optional[str] = None,
                     launched_nodes: Optional[int] = None,
                     launched_resources: Optional[Resources] = None,
                     tpu_create_script: Optional[str] = None,
                     tpu_delete_script: Optional[str] = None) -> None:
            self.cluster_name = cluster_name
            self.cluster_yaml = cluster_yaml
            self.head_ip = head_ip
            self.launched_nodes = launched_nodes
            self.launched_resources = launched_resources
            self.tpu_create_script = tpu_create_script
            self.tpu_delete_script = tpu_delete_script

        def __repr__(self):
            return (f'ResourceHandle('
                    f'\n\tcluster_name={self.cluster_name},'
                    f'\n\thead_ip={self.head_ip},'
                    '\n\tcluster_yaml='
                    f'{self.cluster_yaml}, '
                    f'\n\tlaunched_resources={self.launched_nodes}x '
                    f'{self.launched_resources}, '
                    f'\n\ttpu_create_script={self.tpu_create_script}, '
                    f'\n\ttpu_delete_script={self.tpu_delete_script})')

        def get_cluster_name(self):
            return self.cluster_name

    def __init__(self):
        self.run_timestamp = backend_utils.get_run_timestamp()
        self.log_dir = os.path.join(SKY_LOGS_DIRECTORY, self.run_timestamp)
        # Do not make directories to avoid create folder for commands that
        # do not need it (`sky status`, `sky logs` ...)
        # os.makedirs(self.log_dir, exist_ok=True)

        self._dag = None
        self._optimize_target = None

    def register_info(self, **kwargs) -> None:
        self._dag = kwargs['dag']
        self._optimize_target = kwargs.pop('optimize_target',
                                           OptimizeTarget.COST)

    def _check_task_resources_smaller_than_cluster(self, handle: ResourceHandle,
                                                   task: Task):
        """Check if resources requested by the task are available."""
        assert len(task.resources) == 1, task.resources

        launched_resources = handle.launched_resources
        task_resources = list(task.resources)[0]
        # requested_resources <= actual_resources.
        if not (task.num_nodes <= handle.launched_nodes and
                task_resources.less_demanding_than(launched_resources)):
            cluster_name = handle.cluster_name
            raise exceptions.ResourcesMismatchError(
                'Requested resources do not match the existing cluster.\n'
                f'  Requested: {task.num_nodes}x {task_resources}\n'
                f'  Existing: {handle.launched_nodes}x '
                f'{handle.launched_resources}\n'
                f'To fix: specify a new cluster name, or down the '
                f'existing cluster first: sky down {cluster_name}')

    def _check_existing_cluster(
            self, task: Task, to_provision: Resources,
            cluster_name: str) -> RetryingVmProvisioner.ToProvisionConfig:
        handle = global_user_state.get_handle_from_cluster_name(cluster_name)
        if handle is not None:
            # Cluster already exists.
            self._check_task_resources_smaller_than_cluster(handle, task)
            # Use the existing cluster.
            assert handle.launched_resources is not None, (cluster_name, handle)
            return RetryingVmProvisioner.ToProvisionConfig(
                cluster_name, handle.launched_resources, handle.launched_nodes,
                True)

        logger.info(
            f'{colorama.Fore.CYAN}Creating a new cluster: "{cluster_name}" '
            f'[{task.num_nodes}x {to_provision}].{colorama.Style.RESET_ALL}\n'
            'Tip: to reuse an existing cluster, '
            'specify --cluster (-c). '
            'Run `sky status` to see existing clusters.')
        return RetryingVmProvisioner.ToProvisionConfig(cluster_name,
                                                       to_provision,
                                                       task.num_nodes)

    def _set_tpu_name(self, cluster_config_file: str, num_nodes: int,
                      tpu_name: str) -> None:
        """Sets TPU_NAME on all nodes."""
        ip_list = backend_utils.get_node_ips(cluster_config_file, num_nodes)
        ssh_user, ssh_key = backend_utils.ssh_credential_from_yaml(
            cluster_config_file)

        for ip in ip_list:
            cmd = (f'[[ -z $TPU_NAME ]] && echo "export TPU_NAME={tpu_name}" '
                   '>> ~/.bashrc || echo "TPU_NAME already set"')
            returncode = backend_utils.run_command_on_ip_via_ssh(
                ip,
                cmd,
                ssh_user=ssh_user,
                ssh_private_key=ssh_key,
                log_path=os.path.join(self.log_dir, 'tpu_setup.log'))
            backend_utils.handle_returncode(returncode, cmd,
                                            'Failed to set TPU_NAME on node.')

    def provision(self,
                  task: Task,
                  to_provision: Resources,
                  dryrun: bool,
                  stream_logs: bool,
                  cluster_name: Optional[str] = None):
        """Provisions using 'ray up'."""
        # Try to launch the exiting cluster first
        if cluster_name is None:
            cluster_name = backend_utils.generate_cluster_name()
        _check_cluster_name_is_valid(cluster_name)
        # ray up: the VMs.
        # FIXME: ray up for Azure with different cluster_names will overwrite
        # each other.
        provisioner = RetryingVmProvisioner(self.log_dir, self._dag,
                                            self._optimize_target)

        lock_path = os.path.expanduser(_LOCK_FILENAME.format(cluster_name))
        # TODO(mraheja): remove pylint disabling when filelock version updated
        # pylint: disable=abstract-class-instantiated
        with filelock.FileLock(lock_path):
            to_provision_config = RetryingVmProvisioner.ToProvisionConfig(
                cluster_name, to_provision, task.num_nodes)
            if not dryrun:  # dry run doesn't need to check existing cluster.
                to_provision_config = self._check_existing_cluster(
                    task, to_provision, cluster_name)
            try:
                config_dict = provisioner.provision_with_retries(
                    task, to_provision_config, dryrun, stream_logs)
            except exceptions.ResourcesUnavailableError as e:
                # Do not remove the stopped cluster from the global state
                # if failed to start.
                if e.no_retry:
                    logger.error(e)
                    sys.exit(1)
                # Clean up the cluster's entry in `sky status`.
                global_user_state.remove_cluster(cluster_name, terminate=True)
                logger.error(
                    'Failed to provision all possible launchable resources. '
                    f'Relax the task\'s resource requirements:\n '
                    f'{task.num_nodes}x {task.resources}')
                sys.exit(1)
            if dryrun:
                return
            cluster_config_file = config_dict['ray']

            if 'tpu_name' in config_dict:
                self._set_tpu_name(cluster_config_file,
                                   config_dict['launched_nodes'],
                                   config_dict['tpu_name'])

            ip_list = backend_utils.get_node_ips(cluster_config_file,
                                                 config_dict['launched_nodes'])
            head_ip = ip_list[0]

            handle = self.ResourceHandle(
                cluster_name=cluster_name,
                cluster_yaml=cluster_config_file,
                # Cache head ip in the handle to speed up ssh operations.
                head_ip=head_ip,
                launched_nodes=config_dict['launched_nodes'],
                launched_resources=config_dict['launched_resources'],
                # TPU.
                tpu_create_script=config_dict.get('tpu-create-script'),
                tpu_delete_script=config_dict.get('tpu-delete-script'))

            # Update job queue to avoid stale jobs (when restarted), before
            # setting the cluster to be ready.
            codegen = backend_utils.JobLibCodeGen()
            codegen.update_status()
            cmd = codegen.build()
            returncode = self.run_on_head(handle, cmd)
            backend_utils.handle_returncode(returncode, cmd,
                                            'Failed to update job status.')

            global_user_state.add_or_update_cluster(cluster_name,
                                                    handle,
                                                    ready=True)
            auth_config = backend_utils.read_yaml(handle.cluster_yaml)['auth']
            backend_utils.SSHConfigHelper.add_cluster(cluster_name, ip_list,
                                                      auth_config)

            os.remove(lock_path)
            return handle

    def sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        # Even though provision() takes care of it, there may be cases where
        # this function is called in isolation, without calling provision(),
        # e.g., in CLI.  So we should rerun rsync_up.
        fore = colorama.Fore
        style = colorama.Style
        ip_list = backend_utils.get_node_ips(handle.cluster_yaml,
                                             handle.launched_nodes)
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
            workdir = os.path.join(workdir, '')  # Adds trailing / if needed.

        # Raise warning if directory is too large
        dir_size = _path_size_megabytes(full_workdir, exclude_gitignore=True)
        if dir_size >= _PATH_SIZE_MEGABYTES_WARN_THRESHOLD:
            logger.warning(
                f'{fore.YELLOW}The size of workdir {workdir!r} '
                f'is {dir_size} MB. Try to keep workdir small or use '
                '.gitignore to exclude large files, as '
                'large sizes will slow down rsync. If you use .gitignore but '
                'the path is not initialized in git, you can ignore this '
                f'warning.{style.RESET_ALL}')

        log_path = os.path.join(self.log_dir, 'workdir_sync.log')

        def _sync_workdir_node(ip):
            self._rsync_up(handle,
                           ip=ip,
                           source=workdir,
                           target=SKY_REMOTE_WORKDIR,
                           log_path=log_path,
                           stream_logs=False,
                           raise_error=True)

        num_nodes = handle.launched_nodes
        plural = 's' if num_nodes > 1 else ''
        logger.info(
            f'{fore.CYAN}Syncing workdir (to {num_nodes} node{plural}): '
            f'{style.BRIGHT}{workdir}{style.RESET_ALL}'
            f' -> '
            f'{style.BRIGHT}{SKY_REMOTE_WORKDIR}{style.RESET_ALL}')
        tail_cmd = f'tail -n100 -f {log_path}'
        logger.info('To view detailed progress: '
                    f'{style.BRIGHT}{tail_cmd}{style.RESET_ALL}')
        with console.status('[bold cyan]Syncing[/]'):
            backend_utils.run_in_parallel(_sync_workdir_node, ip_list)

    def sync_file_mounts(
        self,
        handle: ResourceHandle,
        all_file_mounts: Dict[Path, Path],
        cloud_to_remote_file_mounts: Optional[Dict[Path, Path]],
    ) -> None:
        """Mounts all user files to the remote nodes."""
        # File mounts handling for remote paths possibly without write access:
        #  (1) in 'file_mounts' sections, add <prefix> to these target paths.
        #  (2) then, create symlinks from '/.../file' to '<prefix>/.../file'.
        start = time.time()
        del cloud_to_remote_file_mounts  # Unused.
        mounts = all_file_mounts
        symlink_commands = []
        if mounts is None or not mounts:
            return
        fore = colorama.Fore
        style = colorama.Style
        logger.info(f'{fore.CYAN}Processing file mounts.{style.RESET_ALL}')

        ip_list = backend_utils.get_node_ips(handle.cluster_yaml,
                                             handle.launched_nodes)
        ssh_user, ssh_key = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)

        log_path = os.path.join(self.log_dir, 'file_mounts.log')

        def sync_to_all_nodes(src: str,
                              dst: str,
                              command: Optional[str] = None,
                              run_rsync: Optional[bool] = False):
            if run_rsync:
                # Do this for local src paths, not for cloud store URIs
                # (otherwise we have '<abs path to cwd>/gs://.../object/').
                full_src = os.path.abspath(os.path.expanduser(src))
                if not os.path.islink(full_src) and not os.path.isfile(
                        full_src):
                    src = os.path.join(src, '')  # Adds trailing / if needed.

            def _sync_node(ip):
                if command is not None:
                    returncode = backend_utils.run_command_on_ip_via_ssh(
                        ip,
                        command,
                        ssh_user=ssh_user,
                        ssh_private_key=ssh_key,
                        log_path=log_path,
                        stream_logs=False,
                        ssh_control_name=self._ssh_control_name(handle))
                    backend_utils.handle_returncode(
                        returncode,
                        command,
                        f'Failed to sync {src} to {dst}.',
                        raise_error=True)

                if run_rsync:
                    # TODO(zhwu): Optimize for large amount of files.
                    # zip / transfer/ unzip
                    self._rsync_up(handle,
                                   ip=ip,
                                   source=src,
                                   target=dst,
                                   log_path=log_path,
                                   stream_logs=False,
                                   raise_error=True)

            num_nodes = handle.launched_nodes
            plural = 's' if num_nodes > 1 else ''
            logger.info(f'{fore.CYAN}Syncing (to {num_nodes} node{plural}): '
                        f'{style.BRIGHT}{src}{style.RESET_ALL} -> '
                        f'{style.BRIGHT}{dst}{style.RESET_ALL}')
            with console.status('[bold cyan]Syncing[/]'):
                backend_utils.run_in_parallel(_sync_node, ip_list)

        # Check the files and warn
        for dst, src in mounts.items():
            if not task_lib.is_cloud_store_url(src):
                full_src = os.path.abspath(os.path.expanduser(src))
                # Checked during Task.set_file_mounts().
                assert os.path.exists(full_src), f'{full_src} does not exist.'
                src_size = _path_size_megabytes(full_src,
                                                exclude_gitignore=True)
                if src_size >= _PATH_SIZE_MEGABYTES_WARN_THRESHOLD:
                    logger.warning(
                        f'{fore.YELLOW}The size of file mount src {src!r} '
                        f'is {src_size} MB. Try to keep src small or use '
                        '.gitignore to exclude large files, as '
                        'large sizes will slow down rsync. If you use '
                        '.gitignore but the path is not initialized in git, you'
                        f' can ignore this warning.{style.RESET_ALL}')
                if os.path.islink(full_src):
                    logger.warning(
                        f'{fore.YELLOW}Source path {src!r} is a symlink. '
                        f'Symlink contents are not uploaded.{style.RESET_ALL}')

        tail_cmd = f'tail -n100 -f {log_path}'
        logger.info('To view detailed progress: '
                    f'{style.BRIGHT}{tail_cmd}{style.RESET_ALL}')

        for dst, src in mounts.items():
            # TODO: room for improvement.  Here there are many moving parts
            # (download gsutil on remote, run gsutil on remote).  Consider
            # alternatives (smart_open, each provider's own sdk), a
            # data-transfer container etc.
            if not os.path.isabs(dst) and not dst.startswith('~/'):
                dst = f'~/{dst}'
            # Sync 'src' to 'wrapped_dst', a safe-to-write "wrapped" path.
            wrapped_dst = dst
            if not dst.startswith('~/') and not dst.startswith('/tmp/'):
                # Handles the remote paths possibly without write access.
                # (1) add <prefix> to these target paths.
                wrapped_dst = backend_utils.FileMountHelper.wrap_file_mount(dst)
                cmd = backend_utils.FileMountHelper.make_safe_symlink_command(
                    source=dst, target=wrapped_dst)
                symlink_commands.append(cmd)

            if not task_lib.is_cloud_store_url(src):
                full_src = os.path.abspath(os.path.expanduser(src))

                if os.path.isfile(full_src):
                    mkdir_for_wrapped_dst = \
                        f'mkdir -p {os.path.dirname(wrapped_dst)}'
                else:
                    mkdir_for_wrapped_dst = f'mkdir -p {wrapped_dst}'

                # TODO(mluo): Fix method so that mkdir and rsync run together
                sync_to_all_nodes(src=src,
                                  dst=wrapped_dst,
                                  command=mkdir_for_wrapped_dst,
                                  run_rsync=True)
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
                mkdir_for_wrapped_dst = \
                    f'mkdir -p {os.path.dirname(wrapped_dst)}'

            download_target_commands = [
                # Ensure sync can write to wrapped_dst (e.g., '/data/').
                mkdir_for_wrapped_dst,
                # Both the wrapped and the symlink dir exist; sync.
                sync,
            ]
            command = ' && '.join(download_target_commands)
            # dst is only used for message printing.
            sync_to_all_nodes(src, dst, command)

        # (2) Run the commands to create symlinks on all the nodes.
        symlink_command = ' && '.join(symlink_commands)
        if not symlink_command:
            return

        def _symlink_node(ip):
            returncode = backend_utils.run_command_on_ip_via_ssh(
                ip,
                symlink_command,
                ssh_user=ssh_user,
                ssh_private_key=ssh_key,
                log_path=log_path,
                ssh_control_name=self._ssh_control_name(handle))
            backend_utils.handle_returncode(
                returncode,
                symlink_command,
                'Failed to create symlinks. The target destination '
                'may already exist',
                raise_error=True)

        backend_utils.run_in_parallel(_symlink_node, ip_list)

        end = time.time()
        logger.debug(f'File mount sync took {end - start} seconds.')

    def setup(self, handle: ResourceHandle, task: Task) -> None:
        start = time.time()
        style = colorama.Style
        fore = colorama.Fore

        if task.setup is None:
            return

        setup_script = log_lib.make_task_bash_script(task.setup)
        with tempfile.NamedTemporaryFile('w', prefix='sky_setup_') as f:
            f.write(setup_script)
            f.flush()
            setup_sh_path = f.name
            setup_file = os.path.basename(setup_sh_path)
            # Sync the setup script up and run it.
            ip_list = backend_utils.get_node_ips(handle.cluster_yaml,
                                                 handle.launched_nodes)
            ssh_user, ssh_key = backend_utils.ssh_credential_from_yaml(
                handle.cluster_yaml)

            def _setup_node(ip: int) -> int:
                self._rsync_up(handle,
                               ip=ip,
                               source=setup_sh_path,
                               target=f'/tmp/{setup_file}',
                               stream_logs=False)
                # Need this `-i` option to make sure `source ~/.bashrc` work
                cmd = f'/bin/bash -i /tmp/{setup_file} 2>&1'
                returncode = backend_utils.run_command_on_ip_via_ssh(
                    ip,
                    cmd,
                    ssh_user=ssh_user,
                    ssh_private_key=ssh_key,
                    log_path=os.path.join(self.log_dir, 'setup.log'),
                    ssh_control_name=self._ssh_control_name(handle))
                backend_utils.handle_returncode(
                    returncode=returncode,
                    command=cmd,
                    error_msg=f'Failed to setup with return code {returncode}',
                    raise_error=True)

            num_nodes = handle.launched_nodes
            plural = 's' if num_nodes > 1 else ''
            logger.info(f'{fore.CYAN}Running setup on {num_nodes} node{plural}.'
                        f'{style.RESET_ALL}')
            with console.status('[bold cyan]Running setup[/]'):
                backend_utils.run_in_parallel(_setup_node, ip_list)
        logger.info(f'{fore.GREEN}Setup completed.{style.RESET_ALL}')
        end = time.time()
        logger.debug(f'Setup took {end - start} seconds.')

    def sync_down_logs(self, handle: ResourceHandle, job_id: int) -> None:
        codegen = backend_utils.JobLibCodeGen()
        codegen.get_log_path(job_id)
        code = codegen.build()
        returncode, log_dir, stderr = self.run_on_head(handle,
                                                       code,
                                                       stream_logs=False,
                                                       require_outputs=True)
        backend_utils.handle_returncode(returncode, code,
                                        'Failed to sync logs.', stderr)
        log_dir = log_dir.strip()

        local_log_dir = os.path.expanduser(log_dir)
        remote_log_dir = log_dir

        style = colorama.Style
        fore = colorama.Fore
        logger.info(f'{fore.CYAN}Logs Directory: '
                    f'{style.BRIGHT}{local_log_dir}{style.RESET_ALL}')

        ips = backend_utils.get_node_ips(handle.cluster_yaml,
                                         handle.launched_nodes)

        def rsync_down(ip: str) -> None:
            from ray.autoscaler import sdk  # pylint: disable=import-outside-toplevel
            sdk.rsync(
                handle.cluster_yaml,
                source=f'{remote_log_dir}/*',
                target=f'{local_log_dir}',
                down=True,
                ip_address=ip,
                use_internal_ip=False,
                should_bootstrap=False,
            )

        # Call the ray sdk to rsync the logs back to local.
        for i, ip in enumerate(ips):
            try:
                # Disable the output of rsync.
                with open('/dev/null', 'a') as f, contextlib.redirect_stdout(
                        f), contextlib.redirect_stderr(f):
                    rsync_down(ip)
                logger.info(f'{fore.CYAN}Job {job_id} logs: Downloaded from '
                            f'node-{i} ({ip}){style.RESET_ALL}')
            except click.exceptions.ClickException as e:
                # Raised by rsync_down. Remote log dir may not exist, since
                # the job can be run on some part of the nodes.
                if 'SSH command failed' in str(e):
                    logger.debug(f'node-{i} ({ip}) does not have the tasks/*.')
                else:
                    raise e

    def _exec_code_on_head(
        self,
        handle: ResourceHandle,
        codegen: str,
        job_id: int,
        executable: str,
        detach_run: bool = False,
    ) -> None:
        """Executes generated code on the head node."""
        colorama.init()
        style = colorama.Style
        fore = colorama.Fore
        with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
            fp.write(codegen)
            fp.flush()
            script_path = os.path.join(SKY_REMOTE_APP_DIR, f'sky_job_{job_id}')
            # We choose to sync code + exec, because the alternative of 'ray
            # submit' may not work as it may use system python (python2) to
            # execute the script.  Happens for AWS.
            self._rsync_up(handle,
                           source=fp.name,
                           target=script_path,
                           stream_logs=False)
        remote_log_dir = self.log_dir
        local_log_dir = os.path.expanduser(remote_log_dir)
        job_log_path = os.path.join(local_log_dir, 'job_submit.log')
        remote_log_path = os.path.join(remote_log_dir, 'run.log')

        assert executable == 'python3', executable
        cd = f'cd {SKY_REMOTE_WORKDIR}'

        job_submit_cmd = (
            f'mkdir -p {remote_log_dir} && ray job submit '
            f'--address=127.0.0.1:8265 --job-id {job_id} --no-wait '
            f'-- "{executable} -u {script_path} > {remote_log_path} 2>&1"')

        returncode = self.run_on_head(handle,
                                      f'{cd} && {job_submit_cmd}',
                                      log_path=job_log_path,
                                      stream_logs=False)
        backend_utils.handle_returncode(returncode, job_submit_cmd,
                                        f'Failed to submit job {job_id}.')

        logger.info('Job submitted with Job ID: '
                    f'{style.BRIGHT}{job_id}{style.RESET_ALL}')

        try:
            if not detach_run:
                # Wait for the job being sucessfully submitted to ray job.
                time.sleep(1)
                # Sky logs. Not using subprocess.run since it will make the
                # ssh keep connected after ctrl-c.
                self.tail_logs(handle, job_id)
        finally:
            name = handle.cluster_name
            logger.info(f'{fore.CYAN}Job ID: '
                        f'{style.BRIGHT}{job_id}{style.RESET_ALL}'
                        '\nTo cancel the job:\t'
                        f'{backend_utils.BOLD}sky cancel {name} {job_id}'
                        f'{backend_utils.RESET_BOLD}'
                        '\nTo stream the logs:\t'
                        f'{backend_utils.BOLD}sky logs {name} {job_id}'
                        f'{backend_utils.RESET_BOLD}'
                        '\nTo view the job queue:\t'
                        f'{backend_utils.BOLD}sky queue {name}'
                        f'{backend_utils.RESET_BOLD}')

    def tail_logs(self, handle: ResourceHandle, job_id: int) -> None:
        codegen = backend_utils.JobLibCodeGen()
        codegen.tail_logs(job_id)
        code = codegen.build()
        logger.info(f'{colorama.Fore.YELLOW}Start streaming logs...'
                    f'{colorama.Style.RESET_ALL}')

        # With interactive mode, the ctrl-c will send directly to the running
        # program on the remote instance, and the ssh will be disconnected by
        # sshd, so no error code will appear.
        self.run_on_head(
            handle,
            code,
            stream_logs=True,
            redirect_stdout_stderr=False,
            # Allocate a pseudo-terminal to disable output buffering. Otherwise,
            # there may be 5 minutes delay in logging.
            ssh_mode=backend_utils.SshMode.INTERACTIVE)

        # Due to the interactive mode of ssh, we cannot distinguish the ctrl-c
        # from other success case (e.g. the job is finished) from the returncode
        # or catch by KeyboardInterrupt exception.
        # TODO(zhwu): only show this line when ctrl-c is sent.
        logger.warning(f'{colorama.Fore.LIGHTBLACK_EX}The job will keep '
                       f'running after Ctrl-C.{colorama.Style.RESET_ALL}')

    def _add_job(self, handle: ResourceHandle, job_name: str) -> int:
        codegen = backend_utils.JobLibCodeGen()
        username = getpass.getuser()
        codegen.add_job(job_name, username, self.run_timestamp)
        code = codegen.build()
        returncode, job_id_str, stderr = self.run_on_head(handle,
                                                          code,
                                                          stream_logs=False,
                                                          require_outputs=True)
        # TODO(zhwu): this sometimes will unexpectedly fail, we can add
        # retry for this, after we figure out the reason.
        backend_utils.handle_returncode(returncode, code,
                                        'Failed to fetch job id.', stderr)
        try:
            job_id = int(job_id_str)
        except ValueError as e:
            logger.error(stderr)
            raise ValueError(f'Failed to parse job id: {job_id_str}; '
                             f'Returncode: {returncode}') from e
        return job_id

    def execute(
        self,
        handle: ResourceHandle,
        task: Task,
        detach_run: bool,
    ) -> None:
        # Check the task resources vs the cluster resources. Since `sky exec`
        # will not run the provision and _check_existing_cluster
        self._check_task_resources_smaller_than_cluster(handle, task)

        # Otherwise, handle a basic Task.
        if task.run is None:
            logger.info('Nothing to run (Task.run not specified).')
            return

        job_id = self._add_job(handle, task.name)

        # Case: Task(run, num_nodes=1)
        if task.num_nodes == 1:
            return self._execute_task_one_node(handle, task, job_id, detach_run)

        # Case: Task(run, num_nodes=N)
        assert task.num_nodes > 1, task.num_nodes
        return self._execute_task_n_nodes(handle, task, job_id, detach_run)

    def _execute_task_one_node(self, handle: ResourceHandle, task: Task,
                               job_id: int, detach_run: bool) -> None:
        # Launch the command as a Ray task.
        log_dir = os.path.join(self.log_dir, 'tasks')
        log_path = os.path.join(log_dir, 'run.log')

        accelerator_dict = _get_task_demands_dict(task)

        codegen = RayCodeGen()
        codegen.add_prologue(job_id)
        codegen.add_gang_scheduling_placement_group(1, accelerator_dict)

        if callable(task.run):
            run_fn_code = textwrap.dedent(inspect.getsource(task.run))
            run_fn_name = task.run.__name__
            codegen.register_run_fn(run_fn_code, run_fn_name)

        command_for_node = task.run if isinstance(task.run, str) else None
        codegen.add_ray_task(bash_script=command_for_node,
                             task_name=task.name,
                             ray_resources_dict=_get_task_demands_dict(task),
                             log_path=log_path)

        codegen.add_epilogue()

        self._exec_code_on_head(handle,
                                codegen.build(),
                                job_id,
                                executable='python3',
                                detach_run=detach_run)

    def _execute_task_n_nodes(self, handle: ResourceHandle, task: Task,
                              job_id: int, detach_run: bool) -> None:
        # Strategy:
        #   ray.init(...)
        #   for node:
        #     submit _run_cmd(cmd) with resource {node_i: 1}
        log_dir_base = self.log_dir
        log_dir = os.path.join(log_dir_base, 'tasks')
        accelerator_dict = _get_task_demands_dict(task)

        codegen = RayCodeGen()
        codegen.add_prologue(job_id)
        codegen.add_gang_scheduling_placement_group(task.num_nodes,
                                                    accelerator_dict)

        if callable(task.run):
            run_fn_code = textwrap.dedent(inspect.getsource(task.run))
            run_fn_name = task.run.__name__
            codegen.register_run_fn(run_fn_code, run_fn_name)
        # TODO(zhwu): The resources limitation for multi-node ray.tune and
        # horovod should be considered.
        for i in range(task.num_nodes):
            command_for_node = task.run if isinstance(task.run, str) else None

            # Ray's per-node resources, to constrain scheduling each command to
            # the corresponding node, represented by private IPs.
            name = f'node-{i}'
            log_path = os.path.join(f'{log_dir}', f'{name}.log')

            codegen.add_ray_task(
                bash_script=command_for_node,
                task_name=name,
                ray_resources_dict=accelerator_dict,
                log_path=log_path,
                gang_scheduling_id=i,
            )

        codegen.add_epilogue()

        # Logger.
        colorama.init()
        fore = colorama.Fore
        style = colorama.Style
        logger.info(f'\n{fore.CYAN}Starting Task execution.{style.RESET_ALL}')

        # TODO(zhanghao): Add help info for downloading logs.
        self._exec_code_on_head(handle,
                                codegen.build(),
                                job_id,
                                executable='python3',
                                detach_run=detach_run)

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        colorama.init()
        fore = colorama.Fore
        style = colorama.Style
        if not teardown:
            name = handle.cluster_name
            logger.info(f'\n{fore.CYAN}Cluster name: '
                        f'{style.BRIGHT}{name}{style.RESET_ALL}'
                        '\nTo log into the head VM:\t'
                        f'{backend_utils.BOLD}ssh {name}'
                        f'{backend_utils.RESET_BOLD}'
                        '\nTo submit a job:'
                        f'\t\t{backend_utils.BOLD}sky exec {name} yaml_file'
                        f'{backend_utils.RESET_BOLD}'
                        '\nTo stop the cluster:'
                        f'\t{backend_utils.BOLD}sky stop {name}'
                        f'{backend_utils.RESET_BOLD}'
                        '\nTo teardown the cluster:'
                        f'\t{backend_utils.BOLD}sky down {name}'
                        f'{backend_utils.RESET_BOLD}')
            if handle.tpu_delete_script is not None:
                logger.info('Tip: `sky down` will delete launched TPU(s) too.')

    def teardown_ephemeral_storage(self, task: Task) -> None:
        storage_mounts = task.storage_mounts
        if storage_mounts is not None:
            for _, storage in storage_mounts.items():
                if not storage.persistent:
                    storage.delete()

    def teardown(self,
                 handle: ResourceHandle,
                 terminate: bool,
                 _force: bool = False) -> bool:
        cluster_name = handle.cluster_name
        lock_path = os.path.expanduser(_LOCK_FILENAME.format(cluster_name))

        if _force:
            # Should only be forced when teardown is called within a
            # locked section of the code (i.e teardown when not enough
            # resources can be provisioned)
            return self._teardown(handle, terminate)

        try:
            # TODO(mraheja): remove pylint disabling when filelock
            # version updated
            # pylint: disable=abstract-class-instantiated
            with filelock.FileLock(lock_path, _FILELOCK_TIMEOUT_SECONDS):
                success = self._teardown(handle, terminate)
            if success and terminate:
                os.remove(lock_path)
            return success
        except filelock.Timeout:
            logger.error(f'Cluster {cluster_name} is locked by {lock_path}. '
                         'Check to see if it is still being launched.')
        return False

    def _teardown(self, handle: ResourceHandle, terminate: bool) -> bool:
        log_path = os.path.join(os.path.expanduser(self.log_dir),
                                'teardown.log')
        log_abs_path = os.path.abspath(log_path)
        cloud = handle.launched_resources.cloud
        config = backend_utils.read_yaml(handle.cluster_yaml)
        prev_status = global_user_state.get_status_from_cluster_name(
            handle.cluster_name)
        cluster_name = handle.cluster_name
        if terminate and isinstance(cloud, clouds.Azure):
            # Here we handle termination of Azure by ourselves instead of Ray
            # autoscaler.
            resource_group = config['provider']['resource_group']
            terminate_cmd = f'az group delete -y --name {resource_group}'
            with console.status(f'[bold cyan]Terminating '
                                f'[green]{cluster_name}'):
                returncode, stdout, stderr = log_lib.run_with_log(
                    terminate_cmd,
                    log_abs_path,
                    shell=True,
                    stream_logs=False,
                    require_outputs=True)
        elif (terminate and
              prev_status == global_user_state.ClusterStatus.STOPPED):
            if isinstance(cloud, clouds.AWS):
                # TODO(zhwu): Room for optimization. We can move these cloud
                # specific handling to the cloud class.
                # The stopped instance on AWS will not be correctly terminated
                # due to ray's bug.
                region = config['provider']['region']
                query_cmd = (
                    f'aws ec2 describe-instances --region {region} --filters '
                    f'Name=tag:ray-cluster-name,Values={handle.cluster_name} '
                    'Name=instance-state-name,Values=stopping,stopped '
                    f'--query Reservations[].Instances[].InstanceId '
                    '--output text')
                terminate_cmd = (
                    f'aws ec2 terminate-instances --region {region} '
                    f'--instance-ids $({query_cmd})')
                with console.status(f'[bold cyan]Terminating '
                                    f'[green]{cluster_name}'):
                    returncode, stdout, stderr = log_lib.run_with_log(
                        terminate_cmd,
                        log_abs_path,
                        shell=True,
                        stream_logs=False,
                        require_outputs=True)
            elif isinstance(cloud, clouds.GCP):
                zone = config['provider']['availability_zone']
                query_cmd = (
                    f'gcloud compute instances list '
                    f'--filter=\\(labels.ray-cluster-name={cluster_name}\\) '
                    f'--zones={zone} --format=value\\(name\\)')
                terminate_cmd = (
                    f'gcloud compute instances delete --zone={zone} --quiet '
                    f'$({query_cmd})')
                with console.status(f'[bold cyan]Terminating '
                                    f'[green]{cluster_name}'):
                    returncode, stdout, stderr = log_lib.run_with_log(
                        terminate_cmd,
                        log_abs_path,
                        shell=True,
                        stream_logs=False,
                        require_outputs=True)
            else:
                raise ValueError(f'Unsupported cloud {cloud} for stopped '
                                 f'cluster {cluster_name!r}.')
        else:
            config['provider']['cache_stopped_nodes'] = not terminate
            with tempfile.NamedTemporaryFile('w',
                                             prefix='sky_',
                                             delete=False,
                                             suffix='.yml') as f:
                backend_utils.dump_yaml(f.name, config)
                f.flush()

                teardown_verb = 'Terminating' if terminate else 'Stopping'
                with console.status(f'[bold cyan]{teardown_verb} '
                                    f'[green]{cluster_name}'):
                    returncode, stdout, stderr = log_lib.run_with_log(
                        ['ray', 'down', '-y', f.name],
                        log_abs_path,
                        stream_logs=False,
                        require_outputs=True)

            if handle.tpu_delete_script is not None:
                with console.status('[bold cyan]Terminating TPU...'):
                    tpu_rc, tpu_stdout, tpu_stderr = log_lib.run_with_log(
                        ['bash', handle.tpu_delete_script],
                        log_abs_path,
                        stream_logs=False,
                        require_outputs=True)
                if tpu_rc != 0:
                    logger.error(f'{colorama.Fore.RED}Failed to delete TPU.\n'
                                 f'**** STDOUT ****\n'
                                 f'{tpu_stdout}\n'
                                 f'**** STDERR ****\n'
                                 f'{tpu_stderr}{colorama.Style.RESET_ALL}')
                    return False

        if returncode != 0:
            logger.error(
                f'{colorama.Fore.RED}Failed to terminate {cluster_name}.\n'
                f'**** STDOUT ****\n'
                f'{stdout}\n'
                f'**** STDERR ****\n'
                f'{stderr}{colorama.Style.RESET_ALL}')
            return False

        auth_config = backend_utils.read_yaml(handle.cluster_yaml)['auth']
        backend_utils.SSHConfigHelper.remove_cluster(cluster_name,
                                                     handle.head_ip,
                                                     auth_config)
        name = global_user_state.get_cluster_name_from_handle(handle)
        global_user_state.remove_cluster(name, terminate=terminate)

        if terminate:
            # Clean up generated config
            # No try-except is needed since Ray will fail to teardown the
            # cluster if the cluster_yaml is missing.
            os.remove(handle.cluster_yaml)

            # Clean up TPU creation/deletion scripts
            if handle.tpu_delete_script is not None:
                assert handle.tpu_create_script is not None
                os.remove(handle.tpu_create_script)
                os.remove(handle.tpu_delete_script)
        return True

    def _rsync_up(
        self,
        handle: ResourceHandle,
        source: str,
        target: str,
        stream_logs: bool = True,
        log_path: str = '/dev/null',
        ip: Optional[str] = None,
        raise_error: bool = True,
    ) -> None:
        """Runs rsync from 'source' to the cluster's node 'target'."""
        # Attempt to use 'rsync user@ip' directly, which is much faster than
        # going through ray (either 'ray rsync_*' or sdk.rsync()).
        if ip is None:
            ip = handle.head_ip
        if handle.head_ip is None:
            raise ValueError(
                f'The cluster "{handle.cluster_name}" appears to be down. '
                'Run a re-provisioning command (e.g., sky launch) and retry.')

        ssh_user, ssh_key = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)
        # Build command.
        # TODO(zhwu): This will print a per-file progress bar (with -P),
        # shooting a lot of messages to the output. --info=progress2 is used
        # to get a total progress bar, but it requires rsync>=3.1.0 and Mac
        # OS has a default rsync==2.6.9 (16 years old).
        rsync_command = ['rsync', '-Pavz']
        rsync_command.append('--filter=\':- .gitignore\'')
        ssh_options = ' '.join(
            backend_utils.ssh_options_list(ssh_key,
                                           self._ssh_control_name(handle)))
        rsync_command.append(f'-e "ssh {ssh_options}"')
        rsync_command.extend([
            source,
            f'{ssh_user}@{ip}:{target}',
        ])
        command = ' '.join(rsync_command)

        returncode = log_lib.run_with_log(command,
                                          log_path=log_path,
                                          stream_logs=stream_logs,
                                          shell=True)
        backend_utils.handle_returncode(
            returncode,
            command, f'Failed to rsync up {source} -> {target}, '
            f'see {log_path} for details.',
            raise_error=raise_error)

    def _ssh_control_name(self, handle: ResourceHandle) -> str:
        return f'{hashlib.md5(handle.cluster_yaml.encode()).hexdigest()[:10]}'

    # TODO(zhwu): Refactor this to a CommandRunner class, so different backends
    # can support its own command runner.
    def run_on_head(
        self,
        handle: ResourceHandle,
        cmd: str,
        *,
        port_forward: Optional[List[str]] = None,
        log_path: str = '/dev/null',
        redirect_stdout_stderr: bool = True,
        stream_logs: bool = False,
        use_cached_head_ip: bool = True,
        ssh_mode: backend_utils.SshMode = backend_utils.SshMode.NON_INTERACTIVE,
        under_remote_workdir: bool = False,
        require_outputs: bool = False,
    ) -> Union[int, Tuple[int, str, str]]:
        """Runs 'cmd' on the cluster's head node."""
        head_ip = backend_utils.get_head_ip(handle, use_cached_head_ip)
        ssh_user, ssh_private_key = backend_utils.ssh_credential_from_yaml(
            handle.cluster_yaml)
        if under_remote_workdir:
            cmd = f'cd {SKY_REMOTE_WORKDIR} && {cmd}'

        return backend_utils.run_command_on_ip_via_ssh(
            head_ip,
            cmd,
            ssh_user=ssh_user,
            ssh_private_key=ssh_private_key,
            port_forward=port_forward,
            log_path=log_path,
            redirect_stdout_stderr=redirect_stdout_stderr,
            stream_logs=stream_logs,
            ssh_mode=ssh_mode,
            ssh_control_name=self._ssh_control_name(handle),
            require_outputs=require_outputs,
        )
