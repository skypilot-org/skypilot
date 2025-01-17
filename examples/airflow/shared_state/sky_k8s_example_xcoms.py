# This is a WIP example that uses xcom serialization to pass state.db and sky keys between tasks.
# This should not required PVCs to be mounted to the pod, and should be able to run on any Kubernetes cluster.
from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.kubernetes_pod import (
    KubernetesPodOperator)
from airflow.utils.dates import days_ago

default_args = {
    'owner': 'airflow',
    'start_date': days_ago(1),
}


def get_skypilot_task(task_id: str,
                      sky_command: str,
                      previous_task_id: str = None,
                      serialize_xcom: bool = False):
    cmds = [
        "/bin/bash", "-i", "-c",
        "chown -R 1000:1000 /home/sky/.sky /home/sky/.ssh && "
    ]

    if previous_task_id is not None:
        # Deserialize state.db and sky keys from xcom (if needed)
        # TODO(romilb): Implement this using {{ ti.xcom_pull() }} templating
        cmds.append(' echo \'{{ ti.xcom_pull(task_ids="' + previous_task_id +
                    '")["state_db"] }}\' > /home/sky/.sky/state.db &&'
                    ' echo \'{{ ti.xcom_pull(task_ids="' + previous_task_id +
                    '")["sky_key"] }}\' > /home/sky/.ssh/sky-key &&'
                    ' echo \'{{ ti.xcom_pull(task_ids="' + previous_task_id +
                    '")["sky_key_pub"] }}\' > /home/sky/.ssh/sky-key.pub')

    cmds.append(
        f"pip install skypilot-nightly[kubernetes] && {sky_command} && ")

    if serialize_xcom:
        # Serialize state.db and sky keys into xcom
        cmds.append(
            'echo \'{"state_db": "$(cat /home/sky/.sky/state.db)", '
            '"sky_key": "$(cat /home/sky/.ssh/sky-key)", '
            '"sky_key_pub": "$(cat /home/sky/.ssh/sky-key.pub)"}\' > /airflow/xcom/return.json'
        )

    task = KubernetesPodOperator(
        task_id=task_id,
        name="skypilot-pod",
        namespace="default",
        image=
        "us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:20240613",
        cmds=["/bin/bash", "-i", "-c"],
        arguments=["".join(cmds)],
        service_account_name="sky-airflow-sa",
        env_vars={"HOME": "/home/sky"},
        is_delete_operator_pod=True,
        get_logs=True,
        do_xcom_push=serialize_xcom  # Only push XCom if we're serializing data
    )
    return task


with DAG(dag_id='sky_k8s_example_xcoms',
         default_args=default_args,
         schedule_interval=None,
         catchup=False) as dag:
    # Task to launch a SkyPilot cluster
    sky_launch = get_skypilot_task(
        "sky_launch",
        "sky launch -y -c train --cloud kubernetes -- echo training the model",
        previous_task_id=None,
        serialize_xcom=True)
    # Task to get the logs of the SkyPilot cluster
    sky_logs = get_skypilot_task("sky_logs",
                                 "sky logs train > task_logs.txt",
                                 previous_task_id='sky_launch',
                                 serialize_xcom=True)
    # Task to get the list of SkyPilot clusters
    sky_status = get_skypilot_task("sky_status",
                                   "sky status",
                                   previous_task_id='sky_logs',
                                   serialize_xcom=True)
    # Task to delete the SkyPilot cluster
    sky_down = get_skypilot_task("sky_down",
                                 "sky down train",
                                 previous_task_id='sky_status',
                                 serialize_xcom=False)

    sky_launch >> sky_logs >> sky_status >> sky_down
