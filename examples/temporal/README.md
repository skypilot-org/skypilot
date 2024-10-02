# Running SkyPilot Tasks in Temporal Workflows

This example demonstrates how to launch SkyPilot tasks and manage them in a Temporal workflow. 

<p align="center">
  <img src="https://i.imgur.com/rxlO2pJ.png" width="800">
</p>

All activities, such as launching clusters, executing tasks, and tearing down clusters, are run on the same worker, eliminating the need for SkyPilot's state management across multiple workers.

## Defining the Tasks

We will define the following tasks to mock a training workflow:
1. **`data_preprocessing.yaml`**: Generates data and writes it to a bucket.
2. **`train.yaml`**: Trains a model using the data in the bucket.
3. **`eval.yaml`**: Evaluates the model and writes the evaluation results to the bucket.

These tasks are defined in the [mock_training_workflow](https://github.com/romilbhardwaj/mock_train_workflow) repository. The repository is cloned during the workflow to execute the tasks.

## Workflow Overview

We define a Temporal workflow consisting of the following steps:

1. **Clone the repository containing tasks** using `git`.
2. **Launch a SkyPilot cluster** to run the data preprocessing job.
3. **Terminate the cluster** after preprocessing.
4. **Launch another cluster** for training the model.
5. **Execute an evaluation task** on the same training cluster.
6. **Terminate the cluster** after evaluation.

### Temporal Activities

These steps are implemented as Temporal activities, which are functions that can be executed by the Temporal worker:

- **`run_sky_launch`**: Launches a SkyPilot cluster with a specified configuration.
- **`run_sky_down`**: Terminates the specified SkyPilot cluster.
- **`run_sky_exec`**: Executes a task on an existing SkyPilot cluster.
- **`run_git_clone`**: Clones a Git repository to a specified location.

### Single Worker Execution

In this workflow, all tasks are handled by the same Temporal worker. This simplifies the workflow, as SkyPilot’s internal state does not need to be transferred between different workers, ensuring seamless orchestration.

This is achieved by registering all activities (`run_sky_launch`, `run_sky_down`, `run_sky_exec`)to the same worker and enqueueing them in the same task queue:

```python
async with Worker(
    client,
    task_queue='skypilot-task-queue',
    workflows=[SkyPilotWorkflow],
    activities=[run_sky_launch, run_sky_down, run_sky_exec, run_git_clone]
):
```

## Running the Workflow

1. If running temporal locally, start the Temporal server:
    ```bash
    temporal server start-dev
    ```

2. Launch the workflow:
    ```bash
    python skypilot_workflow.py
    ```

3. Monitor the workflow execution in the Temporal Web UI (typically http://localhost:8233).

<p align="center">
  <img src="https://i.imgur.com/rxlO2pJ.png" width="800">
</p>


4. When the workflow completes, all logs will be available in the Temporal Web UI.

<p align="center">
  <img src="https://i.imgur.com/h7LALlX.png" width="800">
</p>