# Running SkyPilot tasks in Airflow


In this guide, we show how a training workflow involving data preprocessing, training and evaluation can be first easily developed with SkyPilot, and then orchestrated in Airflow.

<p align="center">
  <img src="https://i.imgur.com/mcMghXM.png" width="800">
</p>

**💡 Tip:**  SkyPilot also supports defining and running pipelines without Airflow. Check out [Jobs Pipelines](https://skypilot.readthedocs.io/en/latest/examples/managed-jobs.html#job-pipelines) for more information. 

## Why use SkyPilot with Airflow?
In AI workflows, **the transition from development to production is hard**. 

Workflow development happens ad-hoc, with a lot of interaction required 
with the code and data. When moving this to an Airflow DAG in production, managing dependencies, environments and the 
infra requirements of the workflow gets complex. Porting the code to an airflow requires significant time to test and 
validate any changes, often requiring re-writing the code as Airflow operators.

**SkyPilot seamlessly bridges the dev -> production gap**. 

SkyPilot can operate on any of your infra, allowing you to package and run the same code that you ran during development on a
production Airflow cluster. Behind the scenes, SkyPilot handles environment setup, dependency management, and infra orchestration, allowing you to focus on your code.

Here's how you can use SkyPilot to take your dev workflows to production in Airflow:
1. **Define and test your workflow as SkyPilot tasks**.
    - Use `sky launch` and [Sky VSCode integration](https://skypilot.readthedocs.io/en/latest/examples/interactive-development.html#dev-vscode) to run, debug and iterate on your code.
2. **Orchestrate SkyPilot tasks in Airflow** by invoking `sky launch` on their YAMLs as a task in the Airflow DAG.
    - Airflow does the scheduling, logging, and monitoring, while SkyPilot handles the infra setup and task execution.


## Prerequisites

* Airflow installed on a [Kubernetes cluster](https://airflow.apache.org/docs/helm-chart/stable/index.html) or [locally](https://airflow.apache.org/docs/apache-airflow/stable/start.html) (`SequentialExecutor`)
* A Kubernetes cluster to run tasks on. We'll use GKE in this example.
* A Google cloud account with GCS access to store the data for task.
  * Follow [SkyPilot instructions](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html#google-cloud-platform-gcp) to set up Google Cloud credentials.

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

2. We will store intermediate task outputs in a google cloud bucket. Use the following command to create a unique bucket:
   ```bash
   gsutil mb gs://<bucket-name>
   ```
   Take note of the bucket name, as it will be used in the task YAMLs.

3. To provide SkyPilot GCP access, we will create GCP credentials as secrets that will be mounted in SkyPilot's pods. We provide a helper script `create_gcloud_secret.sh` to create the secret:
   ```bash
   ./create_gcloud_secret.sh
   ```
   You can also use other methods, such as GKE workload identity federation, to provide SkyPilot pods access to GCP credentials.

## Defining the tasks

We will define the following tasks to mock a training workflow:
1. `data_preprocessing.yaml`: Generates data and writes it to a bucket.
2. `train.yaml`: Trains a model on the data in the bucket.
3. `eval.yaml`: Evaluates the model and writes evaluation results to the bucket.

We have defined these tasks in the [mock_training_workflow](https://github.com/romilbhardwaj/mock_train_workflow) repository. Clone the repository and follow the instructions in the README to run the tasks.

When developing the workflow, you can run the tasks independently using `sky launch`:

```bash
git clone https://github.com/romilbhardwaj/mock_train_workflow.git
cd mock_train_workflow
# Run the data preprocessing task, replacing <bucket-name> with the bucket you created above
sky launch -c data --env DATA_BUCKET_URL=gs://<bucket-name> data_preprocessing.yaml
```

The train and eval step can be run in a similar way:

```bash
# Run the train task
sky launch -c train --env DATA_BUCKET_URL=gs://<bucket-name> train.yaml
```

Hint: You can use `ssh` and VSCode to [interactively develop](https://skypilot.readthedocs.io/en/latest/examples/interactive-development.html) and debug the tasks.

Note: `eval` can be optionally run on the same cluster as `train` with `sky exec`. Refer to the `shared_state` airflow example on how to do this.

## Writing the Airflow DAG

Once we have developed the tasks, we can seamlessly port them to Airflow.

1. **No changes required to our tasks -** we use the same YAMLs we wrote in the previous step to create an Airflow DAG in `sky_k8s_train_pipeline.py`.
2. **Airflow native logging** - SkyPilot logs are written to container stdout, which is captured as task logs in Airflow and displayed in the UI.
3. **Easy debugging** - If a task fails, you can independently run the task using `sky launch` to debug the issue. SkyPilot will recreate the environment in which the task failed. 

Here's a snippet of the DAG declaration in `sky_k8s_train_pipeline.py`:
```python
with DAG(dag_id='sky_k8s_train_pipeline', ...) as dag:
    # Make sure bucket exists with gsutil mb -l us-central1 gs://<bucket-name>
    bucket_url = "gs://sky-data-demo"

    # Launch data preprocessing task. We use --down to clean up the SkyPilot cluster after the task is done.
    data_preprocess = get_skypilot_task("sky_data_preprocess",
                                        f"sky launch -y -c data --down --cloud kubernetes --env DATA_BUCKET_URL={bucket_url} mock_train_workflow/data_preprocessing.yaml")

    # Task to train the model
    train = get_skypilot_task("sky_train",
                              f"sky launch -y -c train --down --cloud kubernetes --env DATA_BUCKET_URL={bucket_url} mock_train_workflow/train.yaml")

    # Task to evaluate the trained model. This can optionally be run on the same cluster as the training task using `sky exec`
    eval = get_skypilot_task("sky_eval",
                             f"sky launch -y -c eval --down --cloud kubernetes --env DATA_BUCKET_URL={bucket_url} mock_train_workflow/eval.yaml")

    data_preprocess >> train >> eval
```

Behind the scenes, the `get_skypilot_task` uses the `KubernetesPodOperator` to run the `sky` CLI in an ephemeral pod. All clusters are set to auto-down after the task is done, so no dangling clusters are left behind.

## Running the DAG

1. Copy the DAG file to the Airflow DAGs directory.
   ```bash
   cp sky_k8s_train_pipeline.py /path/to/airflow/dags                                              
   # If your Airflow is running on Kubernetes, you may use kubectl cp to copy the file to the pod
   # kubectl cp sky_k8s_example.py <airflow-pod-name>:/opt/airflow/dags 
   ```
2. Run `airflow dags list` to confirm that the DAG is loaded.
3. Find the DAG in the Airflow UI (typically http://localhost:8080) and enable it. The UI may take a couple of minutes to reflect the changes.
4. Trigger the DAG from the Airflow UI using the `Trigger DAG` button.
5. Navigate to the run in the Airflow UI to see the DAG progress and logs of each task.

<p align="center">
  <img src="https://i.imgur.com/mcMghXM.png" width="800">
</p>
<p align="center">
  <img src="https://i.imgur.com/JbgZO8v.png" width="800">
</p>

## Future work: a native Airflow Executor built on SkyPilot

Currently this example relies on a helper `get_skypilot_task` method to wrap SkyPilot invocation in a `KubernetesPodOperator`, but in the future SkyPilot can
provide a native Airflow Executor.

In such a setup, SkyPilot state management also not be required, as the executor will handle SkyPilot cluster launching and termination.