# Running SkyPilot tasks in Airflow with the SkyPilot API Server

In this guide, we show how a training workflow involving data preprocessing, training and evaluation can be first easily developed with SkyPilot, and then orchestrated in Airflow.

This example uses a remote SkyPilot API Server to manage shared state across invocations.

<!-- Source: https://docs.google.com/drawings/d/1Di_KIOlxQEUib_RhMKysXBc6u-5WW9FnbougVWRiGF0/edit?usp=sharing -->

<p align="center">
  <img src="https://i.imgur.com/IiPYQTW.png" width="400">
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

* Airflow installed [locally](https://airflow.apache.org/docs/apache-airflow/stable/start.html) (`SequentialExecutor`)
* SkyPilot API server endpoint to send requests to.
  * If you do not have one, refer to the [API server docs](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html) to deploy one.
  * For this specific example: the API server should have AWS/GCS access to create buckets to store intermediate task outputs.

## Configuring the API server endpoint

Once your API server is deployed, you will need to configure Airflow to use it. Set the `SKYPILOT_API_SERVER_ENDPOINT` variable in Airflow - it will be used by the `run_sky_task` function to send requests to the API server:

```bash
airflow variables set SKYPILOT_API_SERVER_ENDPOINT http://<skypilot-api-server-endpoint>
```

You can also use the Airflow web UI to set the variable:

<p align="center">
  <img src="https://i.imgur.com/SQPVxjl.png" width="600">
</p>


## Defining the tasks

We will define the following tasks to mock a training workflow:
1. `data_preprocessing.yaml`: Generates data and writes it to a bucket.
2. `train.yaml`: Trains a model on the data in the bucket.
3. `eval.yaml`: Evaluates the model and writes evaluation results to the bucket.

We have defined these tasks in this directory and uploaded them to a [Git repository](https://github.com/skypilot-org/mock-train-workflow/).

When developing the workflow, you can run the tasks independently using `sky launch`:

```bash
# Run the data preprocessing task, replacing <bucket-name> with the bucket you created above
sky launch -c data --env DATA_BUCKET_NAME=<bucket-name> --env DATA_BUCKET_STORE_TYPE=s3 data_preprocessing.yaml
```

The train and eval step can be run in a similar way:

```bash
# Run the train task
sky launch -c train --env DATA_BUCKET_NAME=<bucket-name> --env DATA_BUCKET_STORE_TYPE=s3 train.yaml
```

Hint: You can use `ssh` and VSCode to [interactively develop](https://skypilot.readthedocs.io/en/latest/examples/interactive-development.html) and debug the tasks.

Note: `eval` can be optionally run on the same cluster as `train` with `sky exec`.

## Writing the Airflow DAG

Once we have developed the tasks, we can seamlessly run them in Airflow.

1. **No changes required to our tasks -** we use the same YAMLs we wrote in the previous step to create an Airflow DAG in `sky_train_dag.py`.
2. **Airflow native logging** - SkyPilot logs are written to container stdout, which is captured as task logs in Airflow and displayed in the UI.
3. **Easy debugging** - If a task fails, you can independently run the task using `sky launch` to debug the issue. SkyPilot will recreate the environment in which the task failed.

Here's a snippet of the DAG declaration in [sky_train_dag.py](https://github.com/skypilot-org/skypilot/blob/master/examples/airflow/sky_train_dag.py):
```python
with DAG(dag_id='sky_train_dag', default_args=default_args,
         catchup=False) as dag:
    # Path to SkyPilot YAMLs. Can be a git repo or local directory.
    base_path = 'https://github.com/skypilot-org/mock-train-workflow.git'

    # Generate bucket UUID as first task
    bucket_uuid = generate_bucket_uuid()

    # Use the bucket_uuid from previous task
    common_envs = {
        'DATA_BUCKET_NAME': f"sky-data-demo-{bucket_uuid}",
        'DATA_BUCKET_STORE_TYPE': 's3',
    }

    skypilot_api_server_endpoint = "{{ var.value.SKYPILOT_API_SERVER_ENDPOINT }}"
    preprocess_task = run_sky_task.override(task_id="data_preprocess")(
        base_path,
        'data_preprocessing.yaml',
        skypilot_api_server_endpoint,
        envs_override=common_envs)
    train_task = run_sky_task.override(task_id="train")(
        base_path,
        'train.yaml',
        skypilot_api_server_endpoint,
        envs_override=common_envs)
    eval_task = run_sky_task.override(task_id="eval")(
        base_path,
        'eval.yaml',
        skypilot_api_server_endpoint,
        envs_override=common_envs)

    # Define the workflow
    bucket_uuid >> preprocess_task >> train_task >> eval_task
```

Behind the scenes, the `run_sky_task` uses the Airflow native [PythonVirtualenvOperator](https://airflow.apache.org/docs/apache-airflow-providers-standard/stable/operators/python.html#pythonvirtualenvoperator) (@task.virtualenv), which creates a Python virtual environment with `skypilot` installed. We need to run the task in a virtual environment as there's a dependency conflict between the latest `skypilot` and `airflow` Python package.

```python
@task.virtualenv(
    python_version='3.11',
    requirements=['skypilot-nightly[gcp,aws,kubernetes]'],
    system_site_packages=False,
    templates_dict={
        'SKYPILOT_API_SERVER_ENDPOINT': "{{ var.value.SKYPILOT_API_SERVER_ENDPOINT }}",
    })
def run_sky_task(...):
    ...
```

The task YAML files can be sourced in two ways:

1. **From a Git repository** (as shown above):
   ```python
   repo_url = 'https://github.com/skypilot-org/mock-train-workflow.git'
   run_sky_task(...)(repo_url, 'path/to/yaml', git_branch='optional_branch')
   ```
   The task will automatically clone the repository and checkout the specified branch before execution.

2. **From a local path**:
   ```python
   local_path = '/path/to/local/directory'
   run_sky_task(...)(local_path, 'path/to/yaml')
   ```
   This is useful during development or when your tasks are stored locally.

All clusters are set to auto-down after the task is done, so no dangling clusters are left behind.

## Running the DAG

1. Copy the DAG file to the Airflow DAGs directory.
   ```bash
   cp sky_train_dag.py /path/to/airflow/dags
   # If your Airflow is running on Kubernetes, you may use kubectl cp to copy the file to the pod
   # kubectl cp sky_train_dag.py <airflow-pod-name>:/opt/airflow/dags
   ```
2. Run `airflow dags list` to confirm that the DAG is loaded.
3. Find the DAG in the Airflow UI (typically http://localhost:8080) and enable it. The UI may take a couple of minutes to reflect the changes. Force unpause the DAG if it is paused with `airflow dags unpause sky_train_dag`
4. Trigger the DAG from the Airflow UI using the `Trigger DAG` button.
5. Navigate to the run in the Airflow UI to see the DAG progress and logs of each task.

If a task fails, SkyPilot will automatically tear down the SkyPilot cluster.

<p align="center">
  <img src="https://i.imgur.com/HH4y77d.png" width="800">
</p>
<p align="center">
  <img src="https://i.imgur.com/EOR4lzV.png" width="800">
</p>

## Optional: Configure cloud accounts

By default, the SkyPilot task will run using the same cloud credentials the SkyPilot
API Server has. This may not be ideal if we have
an existing service account for our task.

In this example, we'll use a GCP service account to demonstrate how we can use custom credentials.
Refer to [GCP service account](https://docs.skypilot.co/en/latest/cloud-setup/cloud-permissions/gcp.html#gcp-service-account)
guide on how to set up a service account.

Once you have the JSON key for our service account, create an Airflow
connection to store the credentials.

```bash
airflow connections add \
  --conn-type google_cloud_platform \
  --conn-extra "{\"keyfile_dict\": \"<YOUR_SERVICE_ACCOUNT_JSON_KEY>\"}" \
  skypilot_gcp_task
```

You can also use the Airflow web UI to add the connection:

<p align="center">
  <img src="https://i.imgur.com/uFOs3G0.png" width="600">
</p>

Next, we will define `data_preprocessing_gcp_sa.yaml`, which contains small modifications to `data_preprocessing.yaml` that will use our GCP service account. The key changes needed here are to mount the GCP service account JSON key to our SkyPilot cluster, and to activate it using `gcloud auth activate-service-account`.

We will also need a new task to read the GCP service account JSON key from our Airflow connection, and then change the preprocess task in our DAG to refer to this new YAML file.

```python
with DAG(dag_id='sky_train_dag', default_args=default_args,
         catchup=False) as dag:
    ...

    # Get GCP credentials
    gcp_service_account_json = get_gcp_service_account_json()

    ...

    preprocess_task = run_sky_task.override(task_id="data_preprocess")(
        base_path,
        'data_preprocessing_gcp_sa.yaml',
        gcp_service_account_json=gcp_service_account_json,
        envs_override=common_envs)

    ...

    bucket_uuid >> gcp_service_account_json >> preprocess_task >> train_task >> eval_task
```

## Future work: a native Airflow Executor built on SkyPilot

Currently this example relies on a helper `run_sky_task` method to wrap SkyPilot invocation in @task, but in the future SkyPilot can provide a native Airflow Executor.

In such a setup, SkyPilot state management also not be required, as the executor will handle SkyPilot cluster launching and termination.
