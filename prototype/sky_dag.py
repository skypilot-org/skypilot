#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Example DAG demonstrating the usage of the BashOperator."""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="sky_dag",
    schedule_interval="0 0 * * *",
    start_date=datetime(2021, 1, 1),
    catchup=False,
    dagrun_timeout=timedelta(minutes=60),
    tags=["sky"],
    params={
        "cluster_config_file": "/Users/lsf/projects/sky-experiments/prototype/config/aws.yml",
        "run_command": "echo hello world > task_output",
        "output_path": "task_output",
        "output_cloud_uri": "s3://intercloud-data/test_output",
    },
) as dag:
    setup = BashOperator(
        task_id="ray_up",
        bash_command="ray up -y {{params.cluster_config_file}} --no-config-cache",
    )

    execute = BashOperator(
        task_id="ray_exec",
        bash_command="ray exec {{params.cluster_config_file}} '{{params.run_command}}'",
    )

    save_result = BashOperator(
        task_id="copy_result_to_s3",
        bash_command="ray exec {{params.cluster_config_file}} 'aws s3 cp {{params.output_path}} {{params.output_cloud_uri}}'",
    )

    teardown = BashOperator(
        task_id="ray_down",
        bash_command="ray down -y {{params.cluster_config_file}}",
    )

    setup >> execute >> save_result >> teardown

if __name__ == "__main__":
    dag.cli()
