# Running SkyPilot Tasks in Prefect

This guide demonstrates how to integrate SkyPilot with [Prefect](https://www.prefect.io/) to orchestrate ML workflows on cloud infrastructure. SkyPilot handles the infrastructure provisioning and task execution while Prefect manages workflow orchestration, scheduling, and monitoring.

<!-- Source: Architecture diagram -->
<p align="center">
  <img src="https://i.imgur.com/IiPYQTW.png" width="400">
</p>

**💡 Tip:** SkyPilot also supports defining and running pipelines without Prefect. Check out [Jobs Pipelines](https://skypilot.readthedocs.io/en/latest/examples/managed-jobs.html#job-pipelines) for more information.

## Why Use SkyPilot with Prefect?

In AI workflows, **the transition from development to production is hard**. Workflow development happens ad-hoc, with a lot of interaction required with the code and data. When moving this to a Prefect flow in production, managing dependencies, environments, and infrastructure requirements gets complex.

**SkyPilot seamlessly bridges the dev → production gap.**

SkyPilot can operate on any of your infrastructure, allowing you to package and run the same code that you ran during development in a production Prefect deployment. Behind the scenes, SkyPilot handles environment setup, dependency management, and infrastructure orchestration.

### Key Benefits

- **Multi-cloud flexibility**: Run tasks on AWS, GCP, Azure, Kubernetes, or 20+ other clouds
- **Cost optimization**: Automatically find the cheapest available resources across clouds
- **GPU access**: Easy access to GPUs including A100, H100, and other accelerators
- **Spot instances**: Use spot/preemptible instances for cost savings with automatic recovery
- **No code changes**: Use the same SkyPilot YAMLs from development in production

## Prerequisites

- Python 3.9+
- Prefect 2.x or 3.x installed
- SkyPilot installed with cloud credentials configured
- (Optional) SkyPilot API server for multi-user/production deployments

```bash
# Install Prefect
pip install prefect>=2.0

# Install SkyPilot with your preferred cloud support
pip install "skypilot[aws,gcp,kubernetes]"

# Verify cloud credentials
sky check
```

## Quick Start

### 1. Simple Task Execution

The simplest way to run a SkyPilot task from Prefect:

```python
from prefect import flow
from sky_train_flow import run_sky_task

@flow
def my_workflow():
    # Run a single SkyPilot task
    run_sky_task(
        base_path='https://github.com/skypilot-org/mock-train-workflow.git',
        yaml_path='train.yaml',
        envs_override={'EPOCHS': '10'}
    )

if __name__ == '__main__':
    my_workflow()
```

### 2. Multi-Stage Training Pipeline

Run a complete ML pipeline with data preprocessing, training, and evaluation:

```python
from sky_train_flow import sky_train_workflow

# Run the full workflow
sky_train_workflow(
    base_path='https://github.com/skypilot-org/mock-train-workflow.git',
    data_bucket_store_type='s3',
)
```

### 3. GPU-Accelerated Tasks

Run GPU workloads with automatic resource provisioning:

```python
from sky_train_flow import sky_gpu_workflow

# Inline YAML task definition
task_yaml = """
name: fine-tune-llm
resources:
  accelerators: A100:1
setup: |
  pip install transformers torch
run: |
  python train.py --model llama --epochs 10
"""

sky_gpu_workflow(
    task_yaml=task_yaml,
    num_gpus=1,
    gpu_type='A100',
)
```

### 4. Parallel Task Execution

Run multiple tasks in parallel on separate clusters:

```python
from sky_train_flow import sky_parallel_workflow

tasks = [
    {
        'base_path': '/path/to/tasks',
        'yaml_path': 'experiment1.yaml',
        'envs_override': {'LEARNING_RATE': '0.001'},
    },
    {
        'base_path': '/path/to/tasks',
        'yaml_path': 'experiment2.yaml',
        'envs_override': {'LEARNING_RATE': '0.01'},
    },
]

sky_parallel_workflow(tasks=tasks)
```

## Using with SkyPilot API Server

For production deployments, we recommend using the [SkyPilot API Server](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html) for:
- Shared cluster state across multiple users
- Centralized credential management
- Better observability and logging

### Option 1: Environment Variable

```bash
export SKYPILOT_API_SERVER_ENDPOINT=http://your-api-server:46580
python sky_train_flow.py
```

### Option 2: Pass to Workflow

```python
sky_train_workflow(
    base_path='https://github.com/skypilot-org/mock-train-workflow.git',
    api_server_endpoint='http://your-api-server:46580',
)
```

### Option 3: Prefect Variables (Recommended)

Store the endpoint as a Prefect variable for reuse:

```bash
prefect variable set SKYPILOT_API_SERVER_ENDPOINT http://your-api-server:46580
```

Then in your flow:

```python
from prefect import flow
from prefect.variables import Variable

@flow
def my_workflow():
    api_endpoint = Variable.get('SKYPILOT_API_SERVER_ENDPOINT')
    run_sky_task(
        base_path='...',
        yaml_path='train.yaml',
        api_server_endpoint=api_endpoint,
    )
```

## Running Flows

### Local Execution

```bash
# Run the example workflow directly
python sky_train_flow.py

# Or use Prefect CLI
prefect flow-run create --name sky_train_workflow
```

### With Prefect Server/Cloud

1. Start the Prefect server (or use Prefect Cloud):
```bash
prefect server start
```

2. Deploy the flow:
```python
from sky_train_flow import sky_train_workflow

if __name__ == '__main__':
    sky_train_workflow.deploy(
        name='sky-train-deployment',
        work_pool_name='default-agent-pool',
        cron='0 0 * * *',  # Run daily at midnight
    )
```

3. Create a work pool and start a worker:
```bash
prefect work-pool create default-agent-pool
prefect worker start --pool default-agent-pool
```

## Example Workflows

### Hyperparameter Sweep

```python
from prefect import flow
from sky_train_flow import run_sky_task

@flow
def hyperparameter_sweep():
    learning_rates = [0.001, 0.01, 0.1]
    batch_sizes = [32, 64, 128]

    for lr in learning_rates:
        for bs in batch_sizes:
            run_sky_task.submit(
                base_path='/path/to/tasks',
                yaml_path='train.yaml',
                envs_override={
                    'LEARNING_RATE': str(lr),
                    'BATCH_SIZE': str(bs),
                },
                cluster_prefix=f'sweep-lr{lr}-bs{bs}',
            )

hyperparameter_sweep()
```

### Conditional Workflows

```python
from prefect import flow
from sky_train_flow import run_sky_task

@flow
def conditional_workflow(use_gpu: bool = True):
    # Data preprocessing (CPU)
    run_sky_task(
        base_path='/path/to/tasks',
        yaml_path='preprocess.yaml',
    )

    # Training (GPU or CPU based on flag)
    if use_gpu:
        yaml_file = 'train_gpu.yaml'
    else:
        yaml_file = 'train_cpu.yaml'

    run_sky_task(
        base_path='/path/to/tasks',
        yaml_path=yaml_file,
    )
```

### Using Prefect Artifacts

```python
from prefect import flow, task
from prefect.artifacts import create_markdown_artifact
from sky_train_flow import run_sky_task

@flow
def training_with_artifacts():
    cluster = run_sky_task(
        base_path='/path/to/tasks',
        yaml_path='train.yaml',
    )

    # Create an artifact with the results
    create_markdown_artifact(
        key='training-results',
        markdown=f"""
        ## Training Complete

        - **Cluster**: {cluster}
        - **Status**: Success
        - **Model Path**: s3://bucket/model.pt
        """,
    )
```

## Running on Kubernetes

To run SkyPilot tasks on your Kubernetes cluster:

1. Ensure your kubeconfig is set up:
```bash
kubectl config current-context
```

2. Configure SkyPilot for Kubernetes:
```bash
sky check kubernetes
```

3. Create a task YAML that targets Kubernetes:
```yaml
# kubernetes_task.yaml
name: k8s-training
resources:
  cloud: kubernetes
  accelerators: A100:1

setup: |
  pip install torch transformers

run: |
  python train.py
```

4. Use it in your Prefect flow:
```python
run_sky_task(
    base_path='/path/to/tasks',
    yaml_path='kubernetes_task.yaml',
)
```

See [sky_k8s_example.py](sky_k8s_example.py) for a complete Kubernetes example.

## Configuration Options

### Task Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_path` | str | Required | Path to task YAMLs (local or Git URL) |
| `yaml_path` | str | Required | YAML file path relative to base_path |
| `envs_override` | dict | None | Environment variables to override |
| `git_branch` | str | None | Git branch to checkout (for Git URLs) |
| `cluster_prefix` | str | None | Prefix for cluster name |
| `down_on_completion` | bool | True | Tear down cluster after task |
| `api_server_endpoint` | str | None | SkyPilot API server URL |

### Prefect Task Settings

The `run_sky_task` function is decorated with Prefect retry settings:
- **Retries**: 2 (configurable)
- **Retry delay**: 30 seconds

You can override these when calling:
```python
run_sky_task.with_options(retries=5, retry_delay_seconds=60)(
    base_path='...',
    yaml_path='train.yaml',
)
```

## Best Practices

1. **Use the API Server for Production**: For multi-user environments, deploy the SkyPilot API server for shared state management.

2. **Set Auto-down**: Always use `down_on_completion=True` (default) to avoid leaving clusters running and incurring costs.

3. **Use Spot Instances**: Configure spot instances in your SkyPilot YAMLs for cost savings:
   ```yaml
   resources:
     use_spot: true
   ```

4. **Monitor with Prefect UI**: Use Prefect's observability features to monitor workflow execution and debug failures.

5. **Store Secrets Securely**: Use Prefect Secrets for sensitive data like API keys:
   ```python
   from prefect.blocks.system import Secret
   api_key = Secret.load('my-api-key').get()
   ```

## Troubleshooting

### Task Fails to Launch

1. Check cloud credentials:
   ```bash
   sky check
   ```

2. Verify the SkyPilot API server is running (if using):
   ```bash
   curl http://your-api-server:46580/health
   ```

### Cluster Not Terminated

If a cluster wasn't terminated due to a failure:
```bash
sky down <cluster-name>
# Or terminate all clusters
sky down -a
```

### View Cluster Logs

```bash
sky logs <cluster-name>
```

## Additional Resources

- [SkyPilot Documentation](https://docs.skypilot.co/)
- [Prefect Documentation](https://docs.prefect.io/)
- [SkyPilot API Server Guide](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html)
- [SkyPilot GitHub](https://github.com/skypilot-org/skypilot)

## Related Examples

- [Airflow Integration](../airflow/) - Similar integration for Apache Airflow
- [GitHub Actions](../github_actions/) - CI/CD integration with GitHub Actions
