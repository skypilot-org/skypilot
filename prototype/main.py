import dataclasses
import datetime
import enum
import os
import subprocess
import time
from typing import Optional
import webbrowser

from absl import app
from absl import flags
from absl import logging
import jinja2


FLAGS = flags.FLAGS
flags.DEFINE_string(
    "cluster_config_template",
    os.path.join("/Users/lsf/projects/sky-experiments/prototype", "config/aws.yml.j2"),
    "path to the cluster config file relative to repository root",
)
flags.DEFINE_string(
    "dag_template",
    "sky_dag.py.j2",
    "path to the Airflow DAG file template",
)
flags.DEFINE_string(
    "airflow_dags_path",
    "/Users/lsf/airflow/dags",
    "path to the Airflow DAGs location",
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
    working_dir: str
    run_command: str
    output_path: str
    output_cloud_uri: str
    timeout_seconds: int


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    logging.info("$ " + cmd)
    ret = subprocess.run(cmd, shell=True, **kwargs)
    ret.check_returncode()
    return ret


def get_run_id() -> str:
    return "sky_" + datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")


def get_execution_plan_node():
    return ExecutionPlanNode(
        "root",
        "test run",
        CloudProvider.AWS,
        "us-west-2",
        "t3.micro",
        "/Users/lsf/projects/sky-experiments/prototype/user/",
        "echo hello world > task_output",
        "task_output",
        "s3://intercloud-data/test_output",
        3600,
    )


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
            "working_dir": node.working_dir,
        },
    )


def start_airflow(node: ExecutionPlanNode):
    cluster_config_file = write_cluster_config(node)
    dag_id = get_run_id()
    os.makedirs(FLAGS.airflow_dags_path, exist_ok=True)
    output_path = f"{FLAGS.airflow_dags_path}/{dag_id}.py"
    create_from_template(
        FLAGS.dag_template,
        {
            "cluster_config_file": cluster_config_file,
            "dag_id": dag_id,
            "output_cloud_uri": node.output_cloud_uri,
            "output_path": node.output_path,
            "run_command": node.run_command,
            "timeout_seconds": node.timeout_seconds,
        },
        output_path,
    )
    airflow_wait_seconds = 2
    logging.info(f"Waiting {airflow_wait_seconds}s for Airflow to pick up the DAG")
    time.sleep(airflow_wait_seconds)
    run(f"airflow dags trigger {dag_id}")
    webbrowser.open("http://localhost:8080/dagrun/list/")


def main(argv):
    del argv  # Unused.
    node = get_execution_plan_node()
    start_airflow(node)


if __name__ == "__main__":
    app.run(main)
