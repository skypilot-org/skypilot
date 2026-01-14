# Running SkyPilot Tasks in Prefect

This example demonstrates how to run SkyPilot tasks on Kubernetes as part of a Prefect workflow.

## Prerequisites

```bash
# Install Prefect and SkyPilot
pip install prefect "skypilot[kubernetes]"

# Verify Kubernetes access
sky check kubernetes
```

## Usage

```bash
python sky_k8s_example.py
```

This runs a 3-stage ML pipeline on Kubernetes:
1. **Data Preprocessing** (CPU) - Generates synthetic data
2. **Model Training** (GPU) - Trains a model using PyTorch
3. **Model Evaluation** (GPU) - Evaluates model accuracy

## How It Works

The `run_sky_task` Prefect task wraps SkyPilot's SDK:

```python
@task(name='run_sky_task', retries=2, retry_delay_seconds=30)
def run_sky_task(task_config: dict, cluster_prefix: str = 'prefect') -> str:
    import sky
    import uuid

    task_config['resources']['infra'] = 'kubernetes'
    sky_task = sky.Task.from_yaml_config(task_config)

    # Generate a unique cluster name
    task_name = task_config.get('name', 'task')
    cluster_uuid = str(uuid.uuid4())[:4]
    cluster_name = f'{cluster_prefix}-{task_name}-{cluster_uuid}'

    request_id = sky.launch(sky_task, cluster_name=cluster_name, down=True)
    job_id, _ = sky.stream_and_get(request_id)
    sky.tail_logs(cluster_name=cluster_name, job_id=job_id, follow=True)

    return cluster_name
```

Each stage runs on a separate Kubernetes pod managed by SkyPilot.

## Using with SkyPilot API Server

For production, use the [SkyPilot API Server](https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html):

```bash
export SKYPILOT_API_SERVER_ENDPOINT=http://your-api-server:46580
python sky_k8s_example.py
```

## Related

- [Airflow Integration](https://github.com/skypilot-org/skypilot/tree/master/examples/airflow) - Similar integration for Apache Airflow
- [SkyPilot Docs](https://docs.skypilot.co/)
- [Prefect Docs](https://docs.prefect.io/)
