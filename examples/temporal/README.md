# Running SkyPilot Tasks in Temporal Workflows

This example demonstrates how to launch SkyPilot tasks and manage them in a Temporal workflow. 

<p align="center">
  <img src="https://i.imgur.com/rxlO2pJ.png" width="800">
</p>

## SkyPilot API Server Integration

This example supports using the SkyPilot API Server, which eliminates the need for SkyPilot's state management across multiple workers. When using the API server:

- All SkyPilot state is managed by the API server, allowing activities to be executed on any worker
- Activities communicate with the API server using the SkyPilot Python library instead of CLI commands
- The workflow can easily be distributed across multiple workers

To use the API server, set the `SKYPILOT_API_SERVER_ENDPOINT` environment variable:

```bash
export SKYPILOT_API_SERVER_ENDPOINT=https://your-api-server-endpoint
```

You can also set this in a `.env` file in the same directory as the scripts.

If this environment variable is not set, the example will fall back to using local SkyPilot state, requiring all activities to run on the same worker (as in the previous version).

## Implementation Details

The implementation has several key features:

### 1. Consistent Command Structure

All SkyPilot commands follow a consistent pattern with an optional API server endpoint parameter and environment variables support:

```python
@dataclass
class SkyLaunchCommand:
    """Command to launch a SkyPilot cluster with a task."""
    cluster_name: str
    yaml_content: str
    launch_kwargs: Dict[str, Any] = None
    envs_override: Dict[str, str] = None
    api_server_endpoint: Optional[str] = None
```

This allows each command to be configured with the appropriate API server endpoint and environment variables when needed.

### 2. Proper Environment Setup for Sky Module

The Sky module is imported *after* setting the API server endpoint environment variable:

```python
# Set API server endpoint if provided
if input.api_server_endpoint:
    os.environ["SKYPILOT_API_SERVER_ENDPOINT"] = input.api_server_endpoint

# Import sky after setting environment variables
import sky
```

This ensures the SkyPilot client is properly initialized with the correct endpoint.

### 3. Flexible Environment Variable Management

The workflow uses an `envs_override` dictionary to pass environment variables to SkyPilot tasks at multiple levels:

1. **Workflow Input Level**: The primary environment variables are set in the workflow input
   ```python
   @dataclass
   class SkyPilotWorkflowInput:
       cluster_prefix: str
       repo_url: str
       envs_override: Optional[Dict[str, str]] = None
       branch: Optional[str] = None
       api_server_endpoint: Optional[str] = None
   ```

   The `branch` parameter allows you to specify which branch of the repository to clone.

2. **Command Level**: Each command can also have its own environment variables
   ```python
   SkyLaunchCommand(
       cluster_name=cluster_name,
       yaml_content=yaml_content,
       envs_override=input.envs_override,  # Pass down from workflow
       api_server_endpoint=api_server_endpoint
   )
   ```

3. **Runtime Level**: Environment variables can be set when running the workflow
   ```python
   # In run_workflow.py
   envs_override = {}
   if data_bucket_url:
       envs_override["DATA_BUCKET_URL"] = data_bucket_url
   else:
       envs_override["DATA_BUCKET_URL"] = ""  # Set empty string as default
   ```

This multi-level approach provides maximum flexibility for configuring environment variables for SkyPilot tasks.

### 4. Direct Kwargs Passing to SkyPilot Functions

Configuration options are passed directly to Sky functions:

```python
# Prepare launch kwargs
launch_kwargs = {}
if input.envs_override:
    launch_kwargs["envs"] = input.envs_override.copy()

# Launch the task, passing kwargs directly to sky.launch
launch_request_id = sky.launch(task, cluster_name=input.cluster_name, **launch_kwargs)
```

This allows for more flexibility and avoids unnecessary YAML modifications.

### 5. YAML Content Sharing Between Activities

The git clone activity now returns the actual YAML file contents, which are then passed directly to the launch and exec activities:

```python
@dataclass
class GitCloneOutput:
    success: bool
    message: str
    yaml_contents: Dict[str, str]
```

This eliminates the need to reopen files across different workers and ensures all necessary YAML content is available throughout the workflow.

## Defining the Tasks

We will define the following tasks to mock a training workflow:
1. **`data_preprocessing.yaml`**: Generates data and writes it to a bucket.
2. **`train.yaml`**: Trains a model using the data in the bucket.
3. **`eval.yaml`**: Evaluates the model and writes the evaluation results to the bucket.

These tasks are defined in the [mock_training_workflow](https://github.com/romilbhardwaj/mock_train_workflow) repository. The repository is cloned during the workflow to execute the tasks.

## Workflow Overview

We define a Temporal workflow consisting of the following steps:

1. **Clone the repository containing tasks** using `git` and retrieve YAML content.
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

### API Server vs. Single Worker Execution

#### With API Server (Recommended)

When using the SkyPilot API Server, activities can be executed by any worker. The API server maintains the state of all SkyPilot clusters, allowing seamless execution across multiple workers.

#### Without API Server (Legacy)

Without the API Server, all SkyPilot activities (`run_sky_launch`, `run_sky_down`, `run_sky_exec`) must be handled by the same Temporal worker to ensure SkyPilot's internal state is maintained across activities.

This is achieved by registering all activities to the same worker and enqueueing them in the same task queue:

```python
async with Worker(
    client,
    task_queue='skypilot-task-queue',
    workflows=[SkyPilotWorkflow],
    activities=[run_sky_launch, run_sky_down, run_sky_exec, run_git_clone]
):
```

## Running the Workflow

1. (Optional) Set up the SkyPilot API Server and configure the endpoint:
   ```bash
   export SKYPILOT_API_SERVER_ENDPOINT=https://your-api-server-endpoint
   ```
   
   For instructions on deploying an API server, refer to the [API server documentation](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html).

2. (Optional) Configure a data bucket URL for tasks:
   ```bash
   export SKYPILOT_BUCKET_URL=s3://your-bucket-name
   ```
   
   If not set, an empty string will be used. Your task YAML files should handle empty DATA_BUCKET_URL values appropriately.

3. The example uses the `clientserver_example` branch from the repository by default. You can modify this in `run_workflow.py` if needed:
   ```python
   SkyPilotWorkflowInput(
       # ... other parameters ...
       branch="clientserver_example",  # Change branch name here
   )
   ```

4. If running temporal locally, start the Temporal server:
    ```bash
    temporal server start-dev
    ```

5. Start the worker:
    ```bash
    python run_worker.py
    ```

6. Launch the workflow:
    ```bash
    python run_workflow.py
    ```

7. Monitor the workflow execution in the Temporal Web UI (typically http://localhost:8233).

<p align="center">
  <img src="https://i.imgur.com/rxlO2pJ.png" width="800">
</p>


8. When the workflow completes, all logs will be available in the Temporal Web UI.

<p align="center">
  <img src="https://i.imgur.com/h7LALlX.png" width="800">
</p>