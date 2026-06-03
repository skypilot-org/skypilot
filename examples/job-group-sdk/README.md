# Job Groups via Python SDK

This example shows how to create and launch [Job Groups](https://docs.skypilot.co/en/latest/examples/job-groups.html) using the SkyPilot Python SDK instead of YAML files.

## Prerequisites

- SkyPilot installed: https://docs.skypilot.co/en/latest/getting-started/installation.html
- A running Kubernetes cluster configured for SkyPilot (service discovery requires Kubernetes)

## Files

| File | Description |
|------|-------------|
| `job_group_sdk.py` | Builds and launches a server-client Job Group in Python |
| `job_group_primary_aux_sdk.py` | Demonstrates primary/auxiliary task lifecycle |
| `job_group.yaml` | Equivalent YAML for reference |

## Usage

### Server-client networking

This example launches two parallel tasks: a server running an HTTP server, and a client that connects to it using Job Group service discovery.

**With the Python SDK:**

```bash
python examples/job-group-sdk/job_group_sdk.py
```

**With the equivalent YAML (for comparison):**

```bash
sky jobs launch examples/job-group-sdk/job_group.yaml
```

Both approaches produce the same Job Group.

### Primary/auxiliary tasks

This example shows how to designate a primary task (trainer) and an auxiliary task (data-server). When the trainer completes, the data-server is automatically terminated after a grace period.

```bash
python examples/job-group-sdk/job_group_primary_aux_sdk.py
```

## Example output

After launching both scripts, `sky status` shows the Job Groups and their tasks:

```console
$ sky status
Managed jobs
In progress tasks: 2 PENDING, 2 STARTING
ID   TASK  NAME                 REQUESTED  SUBMITTED    TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS              POOL
183        train-with-services  -          -            -              -             0            PENDING (task: 0)   -
 ↳   0     trainer [P]          1x[CPU:2]  -            -              -             0            PENDING             -
 ↳   1     data-server          1x[CPU:2]  -            -              -             0            PENDING             -

182        server-client        -          29 secs ago  29s            -             0            STARTING (task: 0)  -
 ↳   0     server               1x[CPU:2]  29 secs ago  29s            -             0            STARTING            -
 ↳   1     client               1x[CPU:2]  29 secs ago  29s            -             0            STARTING            -
```

### Server-client networking

```console
$ sky jobs logs 182
Hint: This job has 2 tasks. Use 'sky jobs logs 182 TASK' to view logs for a specific task (TASK can be task ID or name).
=== Task server(0) ===
├── Waiting for task resources on 1 node.
└── Job started. Streaming logs... (Ctrl-C to exit log streaming; job will not be killed)
(server, pid=1365) [SkyPilot] Waiting for network setup...
(server, pid=1365) [SkyPilot] Hostname client-0.server-client is now resolvable
(server, pid=1365) [SkyPilot] Network is ready!
(server, pid=1365) Server starting on port 8080
(server, pid=1365) Serving HTTP on 0.0.0.0 port 8080 (http://0.0.0.0:8080/) ...
(server, pid=1365) 10.35.8.177 - - [26/Feb/2026 03:04:58] "GET / HTTP/1.1" 200 -
(server, pid=1365) Server done
✓ Task server(0) finished (status: SUCCEEDED).
=== Task client(1) ===
├── Waiting for task resources on 1 node.
└── Job started. Streaming logs... (Ctrl-C to exit log streaming; job will not be killed)
(client, pid=1381) [SkyPilot] Waiting for network setup...
(client, pid=1381) [SkyPilot] Hostname server-0.server-client is now resolvable
(client, pid=1381) [SkyPilot] Network is ready!
(client, pid=1381) Client starting
(client, pid=1381) Connecting to server-0.server-client:8080
(client, pid=1381) SUCCESS: Connected to server
✓ Task client(1) finished (status: SUCCEEDED).
✓ Job finished (status: SUCCEEDED).
```

### Primary/auxiliary tasks

```console
$ sky jobs logs 183
Hint: This job has 2 tasks. Use 'sky jobs logs 183 TASK' to view logs for a specific task (TASK can be task ID or name).
=== Task trainer(0) ===
├── Waiting for task resources on 1 node.
└── Job started. Streaming logs... (Ctrl-C to exit log streaming; job will not be killed)
(trainer, pid=1364) [SkyPilot] Waiting for network setup...
(trainer, pid=1364) [SkyPilot] Hostname data-server-0.train-with-services is now resolvable
(trainer, pid=1364) [SkyPilot] Network is ready!
(trainer, pid=1364) Trainer starting
(trainer, pid=1364) Training for 30 seconds...
(trainer, pid=1364) Training complete
✓ Task trainer(0) finished (status: SUCCEEDED).
✓ Job finished (status: CANCELLED).
```

The trainer (primary task) completed successfully, and the auxiliary data-server was automatically cancelled.

## Monitoring

```bash
# Check job status
sky jobs queue

# View all task logs
sky jobs logs <job_id>

# View logs for a specific task by name
sky jobs logs <job_id> <task_name>
```

## How it works

A Job Group is a `sky.Dag` with `sky.DagExecution.PARALLEL` execution mode:

```python
import sky

server = sky.Task(name='server', run='python3 -m http.server 8080')
server.set_resources(sky.Resources(cpus=2, infra='kubernetes'))

client = sky.Task(name='client', run='curl http://server-0.${SKYPILOT_JOBGROUP_NAME}:8080/')
client.set_resources(sky.Resources(cpus=2, infra='kubernetes'))

with sky.Dag() as dag:
    dag.add(server)
    dag.add(client)
dag.name = 'my-group'
dag.set_execution(sky.DagExecution.PARALLEL)

sky.jobs.launch(dag)
```

Tasks discover each other via hostnames in the format `{task_name}-{node_index}.{job_group_name}`. The `SKYPILOT_JOBGROUP_NAME` environment variable is injected into all tasks automatically.

You can also load a Job Group from YAML programmatically:

```python
import sky
from sky.utils import dag_utils

dag = dag_utils.load_job_group_from_yaml('job_group.yaml')
sky.jobs.launch(dag)
```
