# CLI JSON Output Plan

Make the `sky` CLI agent/machine-friendly by adding `-o json` output support.

## Commands for First Implementation

| Priority | Command | Description |
|----------|---------|-------------|
| 1 | `sky status` | Cluster status |
| 2 | `sky check` | Cloud/infra credential status |
| 3 | `sky show-gpus` | GPU availability & pricing |
| 4 | `sky queue` | Per-cluster job queue |
| 5 | `sky jobs queue` | Managed jobs status |
| 6 | `sky api status` | API request status |
| 7 | `sky api info` | Server info |

## Deferred Commands

| Command | Reason |
|---------|--------|
| `sky cost-report` | Has bugs |
| `sky serve status` | Deferred |
| `sky storage ls` | Deferred |
| `sky launch`, `sky exec`, etc. | Write commands with streaming logs |
| `sky jobs launch`, `sky serve up` | Write commands with streaming logs |

---

## Timestamp Format

Use **ISO 8601 strings** (UTC) instead of raw epoch integers:

```json
"launched_at": "2024-02-04T10:00:00Z"
```

Benefits:
- Human-readable
- Parseable in all languages (`datetime.fromisoformat()` in Python, `new Date()` in JS)
- Unambiguous timezone (UTC with `Z` suffix)

---

## Example Outputs

### 1. `sky status`

**Current (table):**
```
Clusters
NAME         LAUNCHED     RESOURCES                    STATUS   AUTOSTOP  COMMAND
my-cluster   2 hrs ago    1x GCP(n1-standard-4, T4)    UP       1h        sky launch task.yaml
dev-box      5 days ago   1x AWS(m5.xlarge)            STOPPED  -         sky launch --cloud aws
```

**With `-o json`:**
```json
{
  "clusters": [
    {
      "name": "my-cluster",
      "launched_at": "2024-02-04T10:00:00Z",
      "status": "UP",
      "autostop_minutes": 60,
      "to_down": false,
      "cloud": "GCP",
      "region": "us-central1",
      "zone": "us-central1-a",
      "resources": "1x GCP(n1-standard-4, T4)",
      "nodes": 1,
      "cpus": "4",
      "memory": "15GB",
      "accelerators": "T4:1",
      "user": "alice",
      "workspace": "default",
      "command": "sky launch task.yaml"
    }
  ]
}
```

---

### 2. `sky check`

**Current (table):**
```
Checking credentials to enable clouds for SkyPilot.
  AWS: enabled
  GCP: enabled
  Azure: disabled
    Reason: Azure CLI not installed. Run 'pip install azure-cli'.
  Kubernetes: enabled
    Allowed contexts:
    ├── gke_sky-dev_us-central1-c_skypilot-cluster
    └── kind-local

To enable a cloud, follow the hints above and rerun: sky check
Enabled clouds: AWS, GCP, Kubernetes
```

**With `-o json`:**
```json
{
  "clouds": [
    {
      "cloud": "AWS",
      "enabled": true,
      "capabilities": ["compute", "storage"],
      "reason": null
    },
    {
      "cloud": "GCP",
      "enabled": true,
      "capabilities": ["compute", "storage"],
      "reason": null
    },
    {
      "cloud": "Azure",
      "enabled": false,
      "capabilities": [],
      "reason": "Azure CLI not installed. Run 'pip install azure-cli'."
    },
    {
      "cloud": "Kubernetes",
      "enabled": true,
      "capabilities": ["compute"],
      "reason": null,
      "contexts": [
        {
          "name": "gke_sky-dev_us-central1-c_skypilot-cluster",
          "enabled": true,
          "note": "Cluster has 2 nodes with unlabeled accelerators."
        },
        {
          "name": "kind-local",
          "enabled": true,
          "note": null
        }
      ]
    },
    {
      "cloud": "Slurm",
      "enabled": true,
      "capabilities": ["compute"],
      "reason": null,
      "clusters": [
        {
          "name": "hpc-main",
          "enabled": true,
          "partitions": ["gpu", "cpu", "highmem"]
        },
        {
          "name": "hpc-dev",
          "enabled": true,
          "partitions": ["debug"]
        }
      ]
    }
  ],
  "enabled_clouds": ["AWS", "GCP", "Kubernetes", "Slurm"],
  "workspace": "default"
}
```

---

### 3. `sky show-gpus`

**Current (table):**
```
COMMON_GPUS      AVAILABLE_QUANTITIES
A100             1, 2, 4, 8
A100-80GB        1, 2, 4, 8
H100             1, 8
V100             1, 2, 4, 8
T4               1, 2, 4
L4               1, 2, 4, 8
```

**With `-o json`:**
```json
{
  "accelerators": [
    {
      "name": "A100",
      "available_quantities": [1, 2, 4, 8]
    },
    {
      "name": "H100",
      "available_quantities": [1, 8]
    },
    {
      "name": "V100",
      "available_quantities": [1, 2, 4, 8]
    }
  ]
}
```

**`sky show-gpus A100 --all-regions -o json`:**
```json
{
  "accelerator": "A100",
  "offerings": [
    {
      "cloud": "GCP",
      "region": "us-central1",
      "zone": "us-central1-a",
      "instance_type": "a2-highgpu-1g",
      "quantity": 1,
      "price_ondemand": 3.67,
      "price_spot": 1.10,
      "device_memory_gb": 40,
      "host_memory_gb": 85
    },
    {
      "cloud": "AWS",
      "region": "us-east-1",
      "zone": "us-east-1a",
      "instance_type": "p4d.24xlarge",
      "quantity": 8,
      "price_ondemand": 32.77,
      "price_spot": 12.50,
      "device_memory_gb": 40,
      "host_memory_gb": 1152
    }
  ]
}
```

---

### 4. `sky queue <cluster>`

**Current (table):**
```
Fetching job queue for cluster my-cluster...
ID  NAME      SUBMITTED     STATUS    LOG
1   train     10 mins ago   RUNNING   ~/sky_logs/...
2   eval      5 mins ago    PENDING   ~/sky_logs/...
```

**With `-o json`:**
```json
{
  "cluster": "my-cluster",
  "jobs": [
    {
      "job_id": 1,
      "job_name": "train",
      "username": "alice",
      "submitted_at": "2024-02-04T12:30:00Z",
      "started_at": "2024-02-04T12:30:10Z",
      "ended_at": null,
      "status": "RUNNING",
      "resources": "1x T4",
      "log_path": "~/sky_logs/sky-2024-02-04-12-30-00-000001/run.log"
    },
    {
      "job_id": 2,
      "job_name": "eval",
      "username": "alice",
      "submitted_at": "2024-02-04T12:35:00Z",
      "started_at": null,
      "ended_at": null,
      "status": "PENDING",
      "resources": "1x T4",
      "log_path": "~/sky_logs/sky-2024-02-04-12-35-00-000002/run.log"
    }
  ]
}
```

---

### 5. `sky jobs queue`

**Current (table):**
```
Managed Jobs
ID  NAME       RESOURCES      SUBMITTED      DURATION   STATUS     RECOVERIES
1   train-gpt  1x A100:8      2 hrs ago      1h 45m     RUNNING    2
2   finetune   1x V100:4      1 day ago      23h 10m    SUCCEEDED  0
3   eval       1x T4          30 mins ago    -          PENDING    0
```

**With `-o json`:**
```json
{
  "jobs": [
    {
      "job_id": 1,
      "task_id": 0,
      "job_name": "train-gpt",
      "task_name": "train-gpt",
      "status": "RUNNING",
      "schedule_state": "ALIVE",
      "resources": "1x A100:8",
      "cloud": "GCP",
      "region": "us-central1",
      "zone": "us-central1-a",
      "submitted_at": "2024-02-04T10:00:00Z",
      "started_at": "2024-02-04T10:01:40Z",
      "ended_at": null,
      "duration_seconds": 6300,
      "recovery_count": 2,
      "workspace": "default",
      "user": "alice"
    }
  ],
  "status_counts": {
    "PENDING": 0,
    "STARTING": 0,
    "RUNNING": 1,
    "RECOVERING": 0,
    "SUCCEEDED": 5,
    "CANCELLING": 0,
    "CANCELLED": 1,
    "FAILED": 0
  }
}
```

---

### 6. `sky api status`

**Current (table):**
```
ID        USER    NAME         CREATED       STATUS
abc123    alice   launch       2 mins ago    RUNNING
def456    bob     status       5 mins ago    SUCCEEDED
```

**With `-o json`:**
```json
{
  "requests": [
    {
      "request_id": "abc123def456",
      "user": "alice",
      "name": "launch",
      "created_at": "2024-02-04T12:30:00Z",
      "status": "RUNNING",
      "cluster": "my-cluster"
    },
    {
      "request_id": "ghi789jkl012",
      "user": "bob",
      "name": "status",
      "created_at": "2024-02-04T12:25:00Z",
      "status": "SUCCEEDED",
      "cluster": null
    }
  ]
}
```

---

### 7. `sky api info`

**Current (text):**
```
SkyPilot API Server
  Client version: 0.10.0
  Server URL: http://localhost:46580
  Server status: healthy
  Server version: 0.10.0
  User: alice
```

**With `-o json`:**
```json
{
  "client": {
    "version": "0.10.0",
    "commit": "abc1234"
  },
  "server": {
    "url": "http://localhost:46580",
    "status": "healthy",
    "version": "0.10.0",
    "commit": "def5678",
    "api_version": "1"
  },
  "user": "alice",
  "workspace": "default"
}
```

---

## Implementation Notes

### Flag Design

Add `-o/--output` option with choices:
- `table` (default) - current human-readable format
- `json` - machine-readable JSON

### Behavior in JSON Mode

1. **No side-effect output**: Suppress spinners, progress messages, colors
2. **Clean JSON only**: Output valid JSON to stdout
3. **Timestamps**: Convert epoch `int`/`float` to ISO 8601 string (UTC with `Z` suffix)
4. **Exit codes**: Same semantics (0 = success, non-zero = error)
5. **Errors**: Output to stderr as `{"error": "message", "code": N}`

### Files to Modify

1. `sky/client/cli/flags.py` - Add shared `--output` flag
2. `sky/client/cli/command.py` - Modify each command to support JSON output
3. `sky/client/cli/utils.py` or new `sky/client/cli/json_utils.py` - Helper functions for JSON formatting

### Helper Functions Needed

```python
def format_timestamp(epoch: Optional[float]) -> Optional[str]:
    """Convert epoch timestamp to ISO 8601 UTC string."""
    if epoch is None:
        return None
    return datetime.utcfromtimestamp(epoch).strftime('%Y-%m-%dT%H:%M:%SZ')

def output_json(data: dict) -> None:
    """Print JSON to stdout with consistent formatting."""
    print(json.dumps(data, indent=2))
```
