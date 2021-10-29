"""Execution layer: resource provisioner + task launcher.

Usage:

   >> planned_dag = sky.Optimizer.optimize(dag)
   >> sky.execute(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import colorama
from colorama import Fore, Style
import datetime
import jinja2
import json
import os
import re
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional, Union
import yaml

import sky
from sky.authentication import *
from sky import cloud_stores

IPAddr = str
RunId = str

SKY_LOGS_DIRECTORY = './logs'
STREAM_LOGS_TO_CONSOLE = True
CLUSTER_CONFIG_FILE = None
TASK = None

# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_WORKDIR = '/tmp/workdir'

_CLOUD_TO_TEMPLATE = {
    sky.clouds.AWS: 'config/aws.yml.j2',
    sky.clouds.Azure: 'config/azure.yml.j2',
    sky.clouds.GCP: 'config/gcp.yml.j2',
}


def _get_cluster_config_template(task):
    cloud = task.best_resources.cloud
    if task.num_nodes > 1 and str(cloud) == 'AWS':
        return 'config/aws-distributed.yml.j2'
    return _CLOUD_TO_TEMPLATE[type(cloud)]


def _fill_template(template_path: str,
                   variables: dict,
                   output_path: Optional[str] = None) -> str:
    """Create a file from a Jinja template and return the filename."""
    assert template_path.endswith('.j2'), template_path
    with open(template_path) as fin:
        template = fin.read()
    template = jinja2.Template(template)
    content = template.render(**variables)
    if output_path is None:
        output_path, _ = template_path.rsplit('.', 1)
    with open(output_path, 'w') as fout:
        fout.write(content)
    print(f'Created or updated file {output_path}')
    return output_path


def _write_cluster_config(run_id: RunId, task, cluster_config_template: str):
    cloud = task.best_resources.cloud
    resources_vars = cloud.make_deploy_resources_variables(task)
    return _fill_template(
        cluster_config_template,
        dict(
            resources_vars, **{
                'run_id': run_id,
                'setup_command': task.setup,
                'workdir': task.workdir,
                'docker_image': task.docker_image,
                'container_name': task.container_name,
                'num_nodes': task.num_nodes,
                'file_mounts': task.get_local_to_remote_file_mounts() or {},
                'max_nodes': task.max_nodes,
            }))


def _execute_single_node_command(ip, command, private_key, container_name):
    final_command = command
    if container_name:

        def nest_command(command):
            return command.replace('\\', '\\\\').replace('"', '\\"')

        raw_command = nest_command(command)
        final_command = "docker exec {} /bin/bash -c \"{}\"".format(
            container_name, raw_command)
    import pdb
    pdb.set_trace()
    ssh = subprocess.Popen([
        "ssh", "-i", private_key, "-o", "StrictHostKeyChecking=no",
        "ubuntu@{}".format(ip), final_command
    ])


def _get_run_id() -> RunId:
    return 'sky-' + datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f')


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

    def __init__(self, runner: 'Runner', step_id: str, step_desc: str,
                 execute_fn: Union[str, Callable[[List[IPAddr]], Dict[IPAddr,
                                                                      str]]]):
        self.runner = runner
        self.step_id = str(step_id)
        self.step_desc = step_desc
        self.execute_fn = execute_fn

    def run(self, **kwargs) -> subprocess.CompletedProcess:
        log_path = os.path.join(self.runner.logs_root, f'{self.step_id}.log')
        log_abs_path = os.path.abspath(log_path)
        tail_cmd = f'tail -n100 -f {log_abs_path}'
        # @Frank Fix this
        if STREAM_LOGS_TO_CONSOLE:
            # TODO: `ray up` has a bug where if you redirect stdout and stderr, stdout is not flushed.
            with open(log_path, 'w') as fout:
                proc = subprocess.Popen(
                    self.execute_fn,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                for line in proc.stdout:
                    sys.stdout.write(line)
                    fout.write(line)
                proc.communicate()
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(
                        proc.returncode,
                        proc.args,
                    )
                return proc
        else:
            print(
                f'To view progress: {Style.BRIGHT}{tail_cmd}{Style.RESET_ALL}')
            return subprocess.run(
                self.execute_fn + f' 2>&1 >{log_path}',
                shell=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )


class Runner:
    """
    FIXME: This is a linear sequence of steps for now. Will upgrade to a DAG.
    """

    def __init__(self, run_id: RunId, steps: List[Step] = [], task=None):
        self.run_id = run_id
        self.steps = steps
        self.next_step_id = 0
        self.logs_root = os.path.join(SKY_LOGS_DIRECTORY, run_id)
        os.makedirs(self.logs_root, exist_ok=True)
        self.logger = EventLogger(os.path.join(self.logs_root, '_events.jsonl'))
        self.cluster_ips = []
        self.task = task

    def add_step(self, step_name: str, step_desc: str,
                 execute_fn: str) -> 'Runner':
        step_id = f'{self.next_step_id:03}_{step_name}'
        self.next_step_id += 1
        self.steps.append(Step(self, step_id, step_desc, execute_fn))
        return self

    def run(self) -> 'Runner':
        self.logger.log('start_run')
        print(f'{Fore.GREEN}', end='')
        print('--------------------------')
        print('  Sky execution started')
        print('--------------------------')
        print(f'{Fore.RESET}')

        try:
            for step in self.steps:
                self.logger.log(
                    'start_step',
                    {
                        'step_id': step.step_id,
                        'step_desc': step.step_desc,
                        'execute_fn': str(step.execute_fn),
                    },
                )
                print(
                    f'{Fore.CYAN}Step {step.step_id} started: {step.step_desc}{Fore.RESET}\n{Style.DIM}{step.execute_fn}{Style.RESET_ALL}'
                )
                if isinstance(step.execute_fn, str):
                    if 'ray get-head-ip' in step.execute_fn:
                        output = subprocess.run(step.execute_fn,
                                                shell=True,
                                                check=True,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT)
                        str_output = output.stdout.decode('utf-8')
                        ips = re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
                                         str_output)
                        assert len(ips) == 1, "Only 1 Node can be Head Node!"
                        self.cluster_ips.append(ips[0])
                    elif 'ray get-worker-ips' in step.execute_fn:
                        output = subprocess.run(step.execute_fn,
                                                shell=True,
                                                check=True,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT)
                        str_output = output.stdout.decode('utf-8')
                        ips = re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
                                         str_output)
                        self.cluster_ips.extend(ips)
                    elif 'ray up' in step.execute_fn:
                        output = step.run()
                        # Wait for all workers to setup post setup
                        while True:
                            if TASK.num_nodes <= 1:
                                break
                            proc = subprocess.run(
                                f"ray exec {CLUSTER_CONFIG_FILE} 'ray status'",
                                shell=True,
                                check=True,
                                capture_output=True)
                            output = proc.stdout.decode("ascii")
                            print(output)
                            self.logger.log(output)
                            if f"{TASK.num_nodes-1} ray.worker.default" in output:
                                break
                            time.sleep(5)
                    else:
                        output = step.run()
                else:
                    fn = step.execute_fn
                    if "post_setup" in step.step_id:
                        commands = fn(self.cluster_ips)
                        for k, v in commands.items():
                            _execute_single_node_command(
                                ip=k,
                                command=v,
                                private_key=TASK.private_key,
                                container_name=TASK.container_name)
                    elif "exec" in step.step_id:
                        commands = fn(self.cluster_ips)
                        for k, v in commands.items():
                            v = f'cd {SKY_REMOTE_WORKDIR} && ' + v
                            _execute_single_node_command(
                                ip=k,
                                command=v,
                                private_key=TASK.private_key,
                                container_name=TASK.container_name)

                self.logger.log('finish_step')
                print(f'{Fore.CYAN}Step {step.step_id} finished{Fore.RESET}\n')

            self.logger.log('finish_run')
            print(f'{Fore.GREEN}', end='')
            print('---------------------------')
            print('  Sky execution finished')
            print('---------------------------')
            print(f'{Fore.RESET}')
            return self
        except subprocess.CalledProcessError as e:
            print(f'{Fore.RED}Step failed! {e}{Fore.RESET}')
            raise e


def _verify_ssh_authentication(cloud_type, config, cluster_config_file):
    cloud_type = str(cloud_type)
    if cloud_type == 'AWS':
        config = setup_aws_authentication(config)
    elif cloud_type == 'GCP':
        config = setup_gcp_authentication(config)
    elif cloud_type == 'Azure':
        config = setup_azure_authentication(config)
    else:
        raise ValueError("Cloud type not supported, must be [AWS, GCP, Azure]")

    with open(cluster_config_file, 'w') as yaml_file:
        yaml.dump(config, yaml_file, default_flow_style=False)


def execute(dag: sky.Dag, dryrun: bool = False, teardown: bool = False):
    global CLUSTER_CONFIG_FILE
    global TASK

    colorama.init()

    assert len(dag) == 1, 'Job launcher assumes 1 task for now'
    task = dag.tasks[0]

    run_id = _get_run_id()
    cluster_config_file = _write_cluster_config(
        run_id, task, _get_cluster_config_template(task))

    if dryrun:
        print('Dry run finished.')
        return

    CLUSTER_CONFIG_FILE = cluster_config_file
    TASK = task
    autoscaler_dict = yaml.safe_load(open(CLUSTER_CONFIG_FILE))
    _verify_ssh_authentication(task.best_resources.cloud, autoscaler_dict,
                               cluster_config_file)

    # FIXME: if a command fails, stop the rest.
    runner = Runner(run_id)
    runner.add_step('provision', 'Provision resources',
                    f'ray up -y {cluster_config_file} --no-config-cache')

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

    runner.add_step('get_head_ip', 'Get Head IP',
                    f'ray get-head-ip {cluster_config_file}')

    runner.add_step('get_worker_ips', 'Get Worker IP',
                    f'ray get-worker-ips {cluster_config_file}')

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
    runner.run()
    if not teardown:
        print(
            f'  To log into the cloud VM:\t{Style.BRIGHT}ray attach {cluster_config_file} {Style.RESET_ALL}\n'
        )
        print(
            f'  To teardown the resources:\t{Style.BRIGHT}ray down {cluster_config_file} -y {Style.RESET_ALL}\n'
        )
