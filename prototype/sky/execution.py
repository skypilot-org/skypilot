"""Execution layer: resource provisioner + task launcher.

Usage:

   >> planned_dag = sky.Optimizer.optimize(dag)
   >> sky.execute(planned_dag)

Current resource privisioners:

  - Ray autoscaler

Current task launcher:

  - ray exec + each task's commands
"""
import datetime
import json
import os
import subprocess
import time
from typing import List, Optional

import colorama
from colorama import Fore, Style
import jinja2

import sky

RunId = str

SKY_LOGS_DIRECTORY = './logs'
STREAM_LOGS_TO_CONSOLE = True

# NOTE: keep in sync with the cluster template 'file_mounts'.
SKY_REMOTE_WORKDIR = '/tmp/workdir'

_CLOUD_TO_TEMPLATE = {
    sky.clouds.AWS: 'config/aws.yml.j2',
    sky.clouds.GCP: 'config/gcp.yml.j2',
}


def _get_cluster_config_template(task):
    cloud = task.best_resources.cloud
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
    return _fill_template(
        cluster_config_template,
        {
            'instance_type': task.best_resources.types,
            'run_id': run_id,
            'setup_command': task.setup,
            'workdir': task.workdir,
        },
    )


def _get_run_id() -> RunId:
    return 'sky_' + datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f')


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
                 shell_command: str):
        self.runner = runner
        self.step_id = str(step_id)
        self.step_desc = step_desc
        self.shell_command = shell_command

    def run(self, **kwargs) -> subprocess.CompletedProcess:
        log_path = os.path.join(self.runner.logs_root, f'{self.step_id}.log')
        log_abs_path = os.path.abspath(log_path)
        tail_cmd = f'tail -n100 -f {log_abs_path}'
        if STREAM_LOGS_TO_CONSOLE:
            return subprocess.run(
                self.shell_command + f' 2>&1 | tee {log_path}',
                shell=True,
                check=True,
            )  # TODO: `ray up` has a bug where if you redirect stdout and stderr, stdout is not flushed.
        else:
            print(
                f'To view progress: {Style.BRIGHT}{tail_cmd}{Style.RESET_ALL}')
            return subprocess.run(
                self.shell_command + f' 2>&1 >{log_path}',
                shell=True,
                check=True,
            )


class Runner:
    """
    FIXME: This is a linear sequence of steps for now. Will upgrade to a DAG.
    """

    def __init__(self, run_id: RunId, steps: List[Step] = []):
        self.run_id = run_id
        self.steps = steps
        self.next_step_id = 0
        self.logs_root = os.path.join(SKY_LOGS_DIRECTORY, run_id)
        os.makedirs(self.logs_root, exist_ok=True)
        self.logger = EventLogger(os.path.join(self.logs_root, '_events.jsonl'))

    def add_step(self, step_name: str, step_desc: str,
                 shell_command: str) -> 'Runner':
        step_id = f'{self.next_step_id:03}_{step_name}'
        self.next_step_id += 1
        self.steps.append(Step(self, step_id, step_desc, shell_command))
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
                        'shell_command': step.shell_command,
                    },
                )
                print(
                    f'{Fore.CYAN}Step {step.step_id} started: {step.step_desc}{Fore.RESET}\n{Style.DIM}{step.shell_command}{Style.RESET_ALL}'
                )
                step.run()
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


def execute(dag: sky.Dag, teardown: bool = False):
    colorama.init()

    assert len(dag) == 1, 'Job launcher assumes 1 task for now'
    task = dag.tasks[0]

    run_id = _get_run_id()
    cluster_config_file = _write_cluster_config(
        run_id, task, _get_cluster_config_template(task))

    runner = Runner(run_id)
    runner.add_step('provision', 'Provision resources',
                    f'ray up -y {cluster_config_file} --no-config-cache')
    runner.add_step(
        'sync', 'Sync files',
        f'ray rsync_up {cluster_config_file} {task.workdir} {SKY_REMOTE_WORKDIR}'
    )
    runner.add_step(
        'exec', 'Execute task',
        f'ray exec {cluster_config_file} \'cd {SKY_REMOTE_WORKDIR} && {task.run}\''
    )
    if teardown:
        runner.add_step('teardown', 'Tear down resources',
                        f'ray down -y {cluster_config_file}')
    runner.run()
    if not teardown:
        print(
            f'  To log into the cloud VM:\t{Style.BRIGHT}ray attach {cluster_config_file} {Style.RESET_ALL}\n'
        )
        print(
            f'  To teardown the resources:\t{Style.BRIGHT}ray down {cluster_config_file} {Style.RESET_ALL}\n'
        )
