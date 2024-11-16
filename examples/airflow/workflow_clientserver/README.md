# Running SkyPilot tasks in Airflow with the SkyPilot API Server

In this guide, we show how a training workflow involving data preprocessing, training and evaluation can be first easily developed with SkyPilot, and then orchestrated in Airflow.

This example uses a remote SkyPilot API Server to manage shared state across invocations, and includes a failure callback to tear down the SkyPilot cluster on task failure.

<p align="center">
  <img src="https://i.imgur.com/Z1iEikn.png" width="800">
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
  * If you do not have one, deploy with TODO: Add instructions.
  * This API server should have AWS/GCS access to create buckets to store intermediate task outputs.

## Defining the tasks

We will define the following tasks to mock a training workflow:
1. `data_preprocessing.yaml`: Generates data and writes it to a bucket.
2. `train.yaml`: Trains a model on the data in the bucket.
3. `eval.yaml`: Evaluates the model and writes evaluation results to the bucket.

We have defined these tasks in this directory.

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

Note: `eval` can be optionally run on the same cluster as `train` with `sky exec`. Refer to the `shared_state` airflow example on how to do this.

## Writing the Airflow DAG

Once we have developed the tasks, we can seamlessly run them in Airflow.

1. **No changes required to our tasks -** we use the same YAMLs we wrote in the previous step to create an Airflow DAG in `sky_k8s_train_pipeline.py`.
2. **Airflow native logging** - SkyPilot logs are written to container stdout, which is captured as task logs in Airflow and displayed in the UI.
3. **Easy debugging** - If a task fails, you can independently run the task using `sky launch` to debug the issue. SkyPilot will recreate the environment in which the task failed. 

Here's a snippet of the DAG declaration in `sky_k8s_train_pipeline.py`:
```python
with DAG(dag_id='sky_k8s_train_pipeline',
         default_args=default_args,
         schedule_interval=None,
         catchup=False) as dag:
    bucket_uuid = str(uuid.uuid4())[:4]
    bucket_name = f"sky-data-demo-{bucket_uuid}"
    bucket_store_type = "s3"

    preprocess = run_sky_task.override(task_id="data_preprocess")("data_preprocessing", bucket_name, bucket_store_type)
    train_task = run_sky_task.override(task_id="train")("train", bucket_name, bucket_store_type)
    eval_task = run_sky_task.override(task_id="eval")("eval", bucket_name, bucket_store_type)
    
    # Define the workflow
    preprocess >> train_task >> eval_task
```

Behind the scenes, the `run_sky_task` uses the Airflow native Python operator to invoke the sky python API. All clusters are set to auto-down after the task is done, so no dangling clusters are left behind.

## Running the DAG

1. Copy the DAG file to the Airflow DAGs directory.
   ```bash
   cp sky_k8s_train_pipeline.py /path/to/airflow/dags                                              
   # If your Airflow is running on Kubernetes, you may use kubectl cp to copy the file to the pod
   # kubectl cp sky_k8s_example.py <airflow-pod-name>:/opt/airflow/dags 
   ```
2. Run `airflow dags list` to confirm that the DAG is loaded. 
3. Find the DAG in the Airflow UI (typically http://localhost:8080) and enable it. The UI may take a couple of minutes to reflect the changes. Force unpause the DAG if it is paused with `airflow dags unpause sky_k8s_train_pipeline`
4. Trigger the DAG from the Airflow UI using the `Trigger DAG` button.
5. Navigate to the run in the Airflow UI to see the DAG progress and logs of each task.

<p align="center">
  <img src="https://i.imgur.com/Z1iEikn.png" width="800">
</p>
<p align="center">
  <img src="https://i.imgur.com/D89N5xt.png" width="800">
</p>

## Future work: a native Airflow Executor built on SkyPilot

Currently this example relies on a helper `run_sky_task` method to wrap SkyPilot invocation in @task, but in the future SkyPilot can provide a native Airflow Executor.

In such a setup, SkyPilot state management also not be required, as the executor will handle SkyPilot cluster launching and termination.