"""Execution layer: resource provisioner + task launcher.

Usage:

   >> planned_dag = sky.optimize(dag)
   >> sky.execute(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import functools
import json
import os
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Union, Optional

import colorama
from colorama import Fore, Style

import sky
from sky import backends
from sky import cloud_stores
from sky import logging
from sky.backends import backend_utils

logger = logging.init_logger(__name__)

IPAddr = str
ShellCommand = str
ShellCommandGenerator = Callable[[List[IPAddr]], Dict[IPAddr, ShellCommand]]
ShellCommandOrGenerator = Union[ShellCommand, ShellCommandGenerator]

SKY_LOGS_DIRECTORY = './logs'
STREAM_LOGS_TO_CONSOLE = True

App = backend_utils.App
RunId = backend_utils.RunId

ResourceHandle = str

SKY_REMOTE_WORKDIR = backend_utils.SKY_REMOTE_WORKDIR


def _get_cluster_config_template(task):
    _CLOUD_TO_TEMPLATE = {
        sky.clouds.AWS: 'config/aws.yml.j2',
        sky.clouds.Azure: 'config/azure.yml.j2',
        sky.clouds.GCP: 'config/gcp.yml.j2',
    }
    cloud = task.best_resources.cloud
    if task.num_nodes > 1 and str(cloud) == 'AWS':
        return 'config/aws-distributed.yml.j2'
    return _CLOUD_TO_TEMPLATE[type(cloud)]


class EventLogger:

    def __init__(self, log_file_path: str):
        self.logfile = log_file_path
        # Create an empty file.
        with open(self.logfile, 'w'):
            pass

    def log(self, event: str, payload: dict = {}):
        now = time.time()
        json_payload = {'time': now, 'event': event}
        json_payload.update(payload)
        with open(self.logfile, 'a') as fout:
            fout.write(json.dumps(json_payload))
            fout.write('\n')


class Step:

    def __init__(self,
                 runner: 'Runner',
                 step_id: str,
                 step_desc: str,
                 shell_command: ShellCommandOrGenerator,
                 callback: Callable[[str], Any] = None):
        self.runner = runner
        self.step_id = str(step_id)
        self.step_desc = step_desc
        self.shell_command = shell_command
        self.callback = callback

    def run(self, **kwargs) -> subprocess.CompletedProcess:
        log_path = os.path.join(self.runner.logs_root, f'{self.step_id}.log')
        log_abs_path = os.path.abspath(log_path)
        tail_cmd = f'tail -n100 -f {log_abs_path}'
        # @Frank Fix this
        if STREAM_LOGS_TO_CONSOLE:
            # TODO: `ray up` has a bug where if you redirect stdout and stderr, stdout is not flushed.
            lines = []
            with open(log_path, 'w') as fout:
                proc = subprocess.Popen(
                    self.shell_command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    # text=True,
                )
                for line in proc.stdout:
                    line = line.decode("utf-8")
                    logger.debug(line.rstrip() + '\r')
                    fout.write(line)
                    lines.append(line)
                proc.communicate()
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(
                        proc.returncode,
                        proc.args,
                    )
                if self.callback:
                    self.callback(''.join(lines))
                return proc
        else:
            logger.info(
                f'To view progress: {Style.BRIGHT}{tail_cmd}{Style.RESET_ALL}')
            proc = subprocess.run(
                self.shell_command + f' 2>&1 >{log_path}',
                shell=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # TODO: implement callback
            return proc


class Runner:

    def __init__(self, run_id: RunId, task, steps: List[Step] = []):
        self.run_id = run_id
        self.steps = steps
        self.next_step_id = 0
        self.logs_root = os.path.join(SKY_LOGS_DIRECTORY, run_id)
        os.makedirs(self.logs_root, exist_ok=True)
        self.logger = EventLogger(os.path.join(self.logs_root, '_events.jsonl'))
        self.cluster_ips = []
        self.task = task

    def add_step(self,
                 step_name: str,
                 step_desc: str,
                 shell_command: ShellCommandOrGenerator,
                 callback: Callable[[str], Any] = None) -> 'Runner':
        step_id = f'{self.next_step_id:03}_{step_name}'
        self.next_step_id += 1
        self.steps.append(
            Step(self, step_id, step_desc, shell_command, callback))
        return self

    def run(self) -> 'Runner':
        self.logger.log('start_run')
        logger.info(f'{Fore.GREEN}')
        logger.info('--------------------------')
        logger.info('  Sky execution started')
        logger.info(f'--------------------------{Fore.RESET}')
        logger.info('')

        try:
            for step in self.steps:
                self.logger.log(
                    'start_step',
                    {
                        'step_id': step.step_id,
                        'step_desc': step.step_desc,
                        'shell_command': str(step.shell_command),
                    },
                )
                if isinstance(step.shell_command, ShellCommand):
                    logger.info(
                        f'{Fore.CYAN}Step {step.step_id} started: {step.step_desc}{Fore.RESET}\n{Style.DIM}{step.shell_command}{Style.RESET_ALL}'
                    )
                    step.run()
                else:
                    assert len(self.cluster_ips) >= 1, self.cluster_ips
                    commands = step.shell_command(self.cluster_ips)
                    logger.info(
                        f'{Fore.CYAN}Step {step.step_id} started: {step.step_desc}{Fore.RESET}\n{Style.DIM}{commands}{Style.RESET_ALL}'
                    )
                    for ip, cmd in commands.items():
                        cmd = f'cd {SKY_REMOTE_WORKDIR} && ' + cmd
                        backend_utils.run_command_on_ip_via_ssh(
                            ip, cmd, self.task.private_key,
                            self.task.container_name)

                self.logger.log('finish_step')
                logger.info(
                    f'{Fore.CYAN}Step {step.step_id} finished{Fore.RESET}\n')

            self.logger.log('finish_run')
            logger.info(f'{Fore.GREEN}')
            logger.info('---------------------------')
            logger.info('  Sky execution finished')
            logger.info(f'---------------------------{Fore.RESET}')
            logger.info('')
            return self
        except subprocess.CalledProcessError as e:
            logger.error(f'{Fore.RED}Step failed! {e}{Fore.RESET}')
            raise e


def execute_v1(dag: sky.Dag, dryrun: bool = False, teardown: bool = False):
    colorama.init()

    assert len(dag) == 1, 'Job launcher assumes 1 task for now'
    task = dag.tasks[0]

    run_id = backend_utils.get_run_id()
    config_dict = backend_utils.write_cluster_config(
        run_id, task, _get_cluster_config_template(task), dryrun=dryrun)
    cluster_config_file = config_dict['ray']
    if dryrun:
        logger.info('Dry run finished.')
        return

    # FIXME: if a command fails, stop the rest.
    runner = Runner(run_id, task)
    runner.add_step('provision',
                    'Provision resources',
                    f'ray up -y {cluster_config_file} --no-config-cache',
                    callback=functools.partial(
                        backend_utils.wait_until_ray_cluster_ready,
                        cluster_config_file, task.num_nodes))
    if task.best_resources.accelerator_args is not None and \
        task.best_resources.accelerator_args.get('tpu_name') is not None:
        assert 'gcloud' in config_dict, 'Expect TPU provisioning with gcloud'
        runner.add_step('provision', 'Provision resources with gcloud',
                        f'bash {config_dict["gcloud"][0]}')
        runner.add_step(
            'setup', 'TPU setup',
            f"ray exec {cluster_config_file} \'echo \"export TPU_NAME={task.best_resources.accelerator_args['tpu_name']}\" >> ~/.bashrc\'"
        )

    if task.workdir is not None:
        runner.add_step(
            'sync', 'Sync files',
            f'ray rsync_up {cluster_config_file} {task.workdir}/ {SKY_REMOTE_WORKDIR}'
        )

    if task.get_cloud_to_remote_file_mounts() is not None:
        # Handle cloud -> remote file transfers.
        mounts = task.get_cloud_to_remote_file_mounts()
        for dst, src in mounts.items():
            storage = cloud_stores.get_storage_from_path(src)
            # TODO: room for improvement.  Here there are many moving parts
            # (download gsutil on remote, run gsutil on remote).  Consider
            # alternatives (smart_open, each provider's own sdk), a
            # data-transfer container etc.  We also assumed 'src' is a
            # directory.
            download_command = storage.make_download_dir_command(
                source=src, destination=dst)
            runner.add_step(
                'cloud_to_remote_download',
                'Download files from cloud to remote',
                f'ray exec {cluster_config_file} \'{download_command}\'')

    if task.num_nodes > 1:

        def collect_ips(stdout, expected_count=0):
            ips = re.findall(backend_utils.IP_ADDR_REGEX, stdout)
            if expected_count > 0:
                assert len(ips) == expected_count, (ips, expected_count)
            runner.cluster_ips.extend(ips)

        runner.add_step('get_head_ip',
                        'Get Head IP',
                        f'ray get-head-ip {cluster_config_file}',
                        callback=collect_ips)

        runner.add_step('get_worker_ips',
                        'Get Worker IP',
                        f'ray get-worker-ips {cluster_config_file}',
                        callback=collect_ips)

    if task.post_setup_fn is not None:
        runner.add_step(
            'post_setup',
            'Additional Setup after Base Setup (includes custom setup on individual node)',
            task.post_setup_fn)

    if isinstance(task.run, str):
        runner.add_step(
            'exec', 'Execute task',
            f'ray exec {cluster_config_file} \'cd {SKY_REMOTE_WORKDIR} && {task.run}\''
        )
    else:
        runner.add_step('exec', 'Execute task', task.run)

    if teardown:
        runner.add_step('teardown', 'Tear down resources',
                        f'ray down -y {cluster_config_file}')
        if task.best_resources.accelerator_args['tpu_name'] is not None:
            runner.add_step('teardown', 'Tear down resources with gcloud',
                            f'bash {config_dict["gcloud"][1]}')
    runner.run()
    if not teardown:
        logger.info(
            f'  To log into the cloud VM:\t{Style.BRIGHT}ray attach {cluster_config_file} {Style.RESET_ALL}\n'
        )
        logger.info(
            f'  To teardown the resources:\t{Style.BRIGHT}ray down {cluster_config_file} -y {Style.RESET_ALL}\n'
        )
        if task.best_resources.accelerator_args.get('tpu_name') is not None:
            logger.info(
                f'  To teardown the TPU resources:\t{Style.BRIGHT}bash {config_dict["gcloud"][1]} {Style.RESET_ALL}\n'
            )


def execute_v2(dag: sky.Dag,
               dryrun: bool = False,
               teardown: bool = False,
               stream_logs: bool = True,
               backend: Optional[backends.Backend] = None) -> None:
    """Executes a planned DAG.

    Args:
      dag: sky.Dag.
      dryrun: bool; if True, only print the provision info (e.g., cluster
        yaml).
      teardown: bool; whether to teardown the launched resources after
        execution.
      stream_logs: bool; whether to stream all tasks' outputs to the client.
        Hint: for a ParTask, set this to False to avoid a lot of log outputs;
        each task's output can be redirected to their own files.
      backend: Backend; backend to use for executing the tasks. Defaults to
        CloudVmRayBackend()
    """
    # TODO: Azure. Port some of execute_v1()'s nice logging messages.
    assert len(dag) == 1, 'Job launcher assumes 1 task for now.'
    task = dag.tasks[0]
    best_resources = task.best_resources
    assert best_resources is not None, \
        'Run sky.optimize() before sky.execute().'

    backend = backend if backend is not None else backends.CloudVmRayBackend()

    handle = backend.provision(task,
                               best_resources,
                               dryrun=dryrun,
                               stream_logs=stream_logs)
    if dryrun:
        logger.info('Dry run finished.')
        return

    if task.workdir is not None:
        backend.sync_workdir(handle, task.workdir)

    backend.sync_file_mounts(handle, task.file_mounts,
                             task.get_cloud_to_remote_file_mounts())

    if task.post_setup_fn is not None:
        backend.run_post_setup(handle, task.post_setup_fn, task)

    try:
        backend.execute(handle, task, stream_logs)
    finally:
        # Enables post_execute() to be run after KeyboardInterrupt.
        backend.post_execute(handle, teardown)

    if teardown:
        backend.teardown(handle)


def execute_v3(dag: sky.Dag,
               dryrun: bool = False,
               teardown: bool = False,
               stream_logs: bool = True,
               backend: Optional[backends.Backend] = None,
               minimize=None,
               provision_retry: bool = False) -> None:
    """Executes a planned DAG.

    Args:
      dag: sky.Dag.
      dryrun: bool; if True, only print the provision info (e.g., cluster
        yaml).
      teardown: bool; whether to teardown the launched resources after
        execution.
      stream_logs: bool; whether to stream all tasks' outputs to the client.
        Hint: for a ParTask, set this to False to avoid a lot of log outputs;
        each task's output can be redirected to their own files.
      backend: Backend; backend to use for executing the tasks. Defaults to
        CloudVmRayBackend()
      minimize: bool; the dag optimization metric, e.g. sky.Optimizer.COST.
      provision_retry: bool; whether to retry provisioning when a launchable 
        resource fails to provision.
    """
    # TODO: Azure. Port some of execute_v1()'s nice logging messages.
    assert len(dag) == 1, 'Job launcher assumes 1 task for now.'

    backend = backend if backend is not None else backends.CloudVmRayBackend()

    if minimize is not None:
        dag = sky.optimize(dag, minimize)
    task = dag.tasks[0]
    best_resources = task.best_resources

    # Future: we can `for task in dag.get_topo_tasks():` and prune the nodes
    # from the dag that have already been successfully provisioned.
    if provision_retry:
        assert minimize is not None, 'minimize must be specified for provision_retry'
        handle = backend.retrying_cloud_vm_provision(dag, dryrun, stream_logs,
                                                     minimize)
    else:
        handle = backend.provision(task,
                                   best_resources,
                                   dryrun=dryrun,
                                   stream_logs=stream_logs)
    task = dag.tasks[0]

    if dryrun:
        logger.info('Dry run finished.')
        return

    if task.workdir is not None:
        backend.sync_workdir(handle, task.workdir)

    backend.sync_file_mounts(handle, task.file_mounts,
                             task.get_cloud_to_remote_file_mounts())

    if task.post_setup_fn is not None:
        backend.run_post_setup(handle, task.post_setup_fn, task)

    try:
        backend.execute(handle, task, stream_logs)
    finally:
        # Enables post_execute() to be run after KeyboardInterrupt.
        backend.post_execute(handle, teardown)

    if teardown:
        backend.teardown(handle)


execute = execute_v3
