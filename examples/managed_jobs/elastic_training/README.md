# Elastic Training with Dynamic Workers

This example uses a SkyPilot **job group** with two jobs:
- **controller** (1 node): discovers workers, pushes training state, collects results
- **workers** (DYNAMIC_NODE_SET, 8 nodes, min 4): receive state, train, report back

Workers can be killed and replaced dynamically — the controller detects failures
via `/nodes` and redistributes work.

## Files

| File | Description |
|------|-------------|
| **controller.py** | Discovers workers, sends training state, collects results |
| **worker.py** | Signals readiness, receives state, runs training |
| **job.yaml** | SkyPilot job definition (scripts inlined for V1 fast path) |

## How it works

### 1. Discovery: `/nodes` endpoint

Every pod runs a discovery server on `localhost:9876`. The controller queries
it to find all nodes and their status:

```python
data = json.loads(urllib.request.urlopen('http://localhost:9876/nodes').read())
# Single task: {"nodes": [{"rank": 0, ...}, {"rank": 1, ...}, ...]}
# Job group:   {"controller": [...], "workers": [{"rank": 0, ...}, ...]}
```

### 2. App status: `$SKYPILOT_APP_STATUS`

Workers write their status to this file. SkyPilot syncs it to a pod label,
making it visible to the controller via `/nodes`:

```python
# Worker signals it's ready for work
with open(os.environ['SKYPILOT_APP_STATUS'], 'w') as f:
    f.write('ready')

# Controller finds ready workers
ready = [n for n in data['nodes']
         if n['rank'] != 0 and n.get('app_status') == 'ready']
```

### 3. Controller pushes training state

Each epoch, the controller sends each worker the state it needs:

```python
state = {
    'epoch': 1,
    'partition': 0,          # which data shard to train on
    'total_partitions': 7,   # total workers this epoch
    'peers': ['10.0.1.2', '10.0.1.3', ...],  # for all-reduce
}
# Worker receives state, trains, returns result
result = send_state(worker, state)
# {"rank": 1, "loss": 0.95, "samples": 142}
```

Workers train independently and return results. If a worker dies mid-epoch,
the controller catches the error, refreshes the worker list, and continues.

## Usage

```bash
# Add to ~/.sky/config.yaml: jobs: {use_v1: true}
sky api start --deploy
sky jobs launch -n elastic-train --infra kubernetes job.yaml
```

## Test recovery

While training runs, kill some workers:

```bash
kubectl delete pod elastic-train-<job_id>-{3..5} --force --grace-period=0
```

The controller detects the failure, gets updated worker list from `/nodes`,
and sends state to remaining workers. SkyPilot replaces the killed pods
automatically — they rejoin in subsequent epochs.
