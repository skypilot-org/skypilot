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
    INIT_COMMANDS = (
        # Install gcloud CLI and source the bashrc for accessing buckets in tasks
        'sudo conda install -y -c conda-forge google-cloud-sdk ')

    # Install SkyPilot and clone the mock train workflow repo
    # In your workflow, you can have skypilot and the code baked into the image
    SETUP_COMMAND = (
        "pip install skypilot-nightly[kubernetes,gcp] &&"
        "git clone https://github.com/romilbhardwaj/mock_train_workflow.git /home/sky/mock_train_workflow"
    )

    # Command to extract the gcloud secrets tarball
    EXTRACT_GCLOUD = (
        "mkdir -p /home/sky/.config/gcloud && "
        "tar -xzf /tmp/gcloud-secrets/gcloud-config.tar.gz -C /home/sky/.config/gcloud "
    )

    skypilot_task = KubernetesPodOperator(
        task_id=task_id,
        name="skypilot-pod",
        namespace="default",
        image=
        "us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:20240613",
        cmds=["/bin/bash", "-i", "-c"],
        arguments=[
            f"{INIT_COMMANDS} && "
            f"{EXTRACT_GCLOUD} && "
            f"{SETUP_COMMAND} && "
            f"{sky_command}"
        ],
        service_account_name="sky-airflow-sa",
        env_vars={"HOME": "/home/sky"},
        volumes=[
            k8s.V1Volume(
                name="gcloud-secret-volume",
                secret=k8s.V1SecretVolumeSource(secret_name="gcloud-secret"),
            ),
        ],
        volume_mounts=[
            k8s.V1VolumeMount(name="gcloud-secret-volume",
                              mount_path="/tmp/gcloud-secrets"),
        ],
        is_delete_operator_pod=True,
        get_logs=True,
    )
    return skypilot_task


with DAG(dag_id='sky_k8s_train_pipeline',
         default_args=default_args,
         schedule_interval=None,
         catchup=False) as dag:
    # Make sure bucket exists with gsutil mb -l us-central1 gs://<bucket-name>
    bucket_url = "gs://sky-data-demo"

    # Launch data preprocessing task. We use --down to clean up the SkyPilot cluster after the task is done.
    data_preprocess = get_skypilot_task(
        "sky_data_preprocess",
        f"sky launch -y -c data --down --cloud kubernetes --env DATA_BUCKET_URL={bucket_url} mock_train_workflow/data_preprocessing.yaml"
    )

    # Task to train the model
    train = get_skypilot_task(
        "sky_train",
        f"sky launch -y -c train --down --cloud kubernetes --env DATA_BUCKET_URL={bucket_url} mock_train_workflow/train.yaml"
    )

    # Task to evaluate the trained model. This can optionally be run on the same cluster as the training task using `sky exec`
    eval = get_skypilot_task(
        "sky_eval",
        f"sky launch -y -c eval --down --cloud kubernetes --env DATA_BUCKET_URL={bucket_url} mock_train_workflow/eval.yaml"
    )

    data_preprocess >> train >> eval
