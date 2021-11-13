"""Backend: runs on cloud virtual machines, managed by Ray."""
import ast
import json
import os
import re
import shlex
import subprocess
import tempfile
import textwrap
from typing import Any, Callable, Dict, List, Optional, Tuple
import yaml

import colorama

from sky import backends
from sky import clouds
from sky import cloud_stores
from sky import logging
from sky import resources
from sky import task as task_mod
from sky.backends import backend_utils

App = backend_utils.App
Resources = resources.Resources
Path = str
PostSetupFn = Callable[[str], Any]
SKY_REMOTE_WORKDIR = backend_utils.SKY_REMOTE_WORKDIR

logger = logging.init_logger(__name__)


def _run(cmd, **kwargs):
    return subprocess.run(cmd, shell=True, check=True, **kwargs)


def _get_cluster_config_template(task):
    _CLOUD_TO_TEMPLATE = {
        clouds.AWS: 'config/aws-ray.yml.j2',
        # clouds.Azure: 'config/azure-ray.yml.j2',
        clouds.GCP: 'config/gcp-ray.yml.j2',
    }
    cloud = task.best_resources.cloud
    return _CLOUD_TO_TEMPLATE[type(cloud)]


def _to_accelerator_and_count(resources: Optional[Resources]
                             ) -> (Optional[str], int):
    acc = None
    acc_count = 0
    if resources is not None:
        d = resources.get_accelerators()
        if d is not None:
            assert len(d) == 1, d
            acc, acc_count = list(d.items())[0]
    return acc, acc_count


class RetryingVmProvisioner(object):
    """A provisioner that retries different regions/zones within a cloud."""

    def __init__(self):
        self._blocked_regions = set()
        self._blocked_zones = set()
        colorama.init()

    def _in_blocklist(self, cloud, region, zones):
        if region.name in self._blocked_regions:
            return True
        assert zones, (cloud, region, zones)
        for zone in zones:
            if zone.name not in self._blocked_zones:
                return False
        return True

    def _clear_blocklist(self):
        self._blocked_regions.clear()
        self._blocked_zones.clear()

    def _update_blocklist_on_gcp_error(self, region, zones, stdout, stderr):
        Style = colorama.Style
        assert len(zones) == 1, zones
        zone = zones[0]
        stderr = stderr.decode()
        splits = stderr.split('\n')
        exception_str = [s for s in splits if s.startswith('Exception: ')]
        if len(exception_str) == 1:
            # Parse structured response {'errors': [...]}.
            exception_str = exception_str[0][len('Exception: '):]
            exception_dict = ast.literal_eval(exception_str)
            for error in exception_dict['errors']:
                code = error['code']
                message = error['message']
                logger.warn(f'Got {code} in {zone.name} '
                            f'{Style.DIM}(message: {message})'
                            f'{Style.RESET_ALL}')
                if code == 'QUOTA_EXCEEDED':  # Per region.
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
                logger.warn(f'Got \'resource not found\' in {zone.name}.')
                self._blocked_zones.add(zone.name)
            else:
                logger.info('====== stdout ======')
                for s in stdout.decode().split('\n'):
                    print(s)
                logger.info('====== stderr ======')
                for s in splits:
                    print(s)
                assert False, \
                    'Errors occurred during setup command; check logs above.'
        return True, None

    def _update_blocklist_on_aws_error(self, region, zones, stdout, stderr):
        # The underlying ray autoscaler / boto3 will try all zones of a region
        # at once.
        Style = colorama.Style
        stdout_splits = stdout.decode().split('\n')
        stderr_splits = stderr.decode().split('\n')
        errors = [
            s.strip()
            for s in stdout_splits + stderr_splits
            if 'An error occurred' in s.strip()
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

        logger.warn(f'Got error(s) in all zones of {region.name}:')
        messages = '\n\t'.join(errors)
        logger.warn(f'{Style.DIM}\t{messages}{Style.RESET_ALL}')
        self._blocked_regions.add(region.name)
        return True, None

    def _update_blocklist_on_error(self, cloud, region, zones, stdout,
                                   stderr) -> Tuple[bool, Optional[str]]:
        """Cloud-specific error message handling.

        This parses textual stdout/stderr because we don't directly use the
        underlying clouds' SDKs.  If we did that, we could catch proper
        exceptions instead.

        Returns (should_continue_trying, reason).
        """
        if isinstance(cloud, clouds.GCP):
            return self._update_blocklist_on_gcp_error(region, zones, stdout,
                                                       stderr)

        if isinstance(cloud, clouds.AWS):
            return self._update_blocklist_on_aws_error(region, zones, stdout,
                                                       stderr)

        if isinstance(cloud, clouds.Azure):
            assert False, (stdout, stderr)  # TODO
        else:
            assert False, f'Unknown cloud: {cloud}.'

        return True, None

    def _yield_region_zones(self, task: App, cloud: clouds.Cloud):
        # Try reading previously launched region/zones and try them first,
        # because we may have an existing cluster there.
        region = None
        zones = None
        try:
            path = _get_cluster_config_template(task)[:-len('.j2')]
            with open(path, 'r') as f:
                config = yaml.safe_load(f)
            if type(cloud) in (clouds.AWS, clouds.GCP):
                region = config['provider']['region']
                zones = config['provider']['availability_zone']
            elif type(cloud) is clouds.Azure:
                region = config['provider']['location']
                zones = None
            else:
                assert False, cloud
        except Exception:
            pass
        if region is not None:
            region = clouds.Region(name=region)
            if zones is not None:
                zones = [clouds.Zone(name=zone) for zone in zones.split(',')]
                region.set_zones(zones)
            yield (region, zones)  # Ok to yield again in the next loop.
        for region, zones in cloud.region_zones_provision_loop():
            yield (region, zones)

    def provision_with_retries(self, task: App, to_provision: Resources,
                               dryrun: bool):
        """The provision retry loop."""
        self._clear_blocklist()
        Style = colorama.Style
        for region, zones in self._yield_region_zones(task, to_provision.cloud):
            if self._in_blocklist(to_provision.cloud, region, zones):
                continue
            logger.info(
                f'\n{Style.BRIGHT}Launching on {to_provision.cloud} {region.name} '
                f'({",".join(z.name for z in zones)}).{Style.RESET_ALL}')
            logger.info('If this takes longer than ~30 seconds,'
                        ' provisioning is likely successful.'
                        ' Setting up may take a few minutes.')
            config_dict = backend_utils.write_cluster_config(
                None,
                task,
                _get_cluster_config_template(task),
                region=region,
                zones=zones)
            if dryrun:
                return
            tpu_name = to_provision.accelerator_args.get('tpu_name')
            if tpu_name is not None:
                assert 'gcloud' in config_dict, \
                    'Expect TPU provisioning with gcloud'
                try:
                    _run(f'bash {config_dict["gcloud"][0]}',
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
                except subprocess.CalledProcessError as e:
                    stderr = e.stderr.decode('ascii')
                    if 'ALREADY_EXISTS' in stderr:
                        logger.info(
                            f'TPU {tpu_name} already exists; skipped creation.')
                    elif 'PERMISSION_DENIED' in stderr:
                        logger.info(
                            f'TPU resource is not available in this zone.')
                        continue
                    else:
                        logger.error(stderr)
                        raise e
            cluster_config_file = config_dict['ray']
            # Captures stdout/err.  Otherwise, too many repeated messages.
            proc = subprocess.Popen(['ray', 'up', '-y', cluster_config_file],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                should_continue, reason = self._update_blocklist_on_error(
                    to_provision.cloud, region, zones, stdout, stderr)
                if tpu_name is not None:
                    logger.info('Failed to provision VM. Tearing down TPU resource...')
                    _run(f'bash {config_dict["gcloud"][1]}',
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
                if not should_continue:
                    logger.warn(f'Skipping the rest of the regions/zones; '
                                f'reason: {reason}')
                    break
            else:
                if tpu_name is not None:
                    _run(
                        f"ray exec {cluster_config_file} \'echo \"export TPU_NAME={tpu_name}\" >> ~/.bashrc\'"
                    )
                logger.info(
                    f'{Style.BRIGHT}Successfully provisioned or found'
                    f' existing VM(s). Setup completed.{Style.RESET_ALL}')
                logger.info(
                    f'\nTo log into the head VM:\t{Style.BRIGHT}ray attach'
                    f' {cluster_config_file}{Style.RESET_ALL}\n')
                return config_dict
        message = ('Failed to acquire resources in all regions/zones.'
                   '  Try other resource requirements or different clouds.')
        logger.error(message)
        assert False, message


class CloudVmRayBackend(backends.Backend):
    """Backend: runs on cloud virtual machines, managed by Ray.

    Changing this class may also require updates to:
      * Cloud providers' templates under config/
      * Cloud providers' implementations under clouds/
    """

    ResourceHandle = str  # yaml file

    def __init__(self):
        # TODO: should include this as part of the handle.
        self._managed_tpu = None

    def provision(self, task: App, to_provision: Resources,
                  dryrun: bool) -> ResourceHandle:
        """Provisions using 'ray up'."""
        # ray up: the VMs.
        provisioner = RetryingVmProvisioner()
        config_dict = provisioner.provision_with_retries(
            task, to_provision, dryrun)
        cluster_config_file = config_dict['ray']
        if dryrun:
            return
        # gcloud: TPU.
        if config_dict.get('gcloud') is not None:
            self._managed_tpu = config_dict['gcloud']
        backend_utils.wait_until_ray_cluster_ready(cluster_config_file,
                                                   task.num_nodes)
        return cluster_config_file

    def sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        # TODO: do we really need this if provision() takes care of it?
        # TODO: this only syncs to head.  -A flag from ray rsync_up is being
        # deprecated.
        _run(f'ray rsync_up {handle} {workdir}/ {SKY_REMOTE_WORKDIR}')

    def sync_file_mounts(
            self,
            handle: ResourceHandle,
            all_file_mounts: Dict[Path, Path],
            cloud_to_remote_file_mounts: Optional[Dict[Path, Path]],
    ) -> None:
        # TODO: this only syncs to head.
        # 'all_file_mounts' should already have been handled in provision()
        # using the yaml file.  Here we handle cloud -> remote file transfers.
        mounts = cloud_to_remote_file_mounts
        if mounts is not None:
            for dst, src in mounts.items():
                storage = cloud_stores.get_storage_from_path(src)
                # TODO: room for improvement.  Here there are many moving parts
                # (download gsutil on remote, run gsutil on remote).  Consider
                # alternatives (smart_open, each provider's own sdk), a
                # data-transfer container etc.  We also assumed 'src' is a
                # directory.
                download_command = storage.make_download_dir_command(
                    source=src, destination=dst)
                _run(f'ray exec {handle} \'{download_command}\'')

    def run_post_setup(self, handle: ResourceHandle, post_setup_fn: PostSetupFn,
                       task: App) -> None:
        ip_list = self._get_node_ips(handle, task.num_nodes)
        ip_to_command = post_setup_fn(ip_list)
        for ip, cmd in ip_to_command.items():
            cmd = (f'mkdir -p {SKY_REMOTE_WORKDIR} && '
                   f'cd {SKY_REMOTE_WORKDIR} && {cmd}')
            backend_utils.run_command_on_ip_via_ssh(ip, cmd, task.private_key,
                                                    task.container_name)

    def _execute_par_task(self, handle: ResourceHandle,
                          par_task: task_mod.ParTask,
                          stream_logs: bool) -> None:
        # Case: ParTask(tasks), t.num_nodes == 1 for t in tasks
        for t in par_task.tasks:
            assert t.num_nodes == 1, \
                f'ParTask does not support inner Tasks with num_nodes > 1: {t}'
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
        codegen = [
            textwrap.dedent(f"""\
        import ray
        import subprocess
        ray.init('auto', namespace='__sky__', log_to_driver={stream_logs})
        print('cluster_resources:', ray.cluster_resources())
        print('available_resources:', ray.available_resources())
        print('live nodes:', ray.state.node_ids())
        futures = []
        """)
        ]
        for i, t in enumerate(par_task.tasks):
            cmd = shlex.quote(f'cd {SKY_REMOTE_WORKDIR} && {t.run}')
            # We can't access t.best_resources because the inner task doesn't
            # undergo optimization.
            resources = par_task.get_task_resource_demands(i)
            if resources is not None:
                resources_str = f', resources={json.dumps(resources)}'
                assert len(resources) == 1, \
                    ('There can only be one type of accelerator per instance.'
                    f' Found: {resources}.')
                # Passing this ensures that the Ray remote task gets
                # CUDA_VISIBLE_DEVICES set correctly.  If not passed, that flag
                # would be force-set to empty by Ray.
                num_gpus_str = f', num_gpus={list(resources.values())[0]}'
            else:
                resources_str = ''
                num_gpus_str = ''
            name = f'task-{i}' if t.name is None else t.name
            task_i_codegen = textwrap.dedent(f"""\
        futures.append(ray.remote(lambda: subprocess.run(
            {cmd},
              shell=True, check=True)) \\
              .options(name='{name}'{resources_str}{num_gpus_str}) \\
              .remote())
        """)
            codegen.append(task_i_codegen)
        # Block.
        codegen.append('ray.get(futures)\n')
        codegen = '\n'.join(codegen)

        # Logger.
        colorama.init()
        Fore = colorama.Fore
        Style = colorama.Style
        logger.info(
            f'\n{Fore.CYAN}Starting ParTask execution.{Style.RESET_ALL}')
        if not stream_logs:
            logger.info(
                f'{Fore.CYAN}Logs will not be streamed (stream_logs=False).'
                f'{Style.RESET_ALL} Hint: in the run command, redirect each'
                ' task\'s output to a file, and use `tail -f` to monitor.\n')

        self._exec_code_on_head(handle, codegen)

    def _exec_code_on_head(self, handle: ResourceHandle, codegen: str) -> None:
        """Executes generated code on the head node."""
        with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
            fp.write(codegen)
            fp.flush()
            basename = os.path.basename(fp.name)
            # Rather than 'rsync_up' & 'exec', the alternative of 'ray submit'
            # may not work as the remote VM may use system python (python2) to
            # execute the script.  Happens for AWS.
            _run(f'ray rsync_up {handle} {fp.name} /tmp/{basename}')
        # Note the use of python3 (for AWS AMI).
        _run(f'ray exec {handle} \'python3 /tmp/{basename}\'')

    def execute(self, handle: ResourceHandle, task: App,
                stream_logs: bool) -> None:
        # Execution logic differs for three types of tasks.

        # Case: ParTask(tasks), t.num_nodes == 1 for t in tasks
        if isinstance(task, task_mod.ParTask):
            return self._execute_par_task(handle, task, stream_logs)

        # Otherwise, handle a basic Task.
        if task.run is None:
            logger.info(f'Nothing to run; run command not specified:\n{task}')
            return

        # Case: Task(run, num_nodes=1)
        if task.num_nodes == 1:
            return self._execute_task_one_node(handle, task, stream_logs)

        # Case: Task(run, num_nodes=N)
        assert task.num_nodes > 1, task.num_nodes
        return self._execute_task_n_nodes(handle, task, stream_logs)

    def _execute_task_one_node(self, handle: ResourceHandle, task: App,
                               stream_logs: bool) -> None:
        # Launch the command as a Ray task.
        assert type(task.run) is str, \
            f'Task(run=...) should be a string (found {type(task.run)}).'
        cmd = 'ray exec {} {}'.format(
            handle, shlex.quote(f'cd {SKY_REMOTE_WORKDIR} && {task.run}'))
        if not stream_logs:
            out = tempfile.NamedTemporaryFile('w', prefix='sky_',
                                              suffix='.out').name
            cmd += f' >{out}'
            colorama.init()
            Style = colorama.Style
            logger.info(f'Redirecting stdout, to monitor: '
                        f'{Style.BRIGHT}tail -f {out}{Style.RESET_ALL}')
        _run(cmd)

    def _execute_task_n_nodes(self, handle: ResourceHandle, task: App,
                              stream_logs: bool) -> None:
        # Strategy:
        #   ray.init(..., log_to_driver=False); otherwise too many logs.
        #   for node:
        #     submit _run_cmd(cmd) with resource {node_i: 1}
        codegen = [
            textwrap.dedent(f"""\
        import subprocess
        import ray
        ray.init('auto', namespace='__sky__', log_to_driver={stream_logs})
        print('cluster_resources:', ray.cluster_resources())
        print('available_resources:', ray.available_resources())
        print('live nodes:', ray.state.node_ids())
        futures = []
        """)
        ]
        acc, acc_count = _to_accelerator_and_count(task.best_resources)
        # Get private ips here as Ray internally uses 'node:private_ip' as
        # per-node custom resources.
        ips = self._get_node_ips(handle,
                                 task.num_nodes,
                                 return_private_ips=True)
        ips_dict = task.run(ips)
        for ip in ips_dict:
            command_for_ip = ips_dict[ip]
            # By default /bin/sh is used, and if 'source ~/.bashrc' or 'source
            # activate conda_env' is used, we get /bin/sh: 1: source: not
            # found.  Use /bin/bash -c as a workaround.
            cmd = shlex.quote(
                f'/bin/bash -c \'cd {SKY_REMOTE_WORKDIR} && {command_for_ip}\'')
            # Ray's per-node resources, to constrain scheduling each command to
            # the corresponding node, represented by private IPs.
            demand = {f'node:{ip}': 1}
            resources_str = f', resources={json.dumps(demand)}'
            num_gpus_str = ''
            if acc_count > 0:
                # Passing this ensures that the Ray remote task gets
                # CUDA_VISIBLE_DEVICES set correctly.  If not passed, that flag
                # would be force-set to empty by Ray.
                num_gpus_str = f', num_gpus={acc_count}'
            codegen.append(
                textwrap.dedent(f"""\
        futures.append(ray.remote(lambda: subprocess.run(
            {cmd},
                shell=True, check=True)) \\
                .options(name='task-{ip}'{resources_str}{num_gpus_str}) \\
                .remote())
        """))
        # Block.
        codegen.append('ray.get(futures)\n')
        codegen = '\n'.join(codegen)
        # Logger.
        colorama.init()
        Fore = colorama.Fore
        Style = colorama.Style
        logger.info(f'\n{Fore.CYAN}Starting Task execution.{Style.RESET_ALL}')
        if not stream_logs:
            logger.info(
                f'{Fore.CYAN}Logs will not be streamed (stream_logs=False).'
                f'{Style.RESET_ALL} Hint: in the run command, redirect each'
                ' task\'s output to a file, and use `tail -f` to monitor.\n')
        self._exec_code_on_head(handle, codegen)

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        colorama.init()
        Style = colorama.Style
        if not teardown:
            logger.info(
                f'\nTo log into the head VM:\t{Style.BRIGHT}ray attach {handle} {Style.RESET_ALL}\n'
                f'\nTo down the resources:\t{Style.BRIGHT}ray down {handle} -y {Style.RESET_ALL}\n'
            )
            if self._managed_tpu is not None:
                logger.info(
                    f'To down the TPU resources:\t{Style.BRIGHT}bash {self._managed_tpu[1]} {Style.RESET_ALL}\n'
                )

    def teardown(self, handle: ResourceHandle) -> None:
        _run(f'ray down -y {handle}', shell=True, check=True)
        if self._managed_tpu is not None:
            _run(f'bash {self._managed_tpu[1]}')

    def _get_node_ips(self,
                      handle: ResourceHandle,
                      expected_num_nodes: int,
                      return_private_ips: bool = False) -> List[str]:
        """Returns the IPs of all nodes in the cluster."""
        yaml_handle = handle
        if return_private_ips:
            with open(handle, 'r') as f:
                config = yaml.safe_load(f)
            # Add this field to a temp file to get private ips.
            config['provider']['use_internal_ips'] = True
            yaml_handle = handle + '.tmp'
            backend_utils.yaml_dump(yaml_handle, config)

        out = _run(f'ray get-head-ip {yaml_handle}',
                   stdout=subprocess.PIPE).stdout.decode().strip()
        head_ip = re.findall(backend_utils.IP_ADDR_REGEX, out)
        assert 1 == len(head_ip), out

        out = _run(f'ray get-worker-ips {yaml_handle}',
                   stdout=subprocess.PIPE).stdout.decode()
        worker_ips = re.findall(backend_utils.IP_ADDR_REGEX, out)
        assert expected_num_nodes - 1 == len(worker_ips), (expected_num_nodes -
                                                           1, out)
        if return_private_ips:
            os.remove(yaml_handle)
        return head_ip + worker_ips
