# Running SkyPilot tasks in an Airflow DAG

SkyPilot can be used in an orchestration framework like Airflow to launch tasks as a part of a DAG.

In this guide, we demonstrate how some simple SkyPilot operations, such as launching a cluster, getting its logs and tearing it down, can be orchestrated using Airflow.

<p align="center">
  <img src="https://i.imgur.com/BVZBaR9.png" width="800">
</p>

## Prerequisites

* Airflow installed on a [Kubernetes cluster](https://airflow.apache.org/docs/helm-chart/stable/index.html) or [locally](https://airflow.apache.org/docs/apache-airflow/stable/start.html) (`SequentialExecutor`)
* A Kubernetes cluster to run tasks on. We'll use GKE in this example.
  * You can use our guide on [setting up a Kubernetes cluster](https://skypilot.readthedocs.io/en/latest/reference/kubernetes/kubernetes-setup.html).
  * A persistent volume storage class should be available that supports at least `ReadWriteOnce` access mode. GKE has this supported by default.

## Preparing the Kubernetes Cluster

1. Provision a service account on your Kubernetes cluster for SkyPilot to use to launch tasks. 
   ```bash
   kubectl apply -f sky-sa.yaml
   ```
   For reference, here are the contents of `sky-sa.yaml`:
    ```yaml
    # sky-sa.yaml
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: sky-airflow-sa
      namespace: default
    ---
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRoleBinding
    metadata:
      name: sky-airflow-sa-binding
    subjects:
    - kind: ServiceAccount
      name: sky-airflow-sa
      namespace: default
    roleRef:
      # For minimal permissions, refer to https://skypilot.readthedocs.io/en/latest/cloud-setup/cloud-permissions/kubernetes.html
      kind: ClusterRole
      name: cluster-admin
      apiGroup: rbac.authorization.k8s.io
    ```

2. Provision a persistent volume for SkyPilot to store state across runs.
   ```bash
   kubectl apply -f sky-pv.yaml
   ```
   For reference, here are the contents of `sky-pv.yaml`:
    ```yaml
    # sky-pv.yaml
    apiVersion: v1
    kind: PersistentVolumeClaim
    metadata:
      name: sky-pvc
    spec:
      accessModes:
        - ReadWriteOnce
      resources:
        requests:
          storage: 10Gi # 10Gi is minimum for GKE pd-balanced
      storageClassName: standard-rwo
    ```
   Note: The `storageClassName` should be set to the appropriate storage class that's supported on your cluster. If you have many concurrent tasks, you may want to use a storage class that supports `ReadWriteMany` access mode.

## Writing the Airflow DAG

We provide an example DAG in `sky_k8s_example.py` that:
1. Launches a SkyPilot cluster.
2. Writes logs from the cluster to a local file
3. Checks the status of the cluster and prints to Airflow logs
4. Tears down the cluster.

The DAG is defined in `sky_k8s_example.py`:

```python
# sky_k8s_example.py
from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.kubernetes_pod import KubernetesPodOperator
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
        image="us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:20240613",
        cmds=["/bin/bash", "-i", "-c"],
        arguments=[
            "chown -R 1000:1000 /home/sky/.sky /home/sky/.ssh && "
            "pip install skypilot-nightly[kubernetes] && "
            f"{sky_command}"],
        service_account_name="sky-airflow-sa",
        env_vars={"HOME": "/home/sky"},
        volumes=[
            k8s.V1Volume(
                name="sky-pvc",
                persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(
                    claim_name="sky-pvc"
                ),
            ),
        ],
        volume_mounts=[
            k8s.V1VolumeMount(name="sky-pvc", mount_path="/home/sky/.sky",
                              sub_path="sky"),
            k8s.V1VolumeMount(name="sky-pvc", mount_path="/home/sky/.ssh",
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
    cmds = ("git clone https://github.com/skypilot-org/skypilot.git && "
            "sky launch -y -c train --cloud kubernetes skypilot/examples/minimal.yaml")
    sky_launch = get_skypilot_task("sky_launch", cmds)
    # Task to get the logs of the SkyPilot cluster
    sky_logs = get_skypilot_task("sky_logs", "sky logs train > task_logs.txt")
    # Task to get the list of SkyPilot clusters
    sky_status = get_skypilot_task("sky_status", "sky status")
    # Task to delete the SkyPilot cluster
    sky_down = get_skypilot_task("sky_down", "sky down train")

    sky_launch >> sky_logs >> sky_status >> sky_down
```

## Running the DAG

1. Copy the DAG file to the Airflow DAGs directory.
   ```bash
   cp sky_k8s_example.py /path/to/airflow/dags                                              
   # If your Airflow is running on Kubernetes, you may use kubectl cp to copy the file to the pod
   # kubectl cp sky_k8s_example.py <airflow-pod-name>:/opt/airflow/dags 
   ```
2. Run `airflow dags list` to confirm that the DAG is loaded.
3. Find the DAG in the Airflow UI (typically http://localhost:8080) and enable it. The UI may take a couple of minutes to reflect the changes.
4. Trigger the DAG from the Airflow UI using the `Trigger DAG` button.
5. Navigate to the run in the Airflow UI to see the DAG progress and logs of each task.

<p align="center">
  <img src="https://i.imgur.com/BVZBaR9.png" width="800">
</p>
<p align="center">
  <img src="https://i.imgur.com/GgqpSiU.png" width="800">
</p>

## Tips

1. **Persistent Volume**: If you have many concurrent tasks, you may want to use a storage class that supports [`ReadWriteMany`](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes) access mode.
2. **Cloud credentials**: If you wish to run tasks on different clouds, you can configure cloud credentials in Kubernetes secrets and mount them in the Sky pod defined in the DAG. See [SkyPilot docs on setting up cloud credentials](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#cloud-account-setup) for more on how to configure credentials in the pod.
3. **Logging**: All SkyPilot logs are written to container stdout, which is captured as task logs in Airflow and displayed in the UI. You can also write logs to a file and read them in subsequent tasks.
4. **XComs for shared state**: Airflow also provides [XComs](https://airflow.apache.org/docs/apache-airflow/stable/concepts/xcoms.html) for cross-task communication. [`sky_k8s_example_xcoms.py`](sky_k8s_example_xcoms.py) demonstrates how to use XComs to share state between tasks. 

## Future work: a native Airflow Executor built on SkyPilot

SkyPilot can in the future provide a native Airflow Executor, that provides an operator similar to the `KubernetesPodOperator` but runs the task as native SkyPilot task.

In such a setup, SkyPilot state management would no longer be required, as the executor will handle SkyPilot cluster launching and termination.