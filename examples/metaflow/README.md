# Metaflow + SkyPilot

[Metaflow](https://metaflow.org/) is an open-source framework that makes it quick and easy to build and manage real-life ML, AI, and data science projects. With the [`metaflow-skypilot`](https://github.com/outerbounds/metaflow-skypilot) extension, any step in a Metaflow flow can be offloaded to cloud resources provisioned by SkyPilot — across AWS, GCP, Azure, and more — by adding a single decorator.

## Quickstart

```bash
pip install metaflow metaflow-skypilot
sky check  # verify cloud credentials for your provider(s)
```

## Examples

### [`basic_flow.py`](basic_flow.py) — Ephemeral cloud step with a visual card

Offload a compute step to a fresh cluster. Combines `@skypilot` with `@pypi` for remote dependencies and `@card` to capture visual output. The cluster is torn down automatically when the step finishes.

```bash
python basic_flow.py --environment=pypi run
```

```python
@skypilot(cpus='2+')
@pypi(python='3.9.13', packages={'pyfracgen': '0.0.11', 'matplotlib': '3.8.0'})
@card(type='blank')
@step
def render(self):
    import pyfracgen as pf
    ...
    current.card.append(Image.from_matplotlib(plt.gcf()))
    self.next(self.end)
```

### [`persistent_cluster_flow.py`](persistent_cluster_flow.py) — Iterative development on a warm cluster

Pass a `cluster_name` to reuse the same cluster across runs. The first run provisions it; subsequent runs skip provisioning and start immediately. Supports Metaflow `Parameter`s for easy hyperparameter sweeps from the command line.

```bash
python persistent_cluster_flow.py --environment=pypi run --max_depth 10
```

```python
max_depth = Parameter('max_depth', default=5, type=int)

@skypilot(cpus='4+', cluster_name='my-dev-cluster')
@pypi(python='3.9.13', packages={'scikit-learn': '1.4.0', 'numpy': '1.26.0'})
@step
def train(self):
    from sklearn.ensemble import RandomForestClassifier
    ...
    self.accuracy = clf.score(X_test, y_test)
    self.next(self.end)
```

## How It Works

1. Your flow runs locally as normal. When Metaflow reaches a `@skypilot` step, it packages the step's code and uploads it to your Metaflow datastore (e.g. S3).
2. SkyPilot provisions the cheapest cluster matching your resource requirements — across any cloud or service you have credentials for.
3. The step runs remotely. Results flow back into the next local step automatically.

Each step runs in an isolated working directory (`~/metaflow/assets/<job_name>/`), so parallel runs and cluster reuse never collide.

## Resource Options

`@skypilot` accepts all parameters supported by [`sky.Resources`](https://docs.skypilot.co/en/latest/reference/api.html#sky.Resources), plus `cluster_name` for persistent cluster reuse. Some common options:

| Parameter | Example | Description |
|---|---|---|
| `cpus` | `'4+'` | Minimum vCPUs (`+` = "or more") |
| `memory` | `'16+'` | Minimum RAM in GB |
| `accelerators` | `'A100:1'`, `'T4:2'` | GPU type and count |
| `infra` | `'aws'`, `'gcp'`, `'azure'` | Pin to a specific cloud |
| `region` | `'us-east-1'` | Pin to a specific region |
| `cluster_name` | `'my-cluster'` | Reuse a named persistent cluster |
| `disk_size` | `200` | Root disk size in GB |

## Supplying Credentials

- **Instance IAM role / cloud identity** — works automatically if the VM has datastore access.
- **`@environment` decorator** — pass credentials as environment variables:

  ```python
  @environment(vars={"AWS_ACCESS_KEY_ID": "...", "AWS_SECRET_ACCESS_KEY": "..."})
  @skypilot(cpus='2+')
  @step
  def my_step(self): ...
  ```

- **`@secrets` decorator** — pull from AWS Secrets Manager, GCP Secret Manager, etc.
