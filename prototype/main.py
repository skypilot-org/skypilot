import dataclasses
import datetime
import enum
import json
import logging
import os
import sys
import subprocess
import time
from typing import Dict, List, Optional

from absl import app
from absl import flags
import colorama
from colorama import Fore, Style
import jinja2


FLAGS = flags.FLAGS
flags.DEFINE_string(
    "cluster_config_template",
    "config/aws.yml.j2",
    "path to the cluster config file relative to repository root",
)
flags.DEFINE_string(
    "logs_directory",
    "logs",
    "path to runtime logs",
)


class CloudProvider(enum.Enum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"


@dataclasses.dataclass
class ExecutionPlanNode:
    id: str
    description: str
    cloud: CloudProvider
    region: str
    instance_type: str
    working_dir: Optional[str]
    setup_commands: List[str]
    run_command: str
    output_path: str
    output_cloud_uri: str
    timeout_seconds: int


def get_execution_plan_node():
    return ExecutionPlanNode(
        "root",
        "test run",
        CloudProvider.AWS,
        "us-west-2",
        "p3.2xlarge",
        None,
        [
            "git clone https://github.com/concretevitamin/tpu.git; cd tpu; git checkout -f gpu_train",
        ],
        "python ~/tpu/models/official/resnet/resnet_main.py --use_tpu=False --mode=train --model_dir=~/resnet_model --data_dir=gs://cloud-tpu-test-datasets/fake_imagenet --train_batch_size=256 --train_steps=112590 --amp --xla --loss_scale=128 --iterations_per_loop=1251 2>&1 | tee ~/run.log",
        "run.log",
        "s3://intercloud-data/run.log",
        3600,
    )


class JsonLogger:
    def __init__(self, logger_name: str, log_file_path: str):
        self.logger = logging.getLogger(logger_name)
        self.logger.handlers = []
        self.logger.addHandler(logging.FileHandler(log_file_path))

    def log(self, event: str, payload: Dict = {}):
        now = time.time()
        json_payload = {"time": time.time(), "event": event}
        json_payload.update(payload)
        self.logger.debug(json.dumps(json_payload))
        msg = f"{Fore.CYAN}{Style.BRIGHT}{event}{Style.RESET_ALL}"
        if len(payload) > 0:
            for k, v in payload.items():
                msg += f" {Fore.MAGENTA}{k}={v}"
        msg += f"{Fore.RESET}"
        logging.info(msg)


class Step:
    def __init__(self, runner: "Runner", step_id: str, shell_command: str):
        self.runner = runner
        self.step_id = str(step_id)
        self.shell_command = shell_command

    def run(self, **kwargs) -> subprocess.CompletedProcess:
        logfile = os.path.join(self.runner.logs_root, f"{self.step_id}.log")
        self.shell_command += f" 2>&1 | tee {logfile}"
        proc = subprocess.Popen(
            self.shell_command,
            shell=True,
            **kwargs,
        )
        proc.communicate()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)


class Runner:
    """
    FIXME: This is a linear sequence of steps for now. Will upgrade to a DAG.
    """

    def __init__(self, run_id: Optional[str] = None, steps: List[Step] = []):
        if run_id is None:
            run_id = get_run_id()
        self.run_id = run_id
        self.steps = steps
        self.next_step_id = 0
        self.logs_root = os.path.join(FLAGS.logs_directory, run_id)
        os.makedirs(self.logs_root, exist_ok=True)
        self.logger = JsonLogger(run_id, os.path.join(self.logs_root, "runner.log"))

    def add_step(self, shell_command: str, step_id: str = "") -> "Runner":
        step_id = f"{self.next_step_id:03}_{step_id}"
        self.next_step_id += 1
        self.steps.append(Step(self, step_id, shell_command))
        return self

    def run(self) -> "Runner":
        self.logger.log("start_run")
        for step in self.steps:
            self.logger.log(
                "start_step",
                {"step_id": step.step_id, "shell_command": step.shell_command},
            )
            step.run()
            self.logger.log("finish_step")
        self.logger.log("finish_run")
        return self


def get_run_id() -> str:
    return "sky_" + datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")


def create_from_template(
    template_path: str, variables: dict, output_path: Optional[str] = None
) -> str:
    """Create a file from a Jinja template and return the filename."""
    assert template_path.endswith(".j2"), template_path
    with open(template_path) as fin:
        template = fin.read()
    template = jinja2.Template(template)
    content = template.render(**variables)
    if output_path is None:
        output_path, _ = template_path.rsplit(".", 1)
    with open(output_path, "w") as fout:
        fout.write(content)
    logging.info(f"Created or updated file {output_path}")
    return output_path


def write_cluster_config(node: ExecutionPlanNode) -> str:
    return create_from_template(
        FLAGS.cluster_config_template,
        {
            "instance_type": node.instance_type,
            "setup_commands": node.setup_commands,
            "working_dir": node.working_dir,
        },
    )


def execute_node(node: ExecutionPlanNode) -> Runner:
    cluster_config_file = write_cluster_config(node)
    runner = (
        Runner()
        .add_step(f"ray up -y {cluster_config_file} --no-config-cache", "setup")
        .add_step(f"ray exec {cluster_config_file} '{node.run_command}'", "exec")
        .add_step(
            f"ray exec {cluster_config_file} "
            f"'aws s3 cp {node.output_path} {node.output_cloud_uri}'",
            "save_result_to_cloud",
        )
        .add_step(f"ray down -y {cluster_config_file}", "teardown")
    )
    return runner.run()


def main(argv):
    del argv  # Unused.
    colorama.init()
    node = get_execution_plan_node()
    execute_node(node)


if __name__ == "__main__":
    app.run(main)
