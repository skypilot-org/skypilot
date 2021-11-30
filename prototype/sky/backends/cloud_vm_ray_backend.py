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
from googleapiclient.discovery import Resource
import yaml

import colorama
from ray.autoscaler import sdk

import sky
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
SKY_LOGS_DIRECTORY = backend_utils.SKY_LOGS_DIRECTORY

logger = logging.init_logger(__name__)

_TASK_LAUNCH_CODE_GENERATOR = """\
        import io
        import os
        import ray
        import selectors
        import subprocess
        ray.init('auto', namespace='__sky__', log_to_driver={stream_logs})
        print('cluster_resources:', ray.cluster_resources())
        print('available_resources:', ray.available_resources())
        print('live nodes:', ray.state.node_ids())
        
        def redirect_process_output(proc, log_path, stream_logs, start_streaming_at=''):
            dirname = os.path.dirname(log_path)
            os.makedirs(dirname, exist_ok=True)

            out_io = io.TextIOWrapper(proc.stdout, encoding='utf-8', newline='')
            err_io = io.TextIOWrapper(proc.stderr, encoding='utf-8', newline='')
            sel = selectors.DefaultSelector()
            sel.register(out_io, selectors.EVENT_READ)
            sel.register(err_io, selectors.EVENT_READ)

            stdout = ''
            stderr = ''

            start_streaming_flag = False
            with open(log_path, 'a') as fout:
                while len(sel.get_map()) > 0:
                    for key, _ in sel.select():
                        line = key.fileobj.readline()
                        if not line:
                            sel.unregister(key.fileobj)
                            break
                        if start_streaming_at in line:
                            start_streaming_flag = True
                        if key.fileobj is out_io:
                            stdout += line
                            fout.write(line)
                            fout.flush()
                        else:
                            stderr += line
                            fout.write(line)
                            fout.flush()
                        if stream_logs and start_streaming_flag:
                            print(line, end='')
            return stdout, stderr
        
        futures = []

        def start_task(cmd, log_path, stream_logs):
            proc = subprocess.Popen(cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    shell=True,
                                    executable='/bin/bash')
            redirect_process_output(proc, log_path, stream_logs)
"""


def _run(cmd, **kwargs):
    return subprocess.run(cmd, shell=True, check=True, **kwargs)


def _run_no_outputs(cmd, **kwargs):
    return _run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def _run_with_log(cmd,
                  log_path,
                  stream_logs=False,
                  start_streaming_at='',
                  **kwargs):
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            **kwargs)
    stdout, stderr = backend_utils.redirect_process_output(
        proc, log_path, stream_logs, start_streaming_at=start_streaming_at)
    proc.wait()
    return proc, stdout, stderr


def _get_cluster_config_template(task):
    _CLOUD_TO_TEMPLATE = {
        clouds.AWS: 'config/aws-ray.yml.j2',
        clouds.Azure: 'config/azure-ray.yml.j2',
        clouds.GCP: 'config/gcp-ray.yml.j2',
    }
    cloud = task.best_resources.cloud
    path = _CLOUD_TO_TEMPLATE[type(cloud)]
    return os.path.join(os.path.dirname(sky.__root_dir__), path)


def _to_accelerator_and_count(resources: Optional[Resources]
                             ) -> Tuple[Optional[str], int]:
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

    def __init__(self, log_dir):
        self._blocked_regions = set()
        self._blocked_zones = set()
        self.log_dir = log_dir
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
                for s in stdout.split('\n'):
                    print(s)
                logger.info('====== stderr ======')
                for s in splits:
                    print(s)
                assert False, \
                    'Errors occurred during setup command; check logs above.'

    def _update_blocklist_on_aws_error(self, region, zones, stdout, stderr):
        # The underlying ray autoscaler / boto3 will try all zones of a region
        # at once.
        Style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
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

    def _update_blocklist_on_azure_error(self, region, zones, stdout, stderr):
        # The underlying ray autoscaler will try all zones of a region at once.
        Style = colorama.Style
        stdout_splits = stdout.split('\n')
        stderr_splits = stderr.split('\n')
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

    def _update_blocklist_on_error(self, cloud, region, zones, stdout,
                                   stderr) -> None:
        """Handles cloud-specific errors and updates the block list.

        This parses textual stdout/stderr because we don't directly use the
        underlying clouds' SDKs.  If we did that, we could catch proper
        exceptions instead.
        """
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
                zones = str(config['provider']['zone'])
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
                               dryrun: bool, stream_logs: bool):
        """The provision retry loop."""
        # Get log_path name
        log_path = os.path.join(self.log_dir, 'provision.log')
        log_abs_path = os.path.abspath(log_path)

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
                        ' Setup may take a few minutes.')
            config_dict = backend_utils.write_cluster_config(
                None,
                task,
                _get_cluster_config_template(task),
                region=region,
                zones=zones,
                dryrun=dryrun)
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

            tail_cmd = f'tail -n100 -f {log_path}'
            logger.info(
                f'To view progress: {Style.BRIGHT}{tail_cmd}{Style.RESET_ALL}')
            # Redirect stdout/err to the file and streaming (if stream_logs).
            proc, stdout, stderr = _run_with_log(
                ['ray', 'up', '-y', cluster_config_file],
                log_abs_path,
                stream_logs,
                start_streaming_at='Shared connection to')

            if proc.returncode != 0:
                self._update_blocklist_on_error(to_provision.cloud, region,
                                                zones, stdout, stderr)
                if tpu_name is not None:
                    logger.info(
                        'Failed to provision VM. Tearing down TPU resource...')
                    _run(f'bash {config_dict["gcloud"][1]}',
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
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
        message = ('Failed to acquire resources in all regions/zones'
                   f' (requested {to_provision}).'
                   ' Try changing resource requirements or use another cloud.')
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
        run_id = backend_utils.get_run_id()
        self.log_dir = os.path.join(SKY_LOGS_DIRECTORY, run_id)
        os.makedirs(self.log_dir, exist_ok=True)

    def provision(self, task: App, to_provision: Resources, dryrun: bool,
                  stream_logs: bool) -> ResourceHandle:
        """Provisions using 'ray up'."""
        # ray up: the VMs.
        provisioner = RetryingVmProvisioner(self.log_dir)
        config_dict = provisioner.provision_with_retries(
            task, to_provision, dryrun, stream_logs)
        if dryrun:
            return
        cluster_config_file = config_dict['ray']
        # gcloud: TPU.
        self._managed_tpu = config_dict.get('gcloud')

        backend_utils.wait_until_ray_cluster_ready(to_provision.cloud,
                                                   cluster_config_file,
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
        log_dir = os.path.join(f'{SKY_REMOTE_WORKDIR}', f'{self.log_dir}',
                               'tasks')
        codegen = [
            textwrap.dedent(
                _TASK_LAUNCH_CODE_GENERATOR.format(stream_logs=stream_logs))
        ]
        for i, t in enumerate(par_task.tasks):
            # '. $(conda info --base)/etc/profile.d/conda.sh || true' is used to initialize
            # conda, so that 'conda activate ...' works.
            cmd = shlex.quote(
                f'. $(conda info --base)/etc/profile.d/conda.sh || true && \
                    cd {SKY_REMOTE_WORKDIR} && source ~/.bashrc && {t.run}')
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
            log_path = os.path.join(f'{log_dir}', f'{name}.log')

            task_i_codegen = textwrap.dedent(f"""\
        futures.append(ray.remote(start_task) \\
              .options(name='{name}'{resources_str}{num_gpus_str}) \\
              .remote({cmd}, '{log_path}', {stream_logs}))
        """)
            codegen.append(task_i_codegen)
        # Block.
        codegen.append('ray.get(futures)\n')
        codegen = '\n'.join(codegen)

        # Logger.
        colorama.init()
        Fore = colorama.Fore
        Style = colorama.Style
        logger.info(f'{Fore.CYAN}Starting ParTask execution.{Style.RESET_ALL}')
        if not stream_logs:
            logger.info(
                f'{Fore.CYAN}Logs will not be streamed (stream_logs=False).'
                f'{Style.RESET_ALL} Hint: task outputs are redirected to '
                f'{Style.BRIGHT}{log_dir}{Style.RESET_ALL} on the cluster. To monitor: '
                f'ray exec {handle} "tail -f {log_dir}/*.log" '
                f'(To view the task names: ray exec {handle} "ls {log_dir}/")')

        self._exec_code_on_head(handle, codegen)
        # Get external IPs for the nodes
        external_ips = self._get_node_ips(handle,
                                          expected_num_nodes=1,
                                          return_private_ips=False)
        self._rsync_down_logs(handle, log_dir, external_ips)

    def _rsync_down_logs(self,
                         handle: ResourceHandle,
                         log_dir: str,
                         ips: List[str] = None):
        local_log_dir = os.path.join(f'{self.log_dir}', 'tasks')
        Style = colorama.Style
        logger.info(
            f'Syncing down the logs to {Style.BRIGHT}{local_log_dir}{Style.RESET_ALL}'
        )
        os.makedirs(local_log_dir, exist_ok=True)
        # Call the ray sdk to rsync the logs back to local.
        for ip in ips:
            sdk.rsync(
                handle,
                source=f'{log_dir}/*',
                target=f'{local_log_dir}',
                down=True,
                ip_address=ip,
                use_internal_ip=False,
                should_bootstrap=False,
            )

    def _exec_code_on_head(self,
                           handle: ResourceHandle,
                           codegen: str,
                           stream_logs: bool = True,
                           executable: Optional[str] = None) -> None:
        """Executes generated code on the head node."""
        with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
            fp.write(codegen)
            fp.flush()
            basename = os.path.basename(fp.name)
            # Rather than 'rsync_up' & 'exec', the alternative of 'ray submit'
            # may not work as the remote VM may use system python (python2) to
            # execute the script.  Happens for AWS.
            _run_no_outputs(f'ray rsync_up {handle} {fp.name} /tmp/{basename}')
        if executable is None:
            executable = 'python3'
        cd = f'cd {SKY_REMOTE_WORKDIR}'
        cmd = f'ray exec {handle} \'{cd} && {executable} /tmp/{basename}\''
        log_path = os.path.join(self.log_dir, f'run.log')
        if not stream_logs:
            colorama.init()
            Style = colorama.Style
            logger.info(f'Redirecting stdout/stderr, to monitor: '
                        f'{Style.BRIGHT}tail -f {log_path}{Style.RESET_ALL}')

        _run_with_log(cmd, log_path, stream_logs, shell=True)

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
        codegen = textwrap.dedent(f"""\
            #!/bin/bash
            . $(conda info --base)/etc/profile.d/conda.sh || true
            {task.run}
        """)
        self._exec_code_on_head(handle, codegen, stream_logs, executable='bash')

    def _execute_task_n_nodes(self, handle: ResourceHandle, task: App,
                              stream_logs: bool) -> None:
        # Strategy:
        #   ray.init(..., log_to_driver=False); otherwise too many logs.
        #   for node:
        #     submit _run_cmd(cmd) with resource {node_i: 1}
        log_dir = os.path.join(f'{SKY_REMOTE_WORKDIR}', f'{self.log_dir}',
                               'tasks')
        codegen = [
            textwrap.dedent(
                _TASK_LAUNCH_CODE_GENERATOR.format(stream_logs=stream_logs))
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
            # '. $(conda info --base)/etc/profile.d/conda.sh || true' is used
            # to initialize conda, so that 'conda activate ...' works.
            cmd = shlex.quote(
                f'. $(conda info --base)/etc/profile.d/conda.sh || true && \
                    cd {SKY_REMOTE_WORKDIR} && {command_for_ip}')
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
            # Set the executable to /bin/bash, so that the 'source ~/.bashrc'
            # and 'source activate conda_env' can be used.
            name = f'task-{ip}'
            log_path = os.path.join(f'{log_dir}', f'{name}.log')

            codegen.append(
                textwrap.dedent(f"""\
        futures.append(ray.remote(start_task) \\
              .options(name='{name}'{resources_str}{num_gpus_str}) \\
              .remote({cmd}, '{log_path}', {stream_logs}))
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
                f'{Style.RESET_ALL} Hint: task outputs are redirected to'
                f'{Style.BRIGHT}{log_dir}{Style.RESET_ALL} on the cluster. To monitor: '
                f'ray exec {handle} "tail -f {log_dir}/*.log"\n'
                f'(To view the task names: ray exec {handle} "ls {log_dir}/")')

        self._exec_code_on_head(handle, codegen)

        # Get external IPs for the nodes
        external_ips = self._get_node_ips(handle,
                                          task.num_nodes,
                                          return_private_ips=False)
        self._rsync_down_logs(handle, log_dir, external_ips)

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        colorama.init()
        Style = colorama.Style
        if not teardown:
            logger.info(
                f'\nTo log into the head VM:\t{Style.BRIGHT}ray attach {handle} {Style.RESET_ALL}\n'
                f'\nTo tear down the cluster:\t{Style.BRIGHT}ray down {handle} -y {Style.RESET_ALL}\n'
            )
            if self._managed_tpu is not None:
                logger.info(
                    f'To tear down the TPU(s):\t{Style.BRIGHT}bash {self._managed_tpu[1]} {Style.RESET_ALL}\n'
                )

    def teardown(self, handle: ResourceHandle) -> None:
        _run(f'ray down -y {handle}')
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
