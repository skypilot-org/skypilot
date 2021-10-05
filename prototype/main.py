import dataclasses
import enum
import json
import os
import subprocess

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
    "dag_path",
    "/Users/lsf/projects/sky-experiments/prototype",
    "path to the Airflow DAG file",
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


def run(cmd, **kwargs):
    logging.info("$ " + cmd)
    ret = subprocess.run(cmd, shell=True, **kwargs)
    ret.check_returncode()
    return ret


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
    )


def write_cluster_config(node: ExecutionPlanNode):
    template_path = FLAGS.cluster_config_template
    assert template_path.endswith(".yml.j2"), template_path
    with open(template_path) as fin:
        template = fin.read()
    template = jinja2.Template(template)
    variables = {
        "NODE_TYPE": node.instance_type,
        "WORKING_DIR": node.working_dir,
    }
    conf = template.render(**variables)
    output_path, _ = template_path.rsplit(".", 1)
    with open(output_path, "w") as fout:
        fout.write(conf)
    return output_path


def trigger_airflow(node: ExecutionPlanNode, cluster_config_file: str):
    config = {
        "cluster_config_file": cluster_config_file,
        "run_command": node.run_command,
        "output_path": node.output_path,
        "output_cloud_uri": node.output_cloud_uri,
    }
    config_json = json.dumps(config)
    subdir = "-S /Users/lsf/projects/sky-experiments/prototype"
    run(f"airflow dags trigger sky_dag -c '{config_json}' {subdir}")


def main(argv):
    del argv  # Unused.
    node = get_execution_plan_node()
    cluster_config_file = write_cluster_config(node)
    trigger_airflow(node, cluster_config_file)


if __name__ == "__main__":
    app.run(main)
