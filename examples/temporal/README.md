# Running SkyPilot Tasks in Temporal Workflows

This example demonstrates how to launch SkyPilot tasks and manage them in a Temporal workflow. 

<p align="center">
  <img src="https://i.imgur.com/rxlO2pJ.png" width="800">
</p>

## Prerequisites

- [SkyPilot](https://docs.skypilot.co/en/latest/getting-started/installation.html) installed on temporal workers
- [Remote SkyPilot API server](https://docs.skypilot.co/en/latest/reference/api-server/api-server.html)

Using the SkyPilot API Server eliminates the need for managing SkyPilot state across workers and allows credentials to be stored securely in the API server.

To use the API server, set the `SKYPILOT_API_SERVER_ENDPOINT` environment variable:

```bash
export SKYPILOT_API_SERVER_ENDPOINT=https://your-api-server-endpoint
```

You can also set this in a `.env` file in the same directory as the scripts.

## Defining the Tasks

We will define the following tasks to mock a training workflow:
1. **`data_preprocessing.yaml`**: Generates data and writes it to a bucket.
2. **`train.yaml`**: Trains a model using the data in the bucket.
3. **`eval.yaml`**: Evaluates the model and writes the evaluation results to the bucket.

These tasks are defined in the [mock-train-workflow](https://github.com/skypilot-org/mock-train-workflow/tree/clientserver_example) repository. The repository is cloned during the workflow to execute the tasks.

## Workflow Overview

We define a Temporal workflow consisting of the following steps:

1. **Clone the repository containing SkyPilot tasks** using `git` and retrieve SkyPilot YAMLs.
2. **Launch a SkyPilot cluster** to run the data preprocessing job.
3. **Terminate the cluster** after preprocessing.
4. **Launch another cluster** for training the model.
5. **Execute an evaluation task** on the same training cluster.
6. **Terminate the cluster** after evaluation.

### Temporal Activities

These steps are implemented as Temporal activities, which are functions that can be executed by the Temporal worker:

- **`run_git_clone`**: Clones a Git repository and retrieves YAML file contents.
- **`run_sky_launch`**: Launches a SkyPilot cluster with a specified configuration.
- **`run_sky_down`**: Terminates the specified SkyPilot cluster.
- **`run_sky_exec`**: Executes a task on an existing SkyPilot cluster.


## Running the Workflow

1. Configure the SkyPilot API server endpoint:
   ```bash
   export SKYPILOT_API_SERVER_ENDPOINT=https://your-api-server-endpoint
   ```
   
   For instructions on deploying an API server, refer to the [API server documentation](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html).

2. This example writes task outputs to a bucket. Configure the bucket name:

   ```bash
   export SKYPILOT_BUCKET_NAME=your-bucket-name
   ```

   Make sure the bucket name is unique. 

3. If running temporal locally, start the Temporal server:
    ```bash
    temporal server start-dev
    ```

4. Start the worker:
    ```bash
    python run_worker.py
    ```

5. Launch the workflow:
    ```bash
    python run_workflow.py
    ```

6. Monitor the workflow execution in the Temporal Web UI (typically http://localhost:8233).

<p align="center">
  <img src="https://i.imgur.com/rxlO2pJ.png" width="800">
</p>


8. When the workflow completes, all logs will be available in the Temporal Web UI.

<p align="center">
  <img src="https://i.imgur.com/h7LALlX.png" width="800">
</p>