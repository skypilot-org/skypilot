# Go Integration with SkyPilot REST API

This example demonstrates how to integrate a Go-based workflow orchestrator with SkyPilot's REST API for managing GPU workloads.

## Overview

SkyPilot provides a REST API that enables external systems to:
- Submit managed jobs
- Poll for job status
- Handle preemption and error states
- Cancel jobs

This is the recommended pattern for integrating with orchestrators written in languages other than Python.

## Prerequisites

1. SkyPilot installed and configured:
   ```bash
   pip install "skypilot[all]"
   sky check
   ```

2. SkyPilot API server running:
   ```bash
   sky api start
   ```

3. Go 1.19 or later

## Running the Example

```bash
cd examples/go-integration

# Build the client
go build -o skypilot-client main.go

# Run the example
./skypilot-client
```

### Example Output

```
Checking API server health...
API server is healthy

Submitting job 'go-example-1769574533'...
Job submitted! Job ID: 15

============================================================
MONITORING JOB STATUS
============================================================
Monitoring job ID 15 for completion...
  Status: PENDING
  Status: STARTING
  Status: STARTING
  Status: STARTING
  Status: STARTING
  Status: STARTING
  Status: STARTING
  Status: STARTING
  Status: STARTING
  Status: RUNNING
  Status: SUCCEEDED

============================================================
JOB COMPLETED
============================================================
Job ID:           15
Job Name:         sky-b0f8-zongheng
Final Status:     SUCCEEDED
Orchestrator Map: succeeded
Duration:         11.4 seconds

✓ Job completed successfully!
```

### Example Output (Failure Case)

When a job fails, the script captures and displays the failure reason:

```
============================================================
JOB COMPLETED
============================================================
Job ID:           14
Job Name:         sky-a5a2-zongheng
Final Status:     FAILED_PRECHECKS
Orchestrator Map: failed
Failure Reason:   [sky.exceptions.ResourcesUnavailableError] Catalog does not contain
                  any instances satisfying the request: 1x <Cloud>(cpus=1).

✗ Job ended with status: FAILED_PRECHECKS
```

## Architecture Pattern

The recommended integration pattern is:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Go Orchestrator│────▶│  SkyPilot API   │────▶│  Cloud Provider │
│  (Your System)  │◀────│  Server         │◀────│  (GCP/AWS/etc)  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │
        │   POST /jobs/launch   │
        │──────────────────────▶│
        │   request_id          │
        │◀──────────────────────│
        │                       │
        │   GET /api/status     │  (poll until SUCCEEDED)
        │──────────────────────▶│
        │                       │
        │   POST /jobs/queue/v2 │  (poll for job status)
        │──────────────────────▶│
        │   job status          │
        │◀──────────────────────│
```

## Key Concepts

### Async Request Pattern

All SkyPilot API operations are async:

1. **Submit** - Returns immediately with a `request_id`
2. **Poll** - Use `/api/status` to check if the request completed
3. **Result** - Once complete, the operation's result is available

### Job Status States

```
PENDING        → Waiting for scheduler slot
SUBMITTED      → Submitted to scheduler
STARTING       → Launching cluster
RUNNING        → Job executing
RECOVERING     → Preempted, auto-recovering (spot instances)
CANCELLING     → Cancellation in progress
SUCCEEDED      → Completed successfully
CANCELLED      → Cancelled by user
FAILED         → User code failed
FAILED_SETUP   → Setup script failed
FAILED_*       → Various failure modes
```

### State Mapping for Orchestrator Integration

The example maps SkyPilot job states to simplified orchestrator states suitable for database storage:

| SkyPilot Status | Orchestrator State | Description |
|-----------------|-------------------|-------------|
| `PENDING` | `queued` | Job is waiting for a scheduler slot |
| `SUBMITTED` | `queued` | Job has been submitted to the scheduler |
| `STARTING` | `queued` | Cluster is being launched for the job |
| `RUNNING` | `running` | Job is actively executing |
| `RECOVERING` | `recovering` | Job was preempted and is auto-recovering |
| `CANCELLING` | `cancelled` | Job cancellation is in progress |
| `SUCCEEDED` | `succeeded` | Job completed successfully |
| `CANCELLED` | `cancelled` | Job was cancelled by user |
| `FAILED` | `failed` | Job failed (non-zero exit code) |
| `FAILED_SETUP` | `failed` | Job failed during setup phase |
| `FAILED_PRECHECKS` | `failed` | Job failed prechecks (e.g., invalid resources) |
| `FAILED_DRIVER` | `failed` | Internal SkyPilot driver error |
| `FAILED_CONTROLLER` | `failed` | Jobs controller encountered an error |
| `FAILED_NO_RESOURCE` | `failed` | No resources available to run the job |

**Key observations:**
- **`RECOVERING` status** indicates spot instance preemption; SkyPilot handles recovery automatically
- **`failure_reason` field** contains error details for `FAILED_*` states
- **`recovery_count`** tracks total preemptions during job lifetime

### Preemption Handling

When using spot instances, SkyPilot automatically handles preemption:

1. Detects preemption via cluster health monitoring
2. Transitions job to `RECOVERING` status
3. Launches new instance and resumes job
4. Tracks `recovery_count` for visibility

Your orchestrator should:
- Treat `RECOVERING` as a running state
- Log preemption events for visibility
- Trust SkyPilot to handle recovery automatically

### Polling Recommendations

- Poll `/jobs/queue/v2` every 10-30 seconds for status updates
- Poll `/api/status` every 2-5 seconds when waiting for request completion
- Use exponential backoff on network errors
- Consider caching job status to reduce API calls

## API Endpoints Reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/health` | GET | Health check |
| `/jobs/launch` | POST | Submit a managed job |
| `/jobs/queue/v2` | POST | Get job status |
| `/jobs/cancel` | POST | Cancel jobs |
| `/jobs/logs` | POST | Stream job logs |
| `/api/status` | GET | Check request status |

## Authentication

For authenticated API servers:

```go
// Basic auth
client := NewClient(apiURL, WithBasicAuth("username", "password"))

// Bearer token
client := NewClient(apiURL, WithBearerToken("your-token"))
```

## Production Considerations

1. **Error Handling**: Implement retry logic with exponential backoff
2. **Connection Pooling**: Reuse HTTP connections for efficiency
3. **Timeouts**: Set appropriate timeouts for your use case
4. **Monitoring**: Track job submission latency and success rates
5. **Logging**: Log request IDs for debugging

## Limitations

The SkyPilot API returns some data as Python pickles (base64-encoded). For Go integration:
- Status strings and timestamps are JSON-native
- Full job records require pickle decoding
- Consider using CLI output parsing as an alternative

## Related Resources

- [SkyPilot Documentation](https://skypilot.readthedocs.io/)
- [Managed Jobs Guide](https://skypilot.readthedocs.io/en/latest/examples/managed-jobs.html)
- [API Server Setup](https://skypilot.readthedocs.io/en/latest/reference/api.html)
