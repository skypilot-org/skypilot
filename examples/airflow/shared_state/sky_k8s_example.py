from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.kubernetes_pod import (
    KubernetesPodOperator)
from airflow.utils.dates import days_ago
from kubernetes.client import models as k8s

default_args = {
    'owner': 'airflow',
    'start_date': days_ago(1),
}


def get_skypilot_task(task_id: str, sky_command: str):
    skypilot_task = KubernetesPodOperator(
        task_id=task_id,
        name="skypilot-pod",
        namespace="default",
        image=
        "us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:20240613",
        cmds=["/bin/bash", "-i", "-c"],
        arguments=[
            "chown -R 1000:1000 /home/sky/.sky /home/sky/.ssh && "
            "pip install skypilot-nightly[kubernetes] && "
            f"{sky_command}"
        ],
        service_account_name="sky-airflow-sa",
        env_vars={"HOME": "/home/sky"},
        volumes=[
            k8s.V1Volume(
                name="sky-pvc",
                persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(
                    claim_name="sky-pvc"),
            ),
        ],
        volume_mounts=[
            k8s.V1VolumeMount(name="sky-pvc",
                              mount_path="/home/sky/.sky",
                              sub_path="sky"),
            k8s.V1VolumeMount(name="sky-pvc",
                              mount_path="/home/sky/.ssh",
                              sub_path="ssh"),
        ],
        is_delete_operator_pod=True,
        get_logs=True,
    )
    return skypilot_task


with DAG(dag_id='sky_k8s_example',
         default_args=default_args,
         schedule_interval=None,
         catchup=False) as dag:
    # Task to launch a SkyPilot cluster
    sky_launch = get_skypilot_task(
        "sky_launch",
        "sky launch -y -c train --cloud kubernetes -- echo training the model")
    # Task to get the logs of the SkyPilot cluster
    sky_logs = get_skypilot_task("sky_logs", "sky logs train > task_logs.txt")
    # Task to get the list of SkyPilot clusters
    sky_status = get_skypilot_task("sky_status", "sky status")
    # Task to delete the SkyPilot cluster
    sky_down = get_skypilot_task("sky_down", "sky down train")

    sky_launch >> sky_logs >> sky_status >> sky_down
