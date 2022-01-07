"""Backend: runs on cloud virtual machines, managed by Ray."""
import ast
import getpass
import hashlib
import inspect
import json
import os
import re
import shlex
import subprocess
import tempfile
import textwrap
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

import colorama
from ray.autoscaler import sdk

import sky
from sky import backends
from sky import clouds
from sky import cloud_stores
from sky import dag as dag_lib
from sky import exceptions
from sky import global_user_state
from sky import logging
from sky import optimizer
from sky import resources as resources_lib
from sky import task as task_mod
from sky.backends import backend_utils
from sky.backends.sky_remote_utils import log_utils

App = backend_utils.App

Resources = resources_lib.Resources
Dag = dag_lib.Dag
OptimizeTarget = optimizer.OptimizeTarget

Path = str
PostSetupFn = Callable[[str], Any]
JobStatus = backend_utils.JobStatus
SKY_REMOTE_WORKDIR = backend_utils.SKY_REMOTE_WORKDIR
SKY_REMOTE_APP_DIR = backend_utils.SKY_REMOTE_APP_DIR
SKY_LOGS_DIRECTORY = backend_utils.SKY_LOGS_DIRECTORY
SKY_REMOTE_RAY_VERSION = backend_utils.SKY_REMOTE_RAY_VERSION
SKY_REMOTE_UTIL_PATH = backend_utils.SKY_REMOTE_UTIL_PATH

logger = logging.init_logger(__name__)


def _get_cluster_config_template(cloud):
    cloud_to_template = {
        clouds.AWS: 'config/aws-ray.yml.j2',
        clouds.Azure: 'config/azure-ray.yml.j2',
        clouds.GCP: 'config/gcp-ray.yml.j2',
    }
    path = cloud_to_template[type(cloud)]
    return os.path.join(os.path.dirname(sky.__root_dir__), path)


def _get_task_demands_dict(task: App) -> Optional[Tuple[Optional[str], int]]:
    """Returns the accelerator dict of the task"""
    # TODO: CPU and other memory resources are not supported yet.
    accelerator_dict = None
    resources = task.best_resources
    if resources is not None:
        accelerator_dict = resources.get_accelerators()
    return accelerator_dict


def _log_hint_for_redirected_outputs(log_dir: str, cluster_yaml: str) -> None:
    colorama.init()
    fore = colorama.Fore
    style = colorama.Style
    logger.info(f'{fore.CYAN}Logs will not be streamed (stream_logs=False).'
                f'{style.RESET_ALL} Hint: task outputs are redirected to '
                f'{style.BRIGHT}{log_dir}{style.RESET_ALL} on the cluster. '
                f'To monitor: ray exec {cluster_yaml} '
                f'"tail -f {log_dir}/*.log"\n'
                f'(To view the task names: ray exec {cluster_yaml} '
                f'"ls {log_dir}/")')


def _ssh_options_list(ssh_private_key: Optional[str],
                      ssh_control_path: str,
                      *,
                      timeout=30) -> List[str]:
    """Returns a list of sane options for 'ssh'."""
    # Forked from Ray SSHOptions:
    # https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/command_runner.py
    arg_dict = {
        # Supresses initial fingerprint verification.
        'StrictHostKeyChecking': 'no',
        # SSH IP and fingerprint pairs no longer added to known_hosts.
        # This is to remove a 'REMOTE HOST IDENTIFICATION HAS CHANGED'
        # warning if a new node has the same IP as a previously
        # deleted node, because the fingerprints will not match in
        # that case.
        'UserKnownHostsFile': os.devnull,
        # Try fewer extraneous key pairs.
        'IdentitiesOnly': 'yes',
        # Abort if port forwarding fails (instead of just printing to
        # stderr).
        'ExitOnForwardFailure': 'yes',
        # Quickly kill the connection if network connection breaks (as
        # opposed to hanging/blocking).
        'ServerAliveInterval': 5,
        'ServerAliveCountMax': 3,
        # Control path: important optimization as we do multiple ssh in one
        # sky.execute().
        'ControlMaster': 'auto',
        'ControlPath': '{}/%C'.format(ssh_control_path),
        'ControlPersist': '30s',
        # ConnectTimeout.
        'ConnectTimeout': '{}s'.format(timeout),
    }
    ssh_key_option = [
        '-i',
        ssh_private_key,
    ] if ssh_private_key is not None else []
    return ssh_key_option + [
        x for y in (['-o', '{}={}'.format(k, v)]
                    for k, v in arg_dict.items()
                    if v is not None) for x in y
    ]


def _add_cluster_to_ssh_config(cluster_name: str, cluster_ip: str,
                               auth_config: Dict[str, str]) -> None:
    backend_utils.SSHConfigHelper.add_cluster(cluster_name, cluster_ip,
                                              auth_config)


def _remove_cluster_from_ssh_config(cluster_ip: str,
                                    auth_config: Dict[str, str]) -> None:
    backend_utils.SSHConfigHelper.remove_cluster(cluster_ip, auth_config)


class RayCodeGen(object):
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
        self._ip_to_bundle_index = None

        # job_id
        self.job_id = None

    def add_prologue(self, job_id: int, stream_logs: bool) -> None:
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
            from typing import Dict, List, Optional, Union

            import ray
            import ray.util as ray_util

            sys.path.append('{SKY_REMOTE_UTIL_PATH}')
            import job_utils
            
            job_utils.change_status({job_id!r}, {JobStatus.PENDING.value!r})

            ray.init('auto', namespace='__sky__', log_to_driver={stream_logs})

            print('cluster_resources:', ray.cluster_resources())
            print('available_resources:', ray.available_resources())
            print('live nodes:', ray.state.node_ids())
            
            futures = []"""),
            inspect.getsource(log_utils.redirect_process_output),
            inspect.getsource(log_utils.run_with_log),
            inspect.getsource(log_utils.run_bash_command_with_log),
            'run_bash_command_with_log = ray.remote(run_bash_command_with_log)',
        ]

    def add_gang_scheduling_placement_group(
            self,
            ip_list: List[str],
            accelerator_dict: Dict[str, int],
    ) -> List[Dict[str, int]]:
        """Create the resource_handle for gang scheduling for n_tasks."""
        assert self._has_prologue, 'Call add_prologue() before add_ray_task().'
        bundles = [
            {
                # Set CPU to avoid ray hanging the resources allocation
                # for remote functions, since the task will request 1 CPU
                # by default.
                'CPU': 1,
                f'node:{ip}': 0.01
            } for ip in ip_list
        ]
        if accelerator_dict is not None:
            gpu_dict = {'GPU': list(accelerator_dict.values())[0]}
            for bundle in bundles:
                bundle.update({
                    **accelerator_dict,
                    # Set the GPU to avoid ray hanging the resources allocation
                    **gpu_dict,
                })
        self._ip_to_bundle_index = {ip: i for i, ip in enumerate(ip_list)}

        self._code += [
            textwrap.dedent(f"""\
                pg = ray_util.placement_group({json.dumps(bundles)}, \'STRICT_SPREAD\')
                print(\'Reserving task slots on {len(bundles)} nodes.\', flush=True)
                # FIXME: This will print the error message from autoscaler if
                # it is waiting for other task to finish. We should hide the
                # error message.
                ray.get(pg.ready())
                print(\'SKY INFO: All task slots reserved.\')
                job_utils.change_status({self.job_id!r}, {JobStatus.RUNNING.value!r})
                """),
        ]

    def add_ray_task(
            self,
            bash_script: str,
            task_name: Optional[str],
            ray_resources_dict: Optional[Dict[str, float]],
            log_path: str,
            stream_logs: bool,
            gang_scheduling_ip: str = None,
    ) -> None:
        """Generates code for a ray remote task that runs a bash command."""
        assert self._has_prologue, 'Call add_prologue() before add_ray_task().'
        assert gang_scheduling_ip is None or \
            self._ip_to_bundle_index is not None, \
            'Call add_gang_scheduling_placement_group() before add_ray_task().'

        # Build remote_task.options(...)
        #   name=...
        #   resources=...
        #   num_gpus=...
        name_str = f'name=\'{task_name}\''
        if task_name is None:
            # Make the task name more meaningful in ray log.
            name_str = 'name=\'task\''

        if ray_resources_dict is None:
            resources_str = ''
            num_gpus_str = ''
        else:
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

        if gang_scheduling_ip is not None:
            bundle_index = self._ip_to_bundle_index[gang_scheduling_ip]
            resources_str = ', placement_group=pg'
            resources_str += f', placement_group_bundle_index={bundle_index}'
        logger.debug(
            f'Added Task with options: {name_str}{resources_str}{num_gpus_str}')
        self._code += [
            textwrap.dedent(f"""\
        futures.append(run_bash_command_with_log \\
                .options({name_str}{resources_str}{num_gpus_str}) \\
                .remote(
                    {repr(bash_script)},
                    '{log_path}',
                    stream_logs={stream_logs},
                ))""")
        ]

    def add_epilogue(self) -> None:
        """Generates code that waits for all tasks, then exits."""
        assert self._has_prologue, 'Call add_prologue() before add_epilogue().'
        assert not self._has_epilogue, 'add_epilogue() called twice?'
        self._has_epilogue = True

        self._code.append('ray.get(futures)')
        self._code.append('print(\'SKY INFO: All tasks finished.\')')

    def build(self) -> str:
        """Returns the entire generated program."""
        assert self._has_epilogue, 'Call add_epilogue() before build().'
        return '\n'.join(self._code)


class RetryingVmProvisioner(object):
    """A provisioner that retries different cloud/regions/zones."""

    def __init__(self, log_dir: str, dag: Dag, optimize_target: OptimizeTarget):
        self._blocked_regions = set()
        self._blocked_zones = set()
        self._blocked_launchable_resources = set()

        self.log_dir = log_dir
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
                raise ValueError(
                    'Errors occurred during file_mounts/setup command; '
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
            raise ValueError(
                'Errors occurred during file_mounts/setup command; '
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
            assert False, \
                'Errors occurred during setup command; check logs above.'

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

    def _yield_region_zones(self, to_provision: Resources, cluster_name: str):
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
        if region is not None:
            region = clouds.Region(name=region)
            if zones is not None:
                zones = [clouds.Zone(name=zone) for zone in zones.split(',')]
                region.set_zones(zones)
            yield (region, zones)  # Ok to yield again in the next loop.
        for region, zones in cloud.region_zones_provision_loop(
                instance_type=to_provision.instance_type,
                accelerators=to_provision.accelerators,
                use_spot=to_provision.use_spot,
        ):
            yield (region, zones)

    def _try_provision_tpu(self, to_provision: Resources, acc_args,
                           config_dict) -> bool:
        """Returns whether the provision is successful."""
        tpu_name = acc_args['tpu_name']
        assert 'tpu-create-script' in config_dict, \
            'Expect TPU provisioning with gcloud.'
        try:
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
                logger.info(f'TPU {tpu_name} already exists; skipped creation.')
                return True

            if 'PERMISSION_DENIED' in stderr:
                logger.info('TPUs are not available in this zone.')
                return False

            if 'no more capacity in the zone' in stderr:
                logger.info('No more capacity in this zone.')
                return False

            if 'CloudTpu received an invalid AcceleratorType' in stderr:
                # INVALID_ARGUMENT: CloudTpu received an invalid
                # AcceleratorType, "v3-8" for zone "us-central1-c". Valid
                # values are "v2-8, ".
                tpu_type = list(to_provision.accelerators.keys())[0]
                logger.info(
                    f'TPU type {tpu_type} is not available in this zone.')
                return False

            logger.error(stderr)
            raise e

    def _retry_region_zones(self, task: App, to_provision: Resources,
                            dryrun: bool, stream_logs: bool, cluster_name: str,
                            prev_cluster_config: Optional[Dict[str, Any]]):
        """The provision retry loop."""
        style = colorama.Style
        # Get log_path name
        log_path = os.path.join(self.log_dir, 'provision.log')
        log_abs_path = os.path.abspath(log_path)
        tail_cmd = f'tail -n100 -f {log_path}'
        logger.info('To view detailed progress: '
                    f'{style.BRIGHT}{tail_cmd}{style.RESET_ALL}')

        self._clear_blocklist()
        for region, zones in self._yield_region_zones(to_provision,
                                                      cluster_name):
            if self._in_blocklist(to_provision.cloud, region, zones):
                continue
            zone_str = ','.join(
                z.name for z in zones) if zones is not None else 'all zones'
            logger.info(f'\n{style.BRIGHT}Launching on {to_provision.cloud} '
                        f'{region.name} ({zone_str}){style.RESET_ALL}')
            config_dict = backend_utils.write_cluster_config(
                None,
                task,
                to_provision,
                _get_cluster_config_template(to_provision.cloud),
                region=region,
                zones=zones,
                dryrun=dryrun,
                cluster_name=cluster_name)
            if dryrun:
                return
            acc_args = to_provision.accelerator_args
            tpu_name = None
            if acc_args is not None and acc_args.get('tpu_name') is not None:
                success = self._try_provision_tpu(to_provision, acc_args,
                                                  config_dict)
                if not success:
                    continue
                tpu_name = acc_args['tpu_name']
            cluster_config_file = config_dict['ray']

            # Record early, so if anything goes wrong, 'sky status' will show
            # the cluster name and users can appropriately 'sky down'.  It also
            # means a second 'sky run -c <name>' will attempt to reuse.
            handle = CloudVmRayBackend.ResourceHandle(
                cluster_name=cluster_name,
                cluster_yaml=cluster_config_file,
                requested_nodes=task.num_nodes,
                # OK for this to be shown in CLI as status == INIT.
                launched_resources=to_provision.fill_accelerators(),
                tpu_delete_script=config_dict.get('tpu-delete-script'))
            global_user_state.add_or_update_cluster(cluster_name,
                                                    cluster_handle=handle,
                                                    ready=False)

            gang_failed, proc, stdout, stderr = self._gang_schedule_ray_up(
                task, to_provision.cloud, cluster_config_file, log_abs_path,
                stream_logs, prev_cluster_config)

            if gang_failed or proc.returncode != 0:
                if gang_failed:
                    # There exist partial nodes (e.g., head node) so we must
                    # down before moving on to other regions.
                    # TODO: this signals refactoring opportunities?
                    CloudVmRayBackend().teardown(handle, terminate=True)
                    self._update_blocklist_on_error(
                        to_provision.cloud,
                        region,
                        # Ignored and block region:
                        zones=None,
                        stdout=None,
                        stderr=None)
                else:
                    self._update_blocklist_on_error(to_provision.cloud, region,
                                                    zones, stdout, stderr)
            else:
                # Success.
                if tpu_name is not None:
                    # TODO: refactor to a cleaner design, so that tpu code and
                    # ray up logic are not mixed up.
                    backend_utils.run(f'ray exec {cluster_config_file} '
                                      f'\'echo "export TPU_NAME={tpu_name}" > '
                                      f'{SKY_REMOTE_APP_DIR}/sky_env_var.sh\'')
                cluster_name = config_dict['cluster_name']
                plural = '' if task.num_nodes == 1 else 's'
                logger.info(
                    f'{style.BRIGHT}Successfully provisioned or found'
                    f' existing VM{plural}. Setup completed.{style.RESET_ALL}')
                logger.info(f'\nTo log into the head VM:\t{style.BRIGHT}ssh'
                            f' {cluster_name}{style.RESET_ALL}\n')
                return config_dict
        message = ('Failed to acquire resources in all regions/zones'
                   f' (requested {to_provision}).'
                   ' Try changing resource requirements or use another cloud.')
        logger.error(message)
        raise exceptions.ResourcesUnavailableError()

    def _gang_schedule_ray_up(self, task: App, to_provision_cloud: clouds.Cloud,
                              cluster_config_file: str, log_abs_path: str,
                              stream_logs: bool,
                              prev_cluster_config: Optional[Dict[str, Any]]):
        """Provisions a cluster via 'ray up' with gang scheduling.

        Steps for provisioning > 1 node:
          1) ray up an empty config (no filemounts, no setup commands)
          2) ray up a full config (with filemounts and setup commands)

        For provisioning 1 node, only step 2 is run.

        Benefits:
          - Ensures all nodes are allocated first before potentially expensive
            file_mounts/setup.  (If not all nodes aren't allocated, step 1
            would fail.)

        Returns:
          (did gang scheduling failed; proc; stdout; stderr).
        """
        style = colorama.Style

        def ray_up(start_streaming_at):
            # Redirect stdout/err to the file and streaming (if stream_logs).
            proc, stdout, stderr = backend_utils.run_with_log(
                # NOTE: --no-restart solves the following bug.  Without it, if
                # 'ray up' (sky run) twice on a cluster with >1 node, the
                # worker node gets disconnected/killed by ray autoscaler; the
                # whole task will just freeze.  (Doesn't affect 1-node
                # clusters.)  With this flag, ray processes no longer restart
                # and this bug doesn't show.  Downside is existing tasks on the
                # cluster will keep running (which may be ok with the semantics
                # of 'sky run' twice).
                # Tracked in https://github.com/ray-project/ray/issues/20402.
                ['ray', 'up', '-y', '--no-restart', cluster_config_file],
                log_abs_path,
                stream_logs,
                start_streaming_at=start_streaming_at)
            return proc, stdout, stderr

        def is_cluster_yaml_identical():
            if prev_cluster_config is None:
                return False
            curr_config = backend_utils.read_yaml(cluster_config_file)
            curr = json.dumps(curr_config, sort_keys=True)
            prev = json.dumps(prev_cluster_config, sort_keys=True)
            return curr == prev

        ray_up_on_full_confg_only = False
        # Fast paths for "no need to gang schedule":
        #  (1) task.num_nodes == 1, no need to pre-provision.
        #  (2) otherwise, if cluster configs have not changed, no need either.
        #    TODO: can relax further: if (num_nodes x requested_resources) now
        #    == existing, then skip Step 1.  Requested resources =
        #    cloud(instance_type, acc, acc_args).  If requested clouds are
        #    different, we still need to gang schedule on the new cloud.
        if task.num_nodes == 1:
            # ray up the full yaml.
            proc, stdout, stderr = ray_up(
                start_streaming_at='Shared connection to')
            return False, proc, stdout, stderr
        elif is_cluster_yaml_identical():
            ray_up_on_full_confg_only = True
            # NOTE: consider the exceptional case where cluster yamls are
            # identical, but existing cluster has a few nodes killed (e.g.,
            # spot).  For that case, we ray up once on the full config, instead
            # of doing gang schedule first.  This seems an ok tradeoff because
            # (1) checking how many nodes remain maybe slow, a price the common
            # cases should not pay; (2) setup on the (presumably live) head
            # node is likely no-op.  We can revisit.

        # Step 1: ray up the emptied config.
        if not ray_up_on_full_confg_only:
            config = backend_utils.read_yaml(cluster_config_file)
            pinned_ray_install = ('pip3 install -U '
                                  f'ray[default]=={SKY_REMOTE_RAY_VERSION}')

            file_mounts = {}
            if 'ssh_public_key' in config['auth']:
                # For Azure, we need to add ssh public key to VM by filemounts.
                public_key_path = config['auth']['ssh_public_key']
                file_mounts[public_key_path] = public_key_path

            fields_to_empty = {
                'file_mounts': file_mounts,
                # Need ray for 'ray status' in wait_until_ray_cluster_ready().
                'setup_commands': [config['setup_commands'][0]],
            }
            existing_fields = {k: config[k] for k in fields_to_empty}
            # Keep both in sync.
            assert pinned_ray_install in existing_fields['setup_commands'][
                0], existing_fields['setup_commands']
            config.update(fields_to_empty)
            backend_utils.dump_yaml(cluster_config_file, config)

        proc, stdout, stderr = ray_up(start_streaming_at='Shared connection to')
        if proc.returncode != 0:
            # Head node provisioning failure.
            return False, proc, stdout, stderr

        logger.info(f'{style.BRIGHT}Successfully provisioned or found'
                    f' existing head VM. Waiting for workers.{style.RESET_ALL}')

        # TODO: if requesting a large amount (say 32) of expensive VMs, this
        # may loop for a long time.  Use timeouts and treat as gang_failed.
        cluster_ready = backend_utils.wait_until_ray_cluster_ready(
            to_provision_cloud, cluster_config_file, task.num_nodes)
        gang_failed = not cluster_ready
        if gang_failed or ray_up_on_full_confg_only:
            # Head OK; gang scheduling failure.
            return gang_failed, proc, stdout, stderr

        # Step 2: ray up the full config (file mounts, setup).
        config.update(existing_fields)
        backend_utils.dump_yaml(cluster_config_file, config)
        logger.info('Starting to set up the cluster.')
        proc, stdout, stderr = ray_up(
            # FIXME: Not ideal. The setup log (pip install) will be streamed
            # first, before the ray autoscaler's step-by-step output (<1/1>
            # Setting up head node). Probably some buffering or
            # stdout-vs-stderr bug?  We want the latter to show first, not
            # printed in t he end.
            start_streaming_at='Shared connection to')

        return False, proc, stdout, stderr

    def provision_with_retries(self, task: App, to_provision: Resources,
                               dryrun: bool, stream_logs: bool,
                               cluster_name: str):
        """Provision with retries for all launchable resources."""
        assert cluster_name is not None, 'cluster_name must be specified.'
        launchable_retries_disabled = (self._dag is None or
                                       self._optimize_target is None)
        prev_handle = global_user_state.get_handle_from_cluster_name(
            cluster_name)
        if prev_handle is None:
            prev_cluster_config = None
        else:
            try:
                prev_cluster_config = backend_utils.read_yaml(
                    prev_handle.cluster_yaml)
            except FileNotFoundError:
                prev_cluster_config = None
        style = colorama.Style
        # Retrying launchable resources.
        provision_failed = True
        while provision_failed:
            provision_failed = False
            try:
                config_dict = self._retry_region_zones(
                    task,
                    to_provision,
                    dryrun=dryrun,
                    stream_logs=stream_logs,
                    cluster_name=cluster_name,
                    prev_cluster_config=prev_cluster_config)
                if dryrun:
                    return
                config_dict['launched_resources'] = to_provision
            except exceptions.ResourcesUnavailableError as e:
                if launchable_retries_disabled:
                    logger.warning(
                        'DAG and optimize_target needs to be registered first '
                        'to enable cross-cloud retry. '
                        'To fix, call backend.register_info(dag=dag, '
                        'optimize_target=sky.OptimizeTarget.COST)')
                    raise e
                provision_failed = True
                logger.warning(
                    f'\n{style.BRIGHT}Provision failed for {to_provision}. '
                    'Trying other launchable resources (if any)...'
                    f'{style.RESET_ALL}')
                # Add failed resources to the blocklist.
                self._blocked_launchable_resources.add(to_provision)
                # Set to None so that sky.optimize() will assign a new one
                # (otherwise will skip re-optimizing this task).
                # TODO: set all remaining tasks' best_resources to None.
                task.best_resources = None
                self._dag = sky.optimize(self._dag,
                                         minimize=self._optimize_target,
                                         blocked_launchable_resources=self.
                                         _blocked_launchable_resources)
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

    class ResourceHandle(object):
        """A pickle-able tuple of:

        - (required) Cluster name.
        - (required) Path to a cluster.yaml file.
        - (optional) A cached head node public IP.  Filled in after a
            successful provision().
        - (optional) Requested num nodes
        - (optional) Launched resources
        - (optional) If TPU(s) are managed, a path to a deletion script.
        """

        def __init__(self,
                     *,
                     cluster_name: str,
                     cluster_yaml: str,
                     head_ip: Optional[str] = None,
                     requested_nodes: Optional[int] = None,
                     launched_resources: Optional[Resources] = None,
                     tpu_delete_script: Optional[str] = None) -> None:
            self.cluster_name = cluster_name
            self.cluster_yaml = cluster_yaml
            self.head_ip = head_ip
            self.requested_nodes = requested_nodes
            self.launched_resources = launched_resources
            self.tpu_delete_script = tpu_delete_script

        def __repr__(self):
            return (f'ResourceHandle(\n\thead_ip={self.head_ip},'
                    '\n\tcluster_yaml='
                    f'{backend_utils.get_rel_path(self.cluster_yaml)}, '
                    f'\n\tlaunched_resources={self.requested_nodes}x '
                    f'{self.launched_resources}, '
                    f'\n\ttpu_delete_script={self.tpu_delete_script})')

    def __init__(self):
        run_id = backend_utils.get_run_id()
        self.log_dir = os.path.join(SKY_LOGS_DIRECTORY, run_id)
        os.makedirs(self.log_dir, exist_ok=True)

        self._dag = None
        self._optimize_target = None

    def register_info(self, **kwargs) -> None:
        self._dag = kwargs['dag']
        self._optimize_target = kwargs.pop('optimize_target',
                                           OptimizeTarget.COST)

    def _check_resources_available_for_task(self, handle: ResourceHandle,
                                            task: App):
        """Check if resources requested by the task are available."""
        # requested_resources <= actual_resources.
        if not (task.num_nodes <= handle.requested_nodes and
                backend_utils.requested_resources_available(
                    handle.launched_resources, task.resources)):
            cluster_name = handle.cluster_name
            raise exceptions.ResourcesMismatchError(
                'Requested resources do not match the existing cluster.\n'
                f'  Requested: {task.num_nodes}x {task.resources}\n'
                f'  Existing: {handle.requested_nodes}x '
                f'{handle.launched_resources}\n'
                f'To fix: specify a new cluster name, or down the '
                f'existing cluster first: `sky down {cluster_name}`.')
        task.best_resources = list(task.resources)[0]
        return True

    def _check_existing_cluster(self, task: App, to_provision: Resources,
                                cluster_name: str) -> Tuple[str, Resources]:
        handle = global_user_state.get_handle_from_cluster_name(cluster_name)
        if handle is not None:
            # Cluster already exists.
            self._check_resources_available_for_task(handle, task)
            # Use the existing cluster.
            assert handle.launched_resources is not None, (cluster_name, handle)
            return cluster_name, handle.launched_resources
        logger.info(
            f'{colorama.Fore.CYAN}Creating a new cluster: "{cluster_name}" '
            f'[{task.num_nodes}x {to_provision}].{colorama.Style.RESET_ALL}\n'
            'Tip: to reuse an existing cluster, '
            'specify --cluster-name (-c) in the CLI or use '
            'sky.execute(.., cluster_name=..) in the Python API. '
            'Run `sky status` to see existing clusters.')
        return cluster_name, to_provision

    def provision(self,
                  task: App,
                  to_provision: Resources,
                  dryrun: bool,
                  stream_logs: bool,
                  cluster_name: Optional[str] = None):
        """Provisions using 'ray up'."""
        # Try to launch the exiting cluster first
        if cluster_name is None:
            # TODO: change this ID formatting to something more pleasant.
            # User name is helpful in non-isolated accounts, e.g., GCP, Azure.
            cluster_name = f'sky-{uuid.uuid4().hex[:4]}-{getpass.getuser()}'
        # ray up: the VMs.
        # FIXME: ray up for Azure with different cluster_names will overwrite
        # each other.
        provisioner = RetryingVmProvisioner(self.log_dir, self._dag,
                                            self._optimize_target)

        if not dryrun:  # dry run doesn't need to check existing cluster.
            cluster_name, to_provision = self._check_existing_cluster(
                task, to_provision, cluster_name)
        try:
            config_dict = provisioner.provision_with_retries(
                task, to_provision, dryrun, stream_logs, cluster_name)
        except exceptions.ResourcesUnavailableError as e:
            raise exceptions.ResourcesUnavailableError(
                'Failed to provision all possible launchable resources. '
                f'Relax the task\'s resource requirements:\n{task.resources}'
            ) from e
        if dryrun:
            return
        cluster_config_file = config_dict['ray']
        provisioned_resources = config_dict['launched_resources']

        handle = self.ResourceHandle(
            cluster_name=cluster_name,
            cluster_yaml=cluster_config_file,
            # Cache head ip in the handle to speed up ssh operations.
            head_ip=self._get_node_ips(cluster_config_file, task.num_nodes)[0],
            requested_nodes=task.num_nodes,
            launched_resources=provisioned_resources.fill_accelerators(),
            # TPU.
            tpu_delete_script=config_dict.get('tpu-delete-script'))
        global_user_state.add_or_update_cluster(cluster_name,
                                                handle,
                                                ready=True)
        auth_config = backend_utils.read_yaml(handle.cluster_yaml)['auth']
        _add_cluster_to_ssh_config(cluster_name, handle.head_ip, auth_config)
        return handle

    def sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        # Even though provision() takes care of it, there may be cases where
        # this function is called in isolation, without calling provision(),
        # e.g., in CLI.  So we should rerun rsync_up.
        # TODO: this only syncs to head.
        self._run_rsync(handle,
                        source=f'{workdir}/',
                        target=SKY_REMOTE_WORKDIR,
                        with_outputs=True)

    def sync_file_mounts(
            self,
            handle: ResourceHandle,
            all_file_mounts: Dict[Path, Path],
            cloud_to_remote_file_mounts: Optional[Dict[Path, Path]],
    ) -> None:
        # TODO: this function currently only syncs to head.
        # 'all_file_mounts' should already have been handled in provision()
        # using the yaml file.  Here we handle cloud -> remote file transfers.
        # FIXME: if called out-of-band without provision() first, we actually
        # need to handle all_file_mounts again.
        mounts = cloud_to_remote_file_mounts
        if mounts is None or not mounts:
            return
        fore = colorama.Fore
        style = colorama.Style
        cyan = fore.CYAN
        reset = style.RESET_ALL
        bright = style.BRIGHT
        logger.info(f'{cyan}Processing cloud to VM file mounts.{reset}')
        for dst, src in mounts.items():
            # TODO: room for improvement.  Here there are many moving parts
            # (download gsutil on remote, run gsutil on remote).  Consider
            # alternatives (smart_open, each provider's own sdk), a
            # data-transfer container etc.
            if not os.path.isabs(dst) and not dst.startswith('~/'):
                dst = f'~/{dst}'
            use_symlink_trick = True
            # Sync 'src' to 'wrapped_dst', a safe-to-write "wrapped" path.
            if dst.startswith('~/') or dst.startswith('/tmp/'):
                # Skip as these should be writable locations.
                wrapped_dst = dst
                use_symlink_trick = False
            else:
                wrapped_dst = backend_utils.FileMountHelper.wrap_file_mount(dst)
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
            if not use_symlink_trick:
                command = ' && '.join(download_target_commands)
            else:
                # Goal: point dst --> wrapped_dst.
                command = (
                    backend_utils.FileMountHelper.make_safe_symlink_command(
                        source=dst,
                        target=wrapped_dst,
                        download_target_commands=download_target_commands))
            log_path = os.path.join(self.log_dir,
                                    'file_mounts_cloud_to_remote.log')
            logger.info(f'{cyan} Syncing: {bright}{src} -> {dst}{reset}')
            # TODO: filter out ray boilerplate: Setting `max_workers` for node
            # type ... try re-running the command with --no-config-cache.
            proc, unused_stdout, unused_stderr = backend_utils.run_with_log(
                f'ray exec {handle.cluster_yaml} \'{command}\'',
                os.path.abspath(log_path),
                stream_logs=True,
                shell=True)
            if proc.returncode:
                raise ValueError(
                    f'File mounts\n\t{src} -> {dst}\nfailed to sync. '
                    f'See errors above and log: {log_path}')

    def run_post_setup(self, handle: ResourceHandle, post_setup_fn: PostSetupFn,
                       task: App) -> None:
        ip_list = self._get_node_ips(handle.cluster_yaml, task.num_nodes)
        config = backend_utils.read_yaml(handle.cluster_yaml)
        ssh_user = config['auth']['ssh_user'].strip()
        ip_to_command = post_setup_fn(ip_list)
        for ip, cmd in ip_to_command.items():
            if cmd is not None:
                cmd = (f'mkdir -p {SKY_REMOTE_WORKDIR} && '
                       f'cd {SKY_REMOTE_WORKDIR} && {cmd}')
                backend_utils.run_command_on_ip_via_ssh(ip,
                                                        cmd,
                                                        task.private_key,
                                                        task.container_name,
                                                        ssh_user=ssh_user)

    def _execute_par_task(self, handle: ResourceHandle,
                          par_task: task_mod.ParTask, job_id: int,
                          stream_logs: bool) -> None:
        # Case: ParTask(tasks), t.num_nodes == 1 for t in tasks
        for task in par_task.tasks:
            assert task.num_nodes == 1, \
                ('ParTask does not support inner Tasks with '
                f'num_nodes > 1: {task}')
        # Strategy:
        #  ray.init(..., log_to_driver=False); otherwise too many logs.
        #  for task:
        #    submit _run_cmd(cmds[i]) with resource {task i's resource}
        # Concrete impl. of the above: codegen a script that contains all the
        # tasks, rsync the script to head, and run that script on head.

        # We cannot connect from this local node to the remote Ray cluster
        # using a Ray client, because the default port 10001 may not be open to
        # this local node.
        #
        # One downside(?) of client mode is to dictate local machine having the
        # same python & ray versions as the cluster.  We can plumb through the
        # yamls to take care of it.  The upsides are many-fold (e.g., directly
        # manipulating the futures).
        #
        # TODO: possible to open the port in the yaml?  Run Ray inside docker?
        log_dir_base = os.path.join(f'{SKY_REMOTE_WORKDIR}', f'{self.log_dir}')
        log_dir = os.path.join(log_dir_base, 'tasks')

        ips = self._get_node_ips(handle.cluster_yaml,
                                 par_task.num_nodes,
                                 return_private_ips=True)
        accelerator_dict = _get_task_demands_dict(par_task)

        codegen = RayCodeGen()
        codegen.add_prologue(job_id, stream_logs=stream_logs)
        codegen.add_gang_scheduling_placement_group(ips, accelerator_dict)
        for i, task_i in enumerate(par_task.tasks):
            # '. $(conda info --base)/etc/profile.d/conda.sh || true' is used
            # to initialize conda, so that 'conda activate ...' works.
            task_i_script = backend_utils.make_task_bash_script(task_i.run)
            task_i_name = f'task-{i}' if task_i.name is None else task_i.name
            codegen.add_ray_task(
                bash_script=task_i_script,
                task_name=task_i_name,
                # We can't access t.best_resources because the inner task
                # doesn't undergo optimization.  Example value: {"V100": 1}.
                ray_resources_dict=par_task.get_task_resource_demands(i),
                log_path=os.path.join(log_dir, f'{task_i_name}.log'),
                stream_logs=stream_logs)
        codegen.add_epilogue()
        code = codegen.build()

        # Logger.
        colorama.init()
        fore = colorama.Fore
        style = colorama.Style
        logger.info(f'{fore.CYAN}Starting ParTask execution.{style.RESET_ALL}')
        if not stream_logs:
            _log_hint_for_redirected_outputs(log_dir, handle.cluster_yaml)

        self._exec_code_on_head(handle, code, job_id, executable='python3')

    def _rsync_down_logs(self,
                         handle: ResourceHandle,
                         log_dir: str,
                         ips: List[str] = None):
        local_log_dir = os.path.join(f'{self.log_dir}', 'tasks')
        style = colorama.Style
        logger.info('Syncing down logs to '
                    f'{style.BRIGHT}{local_log_dir}{style.RESET_ALL}')
        os.makedirs(local_log_dir, exist_ok=True)
        # Call the ray sdk to rsync the logs back to local.
        # FIXME: can we make rsync not verbose here (-v)?
        for ip in ips:
            sdk.rsync(
                handle.cluster_yaml,
                source=f'{log_dir}/*',
                target=f'{local_log_dir}',
                down=True,
                ip_address=ip,
                use_internal_ip=False,
                should_bootstrap=False,
            )

    def _exec_code_on_head(
            self,
            handle: ResourceHandle,
            codegen: str,
            job_id: int,
            executable: str,
            stream_logs: bool = True,
    ) -> None:
        """Executes generated code on the head node."""
        with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
            fp.write(codegen)
            fp.flush()
            script_path = os.path.join(SKY_REMOTE_APP_DIR, f'sky_job_{job_id}')
            # We choose to sync code + exec, because the alternative of 'ray
            # submit' may not work as it may use system python (python2) to
            # execute the script.  Happens for AWS.
            self._run_rsync(handle,
                            source=fp.name,
                            target=script_path,
                            with_outputs=False)

        job_log_path = os.path.join(self.log_dir, 'job_submit.log')
        remote_log_dir = os.path.join(SKY_REMOTE_WORKDIR, self.log_dir)
        remote_log_path = os.path.join(remote_log_dir, 'run.log')

        assert executable == 'python3', executable
        cd = f'cd {SKY_REMOTE_WORKDIR}'

        # TODO: Add detach option.
        # TODO: Pipe the output to one file.
        # TODO: Add hint for the user to monitor the job log.
        job_submit_cmd = (
            f'mkdir -p {remote_log_dir} && ray job submit '
            f'--address=127.0.0.1:8265 --job-id {job_id} -- '
            f'"{executable} -u {script_path} 2>&1 > {remote_log_path}"')

        # TODO: Show our own information for job submission.
        self._run_command_on_head_via_ssh(handle, f'{cd} && {job_submit_cmd}',
                                          job_log_path, stream_logs)
        if stream_logs:
            colorama.init()
            style = colorama.Style
            logger.info(
                f'To stream logs: '
                f'{style.BRIGHT}sky logs -c {handle.cluster_name} {job_id}'
                f'{style.RESET_ALL}')

    def _fetch_job_id(self, handle: ResourceHandle) -> int:
        run_id = os.path.basename(self.log_dir)
        codegen = backend_utils.JobUtilsCodeGen()
        username = getpass.getuser()
        codegen.reserve_next_job_id(username, run_id)
        job_id = self._run_command_on_head_via_ssh(handle,
                                                   codegen.build(),
                                                   '/dev/null',
                                                   stream_logs=False)[1]
        job_id = int(job_id)
        return job_id

    def execute(self, handle: ResourceHandle, task: App,
                stream_logs: bool) -> None:
        # Check the task resources vs the cluster resources. Since `sky exec`
        # will not run the provision and _check_existing_cluster
        self._check_resources_available_for_task(handle, task)

        # Execution logic differs for three types of tasks.
        # Case: ParTask(tasks), t.num_nodes == 1 for t in tasks
        if isinstance(task, task_mod.ParTask):
            job_id = self._fetch_job_id(handle)
            return self._execute_par_task(handle, task, job_id, stream_logs)

        # Otherwise, handle a basic Task.
        if task.run is None:
            logger.info(f'Nothing to run; run command not specified:\n{task}')
            return

        job_id = self._fetch_job_id(handle)

        # Case: Task(run, num_nodes=1)
        if task.num_nodes == 1:
            return self._execute_task_one_node(handle, task, job_id,
                                               stream_logs)

        # Case: Task(run, num_nodes=N)
        assert task.num_nodes > 1, task.num_nodes
        return self._execute_task_n_nodes(handle, task, job_id, stream_logs)

    def _execute_task_one_node(self, handle: ResourceHandle, task: App,
                               job_id: int, stream_logs: bool) -> None:
        # Launch the command as a Ray task.
        assert isinstance(task.run, str), \
            f'Task(run=...) should be a string (found {type(task.run)}).'
        script = backend_utils.make_task_bash_script(task.run)

        log_dir = os.path.join(f'{SKY_REMOTE_WORKDIR}', f'{self.log_dir}',
                               'tasks')
        log_path = os.path.join(log_dir, 'run.log')

        ips = self._get_node_ips(handle.cluster_yaml,
                                 task.num_nodes,
                                 return_private_ips=True)
        accelerator_dict = _get_task_demands_dict(task)

        codegen = RayCodeGen()
        codegen.add_prologue(job_id, stream_logs=stream_logs)
        codegen.add_gang_scheduling_placement_group(ips, accelerator_dict)

        codegen.add_ray_task(
            bash_script=script,
            task_name=task.name,
            ray_resources_dict=_get_task_demands_dict(task),
            log_path=log_path,
            stream_logs=stream_logs,
            gang_scheduling_ip=ips[0],
        )

        codegen.add_epilogue()

        self._exec_code_on_head(handle,
                                codegen.build(),
                                job_id,
                                executable='python3')

    def _execute_task_n_nodes(self, handle: ResourceHandle, task: App,
                              job_id: int, stream_logs: bool) -> None:
        # Strategy:
        #   ray.init(..., log_to_driver=False); otherwise too many logs.
        #   for node:
        #     submit _run_cmd(cmd) with resource {node_i: 1}
        log_dir_base = os.path.join(f'{SKY_REMOTE_WORKDIR}', f'{self.log_dir}')
        log_dir = os.path.join(log_dir_base, 'tasks')
        # Get private ips here as Ray internally uses 'node:private_ip' as
        # per-node custom resources.
        ips = self._get_node_ips(handle.cluster_yaml,
                                 task.num_nodes,
                                 return_private_ips=True)
        accelerator_dict = _get_task_demands_dict(task)

        codegen = RayCodeGen()
        codegen.add_prologue(job_id, stream_logs=stream_logs)
        codegen.add_gang_scheduling_placement_group(ips, accelerator_dict)

        ips_dict = task.run(ips)
        for ip in ips_dict:
            command_for_ip = ips_dict[ip]
            script = backend_utils.make_task_bash_script(command_for_ip)

            # Ray's per-node resources, to constrain scheduling each command to
            # the corresponding node, represented by private IPs.
            name = f'{ip}'
            log_path = os.path.join(f'{log_dir}', f'{name}.log')

            codegen.add_ray_task(
                bash_script=script,
                task_name=name,
                ray_resources_dict=accelerator_dict,
                log_path=log_path,
                stream_logs=stream_logs,
                gang_scheduling_ip=ip,
            )

        codegen.add_epilogue()

        # Logger.
        colorama.init()
        fore = colorama.Fore
        style = colorama.Style
        logger.info(f'\n{fore.CYAN}Starting Task execution.{style.RESET_ALL}')
        if not stream_logs:
            _log_hint_for_redirected_outputs(log_dir, handle.cluster_yaml)

        self._exec_code_on_head(handle,
                                codegen.build(),
                                job_id,
                                executable='python3')

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        colorama.init()
        fore = colorama.Fore
        style = colorama.Style
        if not teardown:
            name = global_user_state.get_cluster_name_from_handle(handle)
            logger.info(f'\n{fore.CYAN}Cluster name: '
                        f'{style.BRIGHT}{name}{style.RESET_ALL}'
                        '\nTo log into the head VM:\t'
                        f'{style.BRIGHT}ssh {name} {style.RESET_ALL}\n'
                        '\nTo teardown the cluster:'
                        f'\t{style.BRIGHT}sky down {name}{style.RESET_ALL}\n'
                        '\nTo stop the cluster:'
                        f'\t{style.BRIGHT}sky stop {name}{style.RESET_ALL}\n')
            if handle.tpu_delete_script is not None:
                logger.info('Tip: `sky down` will delete launched TPU(s) too.')

    def teardown_ephemeral_storage(self, task: App) -> None:
        storage_mounts = task.storage_mounts
        if storage_mounts is not None:
            for storage, _ in storage_mounts.items():
                if not storage.persistent:
                    storage.delete()

    def teardown(self, handle: ResourceHandle, terminate: bool) -> None:
        cloud = handle.launched_resources.cloud
        config = backend_utils.read_yaml(handle.cluster_yaml)
        if not terminate and not isinstance(cloud, clouds.AWS):
            # FIXME: no mentions of cache_stopped_nodes in
            # https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/_azure/node_provider.py
            # https://github.com/ray-project/ray/blob/master/python/ray/autoscaler/_private/gcp/node_provider.py
            raise ValueError(
                'Node stopping requested but is not supported on non-AWS '
                'clusters yet. Try manually stopping or `sky down`. '
                f'Found: {handle.launched_resources}')
        if isinstance(cloud, clouds.Azure):
            # Special handling because `ray down` is buggy with Azure.
            cluster_name = config['cluster_name']
            # Set check=False to not error out on not found VMs.
            backend_utils.run(
                'az vm delete --yes --ids $(az vm list --query '
                f'"[? contains(name, \'{cluster_name}\')].id" -o tsv)',
                check=False)
        else:
            config['provider']['cache_stopped_nodes'] = not terminate
            with tempfile.NamedTemporaryFile('w',
                                             prefix='sky_',
                                             delete=False,
                                             suffix='.yml') as f:
                backend_utils.dump_yaml(f.name, config)
                f.flush()
                backend_utils.run(f'ray down -y {f.name}')
            if handle.tpu_delete_script is not None:
                backend_utils.run(f'bash {handle.tpu_delete_script}')
        auth_config = backend_utils.read_yaml(handle.cluster_yaml)['auth']
        _remove_cluster_from_ssh_config(handle.head_ip, auth_config)
        name = global_user_state.get_cluster_name_from_handle(handle)
        global_user_state.remove_cluster(name, terminate=terminate)

    def _get_node_ips(self,
                      cluster_yaml: str,
                      expected_num_nodes: int,
                      return_private_ips: bool = False) -> List[str]:
        """Returns the IPs of all nodes in the cluster."""
        yaml_handle = cluster_yaml
        if return_private_ips:
            config = backend_utils.read_yaml(yaml_handle)
            # Add this field to a temp file to get private ips.
            config['provider']['use_internal_ips'] = True
            yaml_handle = cluster_yaml + '.tmp'
            backend_utils.dump_yaml(yaml_handle, config)

        out = backend_utils.run(f'ray get-head-ip {yaml_handle}',
                                stdout=subprocess.PIPE).stdout.decode().strip()
        head_ip = re.findall(backend_utils.IP_ADDR_REGEX, out)
        assert 1 == len(head_ip), out

        out = backend_utils.run(f'ray get-worker-ips {yaml_handle}',
                                stdout=subprocess.PIPE).stdout.decode()
        worker_ips = re.findall(backend_utils.IP_ADDR_REGEX, out)
        assert expected_num_nodes - 1 == len(worker_ips), (expected_num_nodes -
                                                           1, out)
        if return_private_ips:
            os.remove(yaml_handle)
        return head_ip + worker_ips

    def _ssh_control_path(self, handle: ResourceHandle) -> str:
        """Returns a temporary path to be used as the ssh control path."""
        path = '/tmp/sky_ssh/{}'.format(
            hashlib.md5(handle.cluster_yaml.encode()).hexdigest()[:10])
        os.makedirs(path, exist_ok=True)
        return path

    def _run_rsync(self,
                   handle: ResourceHandle,
                   source: str,
                   target: str,
                   with_outputs: bool = True) -> None:
        """Runs rsync from 'source' to the cluster head node's 'target'."""
        # Attempt to use 'rsync user@ip' directly, which is much faster than
        # going through ray (either 'ray rsync_*' or sdk.rsync()).
        config = backend_utils.read_yaml(handle.cluster_yaml)
        if handle.head_ip is None:
            raise ValueError(
                f'The cluster "{config["cluster_name"]}" appears to be down. '
                'Run a re-provisioning command again (e.g., sky run) and retry.'
            )
        auth = config['auth']
        ssh_user = auth['ssh_user']
        ssh_private_key = auth.get('ssh_private_key')
        # Build command.
        rsync_command = ['rsync', '-a']
        ssh_options = ' '.join(
            _ssh_options_list(ssh_private_key, self._ssh_control_path(handle)))
        rsync_command.append(f'-e "ssh {ssh_options}"')
        rsync_command.extend([
            source,
            f'{ssh_user}@{handle.head_ip}:{target}',
        ])
        command = ' '.join(rsync_command)
        if with_outputs:
            backend_utils.run(command)
        else:
            backend_utils.run_no_outputs(command)

    def ssh_head_command(self,
                         handle: ResourceHandle,
                         port_forward: Optional[List[int]] = None) -> List[str]:
        """Returns a 'ssh' command that logs into a cluster's head node."""
        assert handle.head_ip is not None, \
            f'provision() should have cached head ip: {handle}'
        config = backend_utils.read_yaml(handle.cluster_yaml)
        auth = config['auth']
        ssh_user = auth['ssh_user']
        ssh_private_key = auth.get('ssh_private_key')
        # Build command.  Imitating ray here.
        ssh = ['ssh', '-tt']
        if port_forward is not None:
            for port in port_forward:
                local = remote = port
                logger.info(
                    f'Forwarding port {local} to port {remote} on localhost.')
                ssh += ['-L', '{}:localhost:{}'.format(remote, local)]
        return ssh + _ssh_options_list(
            ssh_private_key,
            self._ssh_control_path(handle)) + [f'{ssh_user}@{handle.head_ip}']

    def _run_command_on_head_via_ssh(self, handle, cmd, log_path, stream_logs):
        """Uses 'ssh' to run 'cmd' on a cluster's head node."""
        base_ssh_command = self.ssh_head_command(handle)
        command = base_ssh_command + [
            'bash',
            '--login',
            '-c',
            '-i',
            shlex.quote(f'true && source ~/.bashrc && export OMP_NUM_THREADS=1 '
                        f'PYTHONWARNINGS=ignore && ({cmd})'),
        ]
        return backend_utils.run_with_log(command, log_path, stream_logs)

    def run_on_head(self,
                    handle: ResourceHandle,
                    cmd: str,
                    stream_logs: bool = False) -> None:
        """Runs 'cmd' on the cluster's head node."""
        return self._run_command_on_head_via_ssh(handle,
                                                 cmd,
                                                 '/dev/null',
                                                 stream_logs=stream_logs)[1]

    def cancel(self, handle: ResourceHandle, job_id: str) -> None:
        """Cancels a job on cluster."""
        self._run_command_on_head_via_ssh(
            handle,
            f'ray stop job --address 127.0.0.1:8265 {job_id}',
            '/dev/null',
            False,
        )
