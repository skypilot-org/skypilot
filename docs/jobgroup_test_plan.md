# JobGroups Test Plan

This document outlines the comprehensive test plan for the JobGroups feature in SkyPilot (PR #8456).

## Table of Contents

1. [Test Environment Setup](#test-environment-setup)
2. [Unit Tests](#unit-tests)
3. [Smoke Tests](#smoke-tests)
4. [Integration Tests](#integration-tests)
5. [Manual Testing Checklist](#manual-testing-checklist)
6. [Test YAML Examples](#test-yaml-examples)

---

## Test Environment Setup

### Prerequisites

```bash
# Clone the repository and checkout the PR branch
git fetch origin pull/8456/head:jobgroup-pr
git checkout jobgroup-pr

# Install development dependencies
pip install -e ".[dev]"

# Verify SkyPilot installation
sky --version

# Start the SkyPilot API server (if using server mode)
sky api start
```

### Environment Variables for Testing

```bash
# Use low-resource controller for faster testing
export SKYPILOT_CONFIG=tests/test_yamls/low_resource_sky_config.yaml

# Optional: Set specific cloud for testing
export SKYPILOT_TEST_CLOUD=aws  # or gcp, kubernetes, lambda
```

---

## Unit Tests

### 1. JobGroup YAML Parsing Tests

**File:** `tests/unit_tests/test_sky/jobs/test_job_group_yaml.py`

```bash
# Run all JobGroup YAML parsing tests
pytest tests/unit_tests/test_sky/jobs/test_job_group_yaml.py -v

# Run specific test
pytest tests/unit_tests/test_sky/jobs/test_job_group_yaml.py::TestJobGroupYamlParsing -v
```

**Test Cases:**

| Test ID | Test Case | Expected Result |
|---------|-----------|-----------------|
| UNIT-YAML-001 | Parse valid multi-document YAML | JobGroup object created with correct jobs |
| UNIT-YAML-002 | Parse JobGroup with `placement: SAME_INFRA` | Placement field correctly parsed |
| UNIT-YAML-003 | Parse JobGroup with `execution: parallel` | Execution mode correctly parsed |
| UNIT-YAML-004 | Parse JobGroup with heterogeneous resources | Each job has different resource specs |
| UNIT-YAML-005 | Reject invalid placement value | Raise validation error |
| UNIT-YAML-006 | Reject missing job name in JobGroup | Raise validation error |
| UNIT-YAML-007 | Parse JobGroup with `num_nodes` per job | Multi-node jobs correctly configured |
| UNIT-YAML-008 | Detect JobGroup vs Pipeline YAML | `is_job_group_yaml()` returns correct boolean |

### 2. JobGroup State Management Tests

**File:** `tests/unit_tests/test_sky/jobs/test_job_group_state.py`

```bash
# Run all JobGroup state tests
pytest tests/unit_tests/test_sky/jobs/test_job_group_state.py -v
```

**Test Cases:**

| Test ID | Test Case | Expected Result |
|---------|-----------|-----------------|
| UNIT-STATE-001 | Insert JobGroup into database | JobGroup metadata columns populated |
| UNIT-STATE-002 | Query jobs by JobGroup ID | All jobs in group returned |
| UNIT-STATE-003 | Update JobGroup status | Status propagates correctly |
| UNIT-STATE-004 | Cancel all jobs in JobGroup | All jobs marked as CANCELLED |
| UNIT-STATE-005 | JobGroup with failed job | Correct failure handling |
| UNIT-STATE-006 | Database migration for JobGroup columns | Migration runs successfully |

### 3. JobGroup Networking Tests

**File:** `tests/unit_tests/test_sky/jobs/test_job_group_networking.py`

```bash
# Run all networking tests
pytest tests/unit_tests/test_sky/jobs/test_job_group_networking.py -v
```

**Test Cases:**

| Test ID | Test Case | Expected Result |
|---------|-----------|-----------------|
| UNIT-NET-001 | Generate /etc/hosts entries | Correct hostname-to-IP mappings |
| UNIT-NET-002 | Generate hostnames for multi-node jobs | `{job_name}-{node_index}` format correct |
| UNIT-NET-003 | Set `SKYPILOT_JOBGROUP_NAME` env var | Environment variable correctly injected |
| UNIT-NET-004 | Handle special characters in job names | Hostnames sanitized correctly |

### 4. DAG Utils Tests

**File:** `tests/unit_tests/test_sky/utils/test_dag_utils_job_group.py`

```bash
pytest tests/unit_tests/test_sky/utils/test_dag_utils_job_group.py -v
```

**Test Cases:**

| Test ID | Test Case | Expected Result |
|---------|-----------|-----------------|
| UNIT-DAG-001 | `load_job_group_from_yaml()` loads correctly | DAG with multiple tasks created |
| UNIT-DAG-002 | `dump_dag_to_yaml_str()` serializes JobGroup | Valid multi-document YAML output |
| UNIT-DAG-003 | DAG `is_job_group()` method | Returns True for JobGroups |
| UNIT-DAG-004 | DAG `set_job_group()` method | Sets job group properties |

---

## Smoke Tests

### 1. Basic JobGroup Launch/Cancel Tests

**File:** `tests/smoke_tests/test_job_group.py`

```bash
# Run all JobGroup smoke tests
pytest tests/smoke_tests/test_job_group.py -v --managed-jobs

# Run on specific cloud
pytest tests/smoke_tests/test_job_group.py -v --generic-cloud aws

# Run on Kubernetes
pytest tests/smoke_tests/test_job_group.py -v --generic-cloud kubernetes
```

**Test Commands for Manual Testing:**

```bash
# Test 1: Basic JobGroup Launch
# Create a simple 2-job JobGroup
cat > /tmp/test_jobgroup_basic.yaml << 'EOF'
name: test-jobgroup-basic

placement: SAME_INFRA
execution: parallel

---
name: job-a

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Job A starting"
  echo "SKYPILOT_JOBGROUP_NAME=$SKYPILOT_JOBGROUP_NAME"
  sleep 60
  echo "Job A completed"

---
name: job-b

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Job B starting"
  echo "SKYPILOT_JOBGROUP_NAME=$SKYPILOT_JOBGROUP_NAME"
  sleep 60
  echo "Job B completed"
EOF

# Launch the JobGroup
sky jobs launch /tmp/test_jobgroup_basic.yaml -y -d

# Check status
sky jobs queue

# Check logs for both jobs
sky jobs logs -n job-a --no-follow
sky jobs logs -n job-b --no-follow

# Cancel the JobGroup
sky jobs cancel -y -n test-jobgroup-basic
```

### 2. Heterogeneous Resource Tests

```bash
# Test 2: Heterogeneous Resources (CPU + GPU)
cat > /tmp/test_jobgroup_hetero.yaml << 'EOF'
name: test-jobgroup-hetero

placement: SAME_INFRA
execution: parallel

---
name: gpu-trainer

resources:
  accelerators: T4:1
  cpus: 4+

run: |
  echo "GPU Trainer starting"
  nvidia-smi
  python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"
  sleep 120
  echo "GPU Trainer completed"

---
name: cpu-preprocessor

resources:
  cpus: 8+
  memory: 16+

run: |
  echo "CPU Preprocessor starting"
  echo "Processing data..."
  sleep 120
  echo "CPU Preprocessor completed"
EOF

# Launch with specific cloud
sky jobs launch /tmp/test_jobgroup_hetero.yaml --infra aws -y -d

# Monitor both jobs
watch -n 5 'sky jobs queue'

# Check GPU job logs
sky jobs logs -n gpu-trainer --no-follow
```

### 3. Multi-Node Jobs in JobGroup

```bash
# Test 3: Multi-node jobs within JobGroup
cat > /tmp/test_jobgroup_multinode.yaml << 'EOF'
name: test-jobgroup-multinode

placement: SAME_INFRA
execution: parallel

---
name: trainer

resources:
  cpus: 2+
  memory: 4+

num_nodes: 2

run: |
  echo "Trainer node $SKYPILOT_NODE_RANK starting"
  echo "Total nodes: $SKYPILOT_NUM_NODES"
  echo "JobGroup name: $SKYPILOT_JOBGROUP_NAME"
  # Test hostname resolution
  ping -c 1 trainer-0.${SKYPILOT_JOBGROUP_NAME} || echo "Ping test (may fail in some environments)"
  ping -c 1 trainer-1.${SKYPILOT_JOBGROUP_NAME} || echo "Ping test (may fail in some environments)"
  sleep 120

---
name: data-worker

resources:
  cpus: 2+
  memory: 4+

num_nodes: 3

run: |
  echo "Data worker node $SKYPILOT_NODE_RANK starting"
  echo "Total data worker nodes: $SKYPILOT_NUM_NODES"
  sleep 120
EOF

sky jobs launch /tmp/test_jobgroup_multinode.yaml -y -d

# Check queue - should show 5 nodes total (2 + 3)
sky jobs queue
```

### 4. Networking Tests (Hostname Resolution)

```bash
# Test 4: Inter-job networking
cat > /tmp/test_jobgroup_networking.yaml << 'EOF'
name: test-jobgroup-net

placement: SAME_INFRA
execution: parallel

---
name: server

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Server starting on port 8080"
  # Simple HTTP server
  python -m http.server 8080 &
  SERVER_PID=$!
  sleep 300
  kill $SERVER_PID

---
name: client

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Client starting"
  echo "Waiting for server to be ready..."
  sleep 30

  # Test hostname resolution
  echo "Testing hostname resolution..."
  getent hosts server.${SKYPILOT_JOBGROUP_NAME} || echo "getent failed"

  # Test connectivity
  echo "Testing connectivity to server..."
  curl -v http://server.${SKYPILOT_JOBGROUP_NAME}:8080/ || echo "curl failed (expected if no files)"

  echo "Networking test completed"
  sleep 120
EOF

sky jobs launch /tmp/test_jobgroup_networking.yaml -y -d

# Check client logs for networking results
sky jobs logs -n client --no-follow
```

### 5. Failure Handling Tests

```bash
# Test 5: Job failure handling
cat > /tmp/test_jobgroup_failure.yaml << 'EOF'
name: test-jobgroup-failure

placement: SAME_INFRA
execution: parallel

---
name: healthy-job

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Healthy job running"
  sleep 120

---
name: failing-job

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "This job will fail"
  sleep 10
  exit 1
EOF

sky jobs launch /tmp/test_jobgroup_failure.yaml -y -d

# Wait and check status
sleep 60
sky jobs queue

# Verify failure handling
sky jobs logs -n failing-job --no-follow
```

### 6. Job Recovery Tests

```bash
# Test 6: Job recovery with exit codes
cat > /tmp/test_jobgroup_recovery.yaml << 'EOF'
name: test-jobgroup-recovery

placement: SAME_INFRA
execution: parallel

---
name: recoverable-job

resources:
  cpus: 2+
  memory: 4+
  job_recovery:
    max_restarts_on_errors: 3
    recover_on_exit_codes: [29]

run: |
  echo "Attempt number: checking if should recover"
  if [ ! -f /tmp/recovery_marker ]; then
    echo "First run - will exit with recoverable code"
    touch /tmp/recovery_marker
    exit 29
  fi
  echo "Second run - should succeed"
  sleep 60
EOF

sky jobs launch /tmp/test_jobgroup_recovery.yaml -y -d
```

### 7. Cancellation Tests

```bash
# Test 7: Cancel all jobs in JobGroup
cat > /tmp/test_jobgroup_cancel.yaml << 'EOF'
name: test-jobgroup-cancel

placement: SAME_INFRA
execution: parallel

---
name: long-job-a

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Long job A starting"
  sleep 600

---
name: long-job-b

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Long job B starting"
  sleep 600

---
name: long-job-c

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Long job C starting"
  sleep 600
EOF

sky jobs launch /tmp/test_jobgroup_cancel.yaml -y -d

# Wait for jobs to start
sleep 120

# Verify all are running
sky jobs queue | grep test-jobgroup-cancel

# Cancel all jobs in the group
sky jobs cancel -y -n test-jobgroup-cancel

# Verify all are cancelled
sleep 30
sky jobs queue | grep test-jobgroup-cancel
```

---

## Integration Tests

### 1. Barrier Synchronization Test

```bash
# Test: Verify barrier sync before run commands
cat > /tmp/test_jobgroup_barrier.yaml << 'EOF'
name: test-jobgroup-barrier

placement: SAME_INFRA
execution: parallel

---
name: fast-setup

resources:
  cpus: 2+
  memory: 4+

setup: |
  echo "Fast setup - takes 5 seconds"
  sleep 5
  echo "Fast setup complete"

run: |
  echo "Fast job run started at $(date)"
  echo "BARRIER_TEST: fast-setup started run"
  sleep 120

---
name: slow-setup

resources:
  cpus: 2+
  memory: 4+

setup: |
  echo "Slow setup - takes 60 seconds"
  sleep 60
  echo "Slow setup complete"

run: |
  echo "Slow job run started at $(date)"
  echo "BARRIER_TEST: slow-setup started run"
  sleep 120
EOF

sky jobs launch /tmp/test_jobgroup_barrier.yaml -y -d

# Check logs - both run commands should start around same time
# despite different setup times
sleep 120
sky jobs logs -n fast-setup --no-follow | grep "BARRIER_TEST"
sky jobs logs -n slow-setup --no-follow | grep "BARRIER_TEST"
```

### 2. Environment Variables Test

```bash
# Test: Verify all JobGroup environment variables
cat > /tmp/test_jobgroup_env.yaml << 'EOF'
name: test-jobgroup-env

placement: SAME_INFRA
execution: parallel

---
name: env-checker

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "=== JobGroup Environment Variables ==="
  echo "SKYPILOT_JOBGROUP_NAME=$SKYPILOT_JOBGROUP_NAME"
  echo "SKYPILOT_NODE_RANK=$SKYPILOT_NODE_RANK"
  echo "SKYPILOT_NUM_NODES=$SKYPILOT_NUM_NODES"
  env | grep SKYPILOT | sort
  echo "=== End Environment Variables ==="
  sleep 60
EOF

sky jobs launch /tmp/test_jobgroup_env.yaml -y -d

# Check environment variables
sky jobs logs -n env-checker --no-follow
```

### 3. SDK Integration Test

```python
# Test: SDK integration for JobGroup
# File: test_job_group_sdk.py

import sky
from sky.jobs.client import sdk as jobs_sdk

# Test 1: Launch JobGroup via SDK
dag = sky.Dag()
dag.name = 'sdk-test-jobgroup'
# Note: SDK API for JobGroup may differ - check implementation

# Test 2: Check JobGroup status
status = jobs_sdk.queue()
print(status)

# Test 3: Cancel JobGroup
# jobs_sdk.cancel(name='sdk-test-jobgroup')
```

---

## Manual Testing Checklist

### Pre-Launch Checks

- [ ] Verify cloud credentials are configured (`sky check`)
- [ ] Verify SkyPilot API server is running (`sky api status`)
- [ ] Verify no conflicting jobs running (`sky jobs queue`)

### Launch Tests

| Test # | Description | Command | Expected Result | Pass/Fail |
|--------|-------------|---------|-----------------|-----------|
| M-001 | Basic 2-job JobGroup | `sky jobs launch basic.yaml -y` | Both jobs start in parallel | |
| M-002 | JobGroup with --infra flag | `sky jobs launch basic.yaml --infra aws -y` | Jobs launch on AWS | |
| M-003 | JobGroup with detach mode | `sky jobs launch basic.yaml -y -d` | Returns immediately | |
| M-004 | JobGroup dry-run | `sky jobs launch basic.yaml --dryrun` | Shows plan without launching | |

### Status/Queue Tests

| Test # | Description | Command | Expected Result | Pass/Fail |
|--------|-------------|---------|-----------------|-----------|
| M-010 | View JobGroup in queue | `sky jobs queue` | Shows all jobs in group | |
| M-011 | Filter by JobGroup name | `sky jobs queue -n {group_name}` | Shows only group jobs | |
| M-012 | Refresh queue | `sky jobs queue -r` | Updates status | |

### Log Tests

| Test # | Description | Command | Expected Result | Pass/Fail |
|--------|-------------|---------|-----------------|-----------|
| M-020 | View job logs | `sky jobs logs -n {job_name}` | Shows job output | |
| M-021 | View controller logs | `sky jobs logs --controller -n {job_name}` | Shows controller output | |
| M-022 | Stream logs | `sky jobs logs -n {job_name} --follow` | Real-time log streaming | |

### Cancellation Tests

| Test # | Description | Command | Expected Result | Pass/Fail |
|--------|-------------|---------|-----------------|-----------|
| M-030 | Cancel JobGroup by name | `sky jobs cancel -y -n {group_name}` | All jobs cancelled | |
| M-031 | Cancel individual job | `sky jobs cancel -y -n {job_name}` | Single job cancelled | |

### Networking Tests (Kubernetes/Same-Infra)

| Test # | Description | Expected Result | Pass/Fail |
|--------|-------------|-----------------|-----------|
| M-040 | Hostname resolution | Jobs can resolve `{job}-{idx}.{group}` | |
| M-041 | TCP connectivity | Jobs can connect via hostname:port | |
| M-042 | /etc/hosts injection | Correct entries in /etc/hosts | |

### Error Handling Tests

| Test # | Description | Command | Expected Result | Pass/Fail |
|--------|-------------|---------|-----------------|-----------|
| M-050 | Invalid YAML | `sky jobs launch invalid.yaml` | Clear error message | |
| M-051 | Missing job name | `sky jobs launch no_name.yaml` | Validation error | |
| M-052 | Job failure | Launch with failing job | Correct status shown | |

---

## Test YAML Examples

### Example 1: Minimal JobGroup

```yaml
# minimal_jobgroup.yaml
name: minimal-jobgroup

placement: SAME_INFRA
execution: parallel

---
name: worker-a

resources:
  cpus: 2+

run: |
  echo "Worker A"
  sleep 60

---
name: worker-b

resources:
  cpus: 2+

run: |
  echo "Worker B"
  sleep 60
```

### Example 2: RL Training JobGroup

```yaml
# rl_training_jobgroup.yaml
name: rl-experiment

placement: SAME_INFRA
execution: parallel

---
name: trainer

resources:
  accelerators: H100:8
  image_id: docker:pytorch/pytorch:latest

num_nodes: 2

run: |
  echo "Starting trainer on node $SKYPILOT_NODE_RANK"
  echo "Connecting to replay buffer at: replay-buffer.${SKYPILOT_JOBGROUP_NAME}:6379"
  # torchrun training command here
  sleep 300

---
name: replay-buffer

resources:
  cpus: 8+
  memory: 256+

run: |
  echo "Starting replay buffer server"
  # python replay_buffer.py --port 6379
  sleep 300

---
name: env-worker

resources:
  cpus: 96+
  memory: 32+

num_nodes: 4

run: |
  echo "Starting env worker $SKYPILOT_NODE_RANK"
  echo "Trainer address: trainer-0.${SKYPILOT_JOBGROUP_NAME}:8080"
  # python env_worker.py
  sleep 300
```

### Example 3: Data Pipeline JobGroup

```yaml
# data_pipeline_jobgroup.yaml
name: data-pipeline

placement: SAME_INFRA
execution: parallel

---
name: ingestion

resources:
  cpus: 4+
  memory: 8+

run: |
  echo "Ingesting data..."
  sleep 120

---
name: transform

resources:
  cpus: 8+
  memory: 16+

run: |
  echo "Transforming data..."
  echo "Reading from ingestion.${SKYPILOT_JOBGROUP_NAME}"
  sleep 120

---
name: export

resources:
  cpus: 2+
  memory: 4+

run: |
  echo "Exporting results..."
  sleep 120
```

---

## Running the Full Test Suite

```bash
# Run all unit tests for JobGroup
pytest tests/unit_tests/test_sky/jobs/test_job_group*.py -v

# Run all smoke tests for JobGroup
pytest tests/smoke_tests/test_job_group.py -v --managed-jobs

# Run on specific clouds
pytest tests/smoke_tests/test_job_group.py -v --aws --gcp --kubernetes

# Run with verbose output and no capture
pytest tests/smoke_tests/test_job_group.py -v -s --managed-jobs

# Run with coverage
pytest tests/unit_tests/test_sky/jobs/test_job_group*.py --cov=sky.jobs --cov-report=html
```

---

## Known Limitations (from PR #8456)

1. **SAME_INFRA placement**: Parsed but not enforced in current implementation
2. **Preemption recovery**: Jobs fail instead of recovering for JobGroups
3. **Controller logs**: Large `_run_job_group` method (~270 lines) may need refactoring

---

## Test Results Template

| Category | Total Tests | Passed | Failed | Skipped |
|----------|-------------|--------|--------|---------|
| Unit Tests - YAML Parsing | | | | |
| Unit Tests - State Management | | | | |
| Unit Tests - Networking | | | | |
| Smoke Tests - Basic | | | | |
| Smoke Tests - Heterogeneous | | | | |
| Smoke Tests - Multi-node | | | | |
| Integration Tests | | | | |
| **TOTAL** | | | | |

---

## Appendix: Pytest Markers

```python
# Available markers for JobGroup tests
@pytest.mark.managed_jobs        # Mark as managed jobs test
@pytest.mark.job_group           # Mark as JobGroup-specific test
@pytest.mark.no_kubernetes       # Skip on Kubernetes
@pytest.mark.no_aws              # Skip on AWS
@pytest.mark.no_gcp              # Skip on GCP
```
