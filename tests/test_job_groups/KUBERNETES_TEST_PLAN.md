# JobGroup Kubernetes Test Plan

This document provides a comprehensive test plan for testing the JobGroup feature on Kubernetes clusters.

## Overview

JobGroups enable users to run heterogeneous workloads as a single unit, where each job can have different resource requirements and entrypoints while sharing network access and optional shared filesystems.

**Key components to test:**
- YAML parsing and validation
- Parallel cluster launching
- Networking setup (DNS and /etc/hosts injection)
- Barrier synchronization
- Job monitoring and lifecycle
- Failure handling and recovery
- Environment variable injection

---

## Test Environment Setup

### Prerequisites
```bash
# Ensure Kubernetes cluster is accessible
kubectl cluster-info

# Verify SkyPilot can access the cluster
sky check kubernetes

# Ensure sufficient cluster resources for testing
# Minimum: 8+ CPUs, 16GB+ memory available
```

### API Server Setup
```bash
# Restart API server to pick up any code changes
sky api stop && sky api start
sky api status
```

---

## Phase 1: Unit Tests (No Cloud Resources)

### 1.1 YAML Parsing Tests

Run existing unit tests:
```bash
pytest tests/test_job_groups/test_job_group.py -v
```

**Additional manual verification:**

```bash
# Test: Valid JobGroup detection
cat > /tmp/test_jobgroup_valid.yaml << 'EOF'
---
name: test-group
placement: SAME_INFRA
execution: parallel
---
name: job-a
resources:
  cpus: 2
run: echo "Job A"
---
name: job-b
resources:
  cpus: 2
run: echo "Job B"
EOF

# Expected: Should parse as JobGroup
python -c "
from sky.utils import dag_utils
dag = dag_utils.load_dag_from_yaml('/tmp/test_jobgroup_valid.yaml')
assert dag.is_job_group(), 'Should be detected as JobGroup'
print('OK: Detected as JobGroup')
print(f'  Name: {dag.name}')
print(f'  Tasks: {[t.name for t in dag.tasks]}')
"
```

**Edge cases to test:**

| Test Case | YAML Content | Expected Result |
|-----------|--------------|-----------------|
| Missing job name | `run: echo hello` (no name) | `ValueError: must have a "name" field` |
| Duplicate job names | Two jobs named "job-a" | `ValueError: Duplicate job name` |
| Invalid placement | `placement: INVALID` | `ValueError: Invalid placement mode` |
| Empty JobGroup | Only header, no jobs | Parse error or single-task |
| Single job | Header + 1 job | Valid JobGroup |
| Chain DAG misdetection | `name: foo` only header | Should NOT be JobGroup |

### 1.2 Networking Unit Tests

```bash
# Test environment variable generation
python -c "
from sky.jobs import job_group_networking

env_vars = job_group_networking.get_job_group_env_vars('test-group')
assert 'SKYPILOT_JOBGROUP_NAME' in env_vars
assert env_vars['SKYPILOT_JOBGROUP_NAME'] == 'test-group'
print('OK: Environment variables generated correctly')
print(f'  Vars: {env_vars}')
"
```

---

## Phase 2: Basic Integration Tests (Kubernetes)

### 2.1 Minimal JobGroup (2 Jobs, CPU Only)

**Test file:** `tests/test_job_groups/test_job_group.yaml`

```yaml
---
name: test-job-group
placement: SAME_INFRA
execution: parallel
---
name: job-a
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Job A Starting"
  echo "SKYPILOT_JOBGROUP_NAME: ${SKYPILOT_JOBGROUP_NAME}"
  echo "Hostname: $(hostname)"
  sleep 10
  echo "Job A done"
---
name: job-b
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Job B Starting"
  echo "SKYPILOT_JOBGROUP_NAME: ${SKYPILOT_JOBGROUP_NAME}"
  echo "Hostname: $(hostname)"
  sleep 10
  echo "Job B done"
```

**Commands:**
```bash
# Launch JobGroup
sky jobs launch tests/test_job_groups/test_job_group.yaml -y

# Monitor status
sky jobs queue

# Check logs for both jobs
sky jobs logs <job_id>

# Expected:
# - Both jobs launch in parallel
# - Both jobs have SKYPILOT_JOBGROUP_NAME set
# - Both jobs complete with SUCCEEDED status
```

**Verification checklist:**
- [ ] Both jobs show in `sky jobs queue`
- [ ] Both jobs have status RUNNING → SUCCEEDED
- [ ] Logs show correct environment variables
- [ ] Jobs complete within expected time
- [ ] Cleanup removes both clusters

### 2.2 Cross-Job Networking Test

**Test file:** `k8s_networking_test.yaml`

```yaml
---
name: k8s-network-test
placement: SAME_INFRA
execution: parallel
---
name: server
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Server starting on $(hostname)"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"

  # Start a simple HTTP server
  python3 -m http.server 8080 &
  SERVER_PID=$!

  # Keep running for client to connect
  sleep 60

  # Cleanup
  kill $SERVER_PID 2>/dev/null || true
  echo "Server done"
---
name: client
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Client starting on $(hostname)"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"

  # Wait for server to start
  sleep 10

  # Test DNS resolution
  echo "Testing DNS resolution..."
  SERVER_HOST="server-0.${SKYPILOT_JOBGROUP_NAME}"
  echo "Server host: $SERVER_HOST"

  # Try to resolve the hostname
  getent hosts $SERVER_HOST || echo "DNS lookup failed, checking /etc/hosts"
  cat /etc/hosts | grep -i server || echo "No server entry in /etc/hosts"

  # Try to connect to server
  echo "Attempting connection to server..."
  for i in {1..10}; do
    if curl -s --connect-timeout 5 http://$SERVER_HOST:8080/ > /dev/null 2>&1; then
      echo "SUCCESS: Connected to server on attempt $i"
      exit 0
    fi
    echo "Attempt $i failed, retrying..."
    sleep 3
  done

  echo "FAILED: Could not connect to server"
  exit 1
```

**Verification:**
- [ ] Server starts and listens on port 8080
- [ ] Client can resolve server hostname
- [ ] Client successfully connects to server
- [ ] Both jobs complete with SUCCEEDED status

### 2.3 Multi-Node Job in JobGroup

**Test file:** `k8s_multinode_test.yaml`

```yaml
---
name: k8s-multinode-group
placement: SAME_INFRA
execution: parallel
---
name: workers
resources:
  cpus: 2
  infra: kubernetes
num_nodes: 2
run: |
  echo "Worker node $(hostname) starting"
  echo "SKYPILOT_NUM_NODES: ${SKYPILOT_NUM_NODES}"
  echo "SKYPILOT_NODE_RANK: ${SKYPILOT_NODE_RANK}"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"
  sleep 20
  echo "Worker node done"
---
name: coordinator
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Coordinator starting"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"

  # Wait for workers to be ready
  sleep 5

  # Check if we can reach worker nodes
  echo "Checking worker nodes..."
  for i in 0 1; do
    WORKER_HOST="workers-$i.${SKYPILOT_JOBGROUP_NAME}"
    echo "Checking $WORKER_HOST..."
    ping -c 1 $WORKER_HOST 2>/dev/null && echo "  Reachable" || echo "  Not reachable"
  done

  sleep 15
  echo "Coordinator done"
```

**Verification:**
- [ ] Workers launch as 2-node cluster
- [ ] Coordinator can reference worker hostnames
- [ ] All nodes have correct environment variables
- [ ] num_nodes=2 is respected

---

## Phase 3: Heterogeneous Resource Tests

### 3.1 Mixed CPU/Memory Requirements

**Test file:** `k8s_heterogeneous_test.yaml`

```yaml
---
name: k8s-hetero-test
placement: SAME_INFRA
execution: parallel
---
name: compute-heavy
resources:
  cpus: 4
  memory: 4+
  infra: kubernetes
run: |
  echo "Compute-heavy job on $(hostname)"
  echo "CPU info:"
  cat /proc/cpuinfo | grep processor | wc -l
  sleep 20
  echo "Done"
---
name: memory-heavy
resources:
  cpus: 2
  memory: 8+
  infra: kubernetes
run: |
  echo "Memory-heavy job on $(hostname)"
  echo "Memory info:"
  free -h
  sleep 20
  echo "Done"
---
name: lightweight
resources:
  cpus: 1
  memory: 2+
  infra: kubernetes
run: |
  echo "Lightweight job on $(hostname)"
  sleep 20
  echo "Done"
```

**Verification:**
- [ ] Each job gets requested resources
- [ ] Jobs with different resource profiles launch successfully
- [ ] Kubernetes schedules pods on appropriate nodes

### 3.2 GPU Job with CPU Jobs (if GPUs available)

**Test file:** `k8s_gpu_cpu_test.yaml`

```yaml
---
name: k8s-gpu-cpu-group
placement: SAME_INFRA
execution: parallel
---
name: gpu-trainer
resources:
  accelerators: T4:1  # or available GPU type
  infra: kubernetes
run: |
  echo "GPU trainer starting"
  nvidia-smi || echo "nvidia-smi not available"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"
  sleep 30
  echo "GPU trainer done"
---
name: cpu-preprocessor
resources:
  cpus: 4
  infra: kubernetes
run: |
  echo "CPU preprocessor starting"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"
  echo "Can reach trainer at: gpu-trainer-0.${SKYPILOT_JOBGROUP_NAME}"
  sleep 30
  echo "CPU preprocessor done"
```

**Skip if:** No GPUs available in cluster
**Verification:**
- [ ] GPU job gets GPU allocation
- [ ] CPU job runs alongside GPU job
- [ ] Both can reference each other by hostname

---

## Phase 4: RL Example Architecture Test

This tests the full architecture shown in the design document.

**Test file:** `k8s_rl_architecture_test.yaml`

```yaml
---
name: rl-experiment
placement: SAME_INFRA
execution: parallel
---
name: trainer
resources:
  cpus: 4
  memory: 8+
  infra: kubernetes
num_nodes: 2
run: |
  echo "Trainer node ${SKYPILOT_NODE_RANK} starting"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"
  echo "Master addr: trainer-0.${SKYPILOT_JOBGROUP_NAME}"
  echo "Replay buffer: replay-buffer-0.${SKYPILOT_JOBGROUP_NAME}:6379"

  # Simulate training
  for i in {1..10}; do
    echo "Training iteration $i"
    sleep 5
  done
  echo "Trainer done"
---
name: data-processor
resources:
  cpus: 2
  memory: 4+
  infra: kubernetes
num_nodes: 2
run: |
  echo "Data processor ${SKYPILOT_NODE_RANK} starting"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"
  echo "Output to: replay-buffer-0.${SKYPILOT_JOBGROUP_NAME}:6379"

  # Simulate data processing
  for i in {1..8}; do
    echo "Processing batch $i"
    sleep 5
  done
  echo "Data processor done"
---
name: replay-buffer
resources:
  cpus: 4
  memory: 16+
  infra: kubernetes
run: |
  echo "Replay buffer starting on $(hostname)"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"

  # Simulate replay buffer service
  echo "Listening on port 6379..."
  for i in {1..12}; do
    echo "Replay buffer heartbeat $i"
    sleep 5
  done
  echo "Replay buffer done"
---
name: env-worker
resources:
  cpus: 8
  memory: 8+
  infra: kubernetes
num_nodes: 2
run: |
  echo "Env worker ${SKYPILOT_NODE_RANK} starting"
  echo "JobGroup: ${SKYPILOT_JOBGROUP_NAME}"
  echo "Trainer addr: trainer-0.${SKYPILOT_JOBGROUP_NAME}:8080"

  # Simulate environment execution
  for i in {1..10}; do
    echo "Environment step $i"
    sleep 5
  done
  echo "Env worker done"
```

**Commands:**
```bash
# Launch the RL architecture
sky jobs launch k8s_rl_architecture_test.yaml -y

# Monitor all jobs
watch -n 5 sky jobs queue

# Check individual job logs
sky jobs logs <job_id>
```

**Verification checklist:**
- [ ] All 4 job types launch successfully
- [ ] trainer: 2 nodes
- [ ] data-processor: 2 nodes
- [ ] replay-buffer: 1 node
- [ ] env-worker: 2 nodes
- [ ] Total of 7 pods/clusters created
- [ ] All jobs can reference each other by hostname
- [ ] All jobs complete with SUCCEEDED status
- [ ] Cleanup removes all clusters

---

## Phase 5: Edge Cases and Error Handling

### 5.1 Job Failure in Group

**Test file:** `k8s_failure_test.yaml`

```yaml
---
name: k8s-failure-test
placement: SAME_INFRA
execution: parallel
---
name: success-job
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Success job running"
  sleep 30
  echo "Success job completed"
---
name: failing-job
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Failing job starting"
  sleep 10
  echo "About to fail..."
  exit 1
```

**Expected behavior:**
- [ ] Both jobs launch
- [ ] `failing-job` fails with exit code 1
- [ ] `failing-job` shows FAILED status
- [ ] `success-job` may continue or be cancelled (document actual behavior)
- [ ] Logs for failing job show failure reason

### 5.2 Resource Unavailable

**Test file:** `k8s_resource_unavailable.yaml`

```yaml
---
name: k8s-resource-test
placement: SAME_INFRA
execution: parallel
---
name: normal-job
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Normal job"
  sleep 10
---
name: impossible-job
resources:
  cpus: 1000  # More than cluster has
  memory: 10000+
  infra: kubernetes
run: |
  echo "This should not run"
```

**Expected behavior:**
- [ ] Optimization/scheduling should fail early
- [ ] Clear error message about resource unavailability
- [ ] No partial launches (all-or-nothing)

### 5.3 Cancellation Test

```bash
# Launch a long-running JobGroup
cat > /tmp/k8s_cancel_test.yaml << 'EOF'
---
name: k8s-cancel-test
placement: SAME_INFRA
execution: parallel
---
name: long-job-a
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Long job A starting"
  sleep 300
---
name: long-job-b
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Long job B starting"
  sleep 300
EOF

sky jobs launch /tmp/k8s_cancel_test.yaml -y

# Wait for jobs to start running
sleep 30

# Cancel the JobGroup
sky jobs cancel <job_id>

# Verify cancellation
sky jobs queue
```

**Verification:**
- [ ] `sky jobs cancel` cancels ALL jobs in the group
- [ ] Both jobs show CANCELLED status
- [ ] Clusters are cleaned up
- [ ] No orphaned resources

### 5.4 Empty Run Command

**Test file:** `k8s_empty_run_test.yaml`

```yaml
---
name: k8s-empty-run
placement: SAME_INFRA
execution: parallel
---
name: empty-job
resources:
  cpus: 2
  infra: kubernetes
# No run command
---
name: normal-job
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Normal job"
  sleep 10
```

**Expected behavior:**
- [ ] Empty job should be skipped or succeed immediately
- [ ] Normal job should run as expected

### 5.5 Duplicate Job Names (Validation Test)

```bash
cat > /tmp/k8s_duplicate_names.yaml << 'EOF'
---
name: k8s-duplicate-test
placement: SAME_INFRA
execution: parallel
---
name: my-job
resources:
  cpus: 2
  infra: kubernetes
run: echo "Job 1"
---
name: my-job
resources:
  cpus: 2
  infra: kubernetes
run: echo "Job 2"
EOF

# This should fail validation
sky jobs launch /tmp/k8s_duplicate_names.yaml -y
```

**Expected:** Validation error about duplicate job names

---

## Phase 6: Recovery Tests

### 6.1 Job Recovery Configuration

**Test file:** `k8s_recovery_test.yaml`

```yaml
---
name: k8s-recovery-test
placement: SAME_INFRA
execution: parallel
---
name: recoverable-job
resources:
  cpus: 2
  infra: kubernetes
  job_recovery:
    max_restarts_on_errors: 3
    recover_on_exit_codes: [29]
run: |
  echo "Recoverable job starting (attempt $SKYPILOT_TASK_ID)"
  # Simulate a recoverable failure on first attempt
  if [ ! -f /tmp/recovery_marker ]; then
    touch /tmp/recovery_marker
    echo "First attempt, simulating recoverable error"
    exit 29
  fi
  echo "Recovery successful!"
  sleep 10
---
name: normal-job
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Normal job running"
  sleep 30
```

**Note:** Recovery behavior in JobGroups may differ from single jobs. Document actual behavior.

### 6.2 Preemption Simulation (if possible)

For Kubernetes, preemption can be simulated by:
1. Using low-priority pods
2. Manually deleting pods
3. Using resource pressure

```bash
# Launch JobGroup
sky jobs launch k8s_recovery_test.yaml -y

# Get pod names
kubectl get pods -A | grep sky

# Simulate preemption by deleting a pod
kubectl delete pod <pod-name> -n <namespace>

# Observe recovery behavior
watch -n 5 sky jobs queue
```

**Expected behavior (based on PR notes):**
- Jobs may fail rather than recover automatically
- Document actual recovery behavior

---

## Phase 7: Shared Filesystem Tests (if NFS available)

### 7.1 Shared Volume Test

**Prerequisites:**
- NFS server or PVC available
- Volume configured in SkyPilot

**Test file:** `k8s_shared_volume_test.yaml`

```yaml
---
name: k8s-shared-volume
placement: SAME_INFRA
execution: parallel
---
name: writer
resources:
  cpus: 2
  infra: kubernetes
volumes:
  /mnt/shared: shared-volume  # Must be pre-configured
run: |
  echo "Writer starting"
  echo "Writing to shared volume..."
  echo "Hello from writer at $(date)" > /mnt/shared/test_file.txt
  echo "File written"
  ls -la /mnt/shared/
  sleep 30
---
name: reader
resources:
  cpus: 2
  infra: kubernetes
volumes:
  /mnt/shared: shared-volume
run: |
  echo "Reader starting"
  sleep 10  # Wait for writer
  echo "Reading from shared volume..."
  cat /mnt/shared/test_file.txt || echo "File not found yet"
  sleep 5
  cat /mnt/shared/test_file.txt || echo "Still not found"
  sleep 20
```

**Skip if:** No shared volume available
**Verification:**
- [ ] Both jobs mount the same volume
- [ ] Writer creates file
- [ ] Reader can read file created by writer

---

## Phase 8: Stress and Scale Tests

### 8.1 Many Jobs in Single Group

**Test file:** `k8s_scale_test.yaml`

```yaml
---
name: k8s-scale-test
placement: SAME_INFRA
execution: parallel
---
name: job-1
resources:
  cpus: 1
  infra: kubernetes
run: |
  echo "Job 1"
  sleep 20
---
name: job-2
resources:
  cpus: 1
  infra: kubernetes
run: |
  echo "Job 2"
  sleep 20
---
name: job-3
resources:
  cpus: 1
  infra: kubernetes
run: |
  echo "Job 3"
  sleep 20
---
name: job-4
resources:
  cpus: 1
  infra: kubernetes
run: |
  echo "Job 4"
  sleep 20
---
name: job-5
resources:
  cpus: 1
  infra: kubernetes
run: |
  echo "Job 5"
  sleep 20
```

**Verification:**
- [ ] All 5 jobs launch successfully
- [ ] Parallel execution is observed
- [ ] All jobs complete
- [ ] Cleanup handles all clusters

### 8.2 Concurrent JobGroups

```bash
# Launch multiple JobGroups concurrently
sky jobs launch tests/test_job_groups/test_job_group.yaml -n group-1 -y &
sky jobs launch tests/test_job_groups/test_job_group.yaml -n group-2 -y &
wait

# Verify both groups run independently
sky jobs queue
```

**Verification:**
- [ ] Both JobGroups launch and run
- [ ] No interference between groups
- [ ] Proper cleanup of both

---

## Test Result Summary Template

| Test | Status | Notes |
|------|--------|-------|
| 1.1 YAML Parsing | | |
| 1.2 Networking Unit | | |
| 2.1 Minimal JobGroup | | |
| 2.2 Cross-Job Networking | | |
| 2.3 Multi-Node Job | | |
| 3.1 Mixed Resources | | |
| 3.2 GPU + CPU | | |
| 4.1 RL Architecture | | |
| 5.1 Job Failure | | |
| 5.2 Resource Unavailable | | |
| 5.3 Cancellation | | |
| 5.4 Empty Run | | |
| 5.5 Duplicate Names | | |
| 6.1 Recovery Config | | |
| 6.2 Preemption | | |
| 7.1 Shared Volume | | |
| 8.1 Scale (5 jobs) | | |
| 8.2 Concurrent Groups | | |

---

## Known Limitations (from PR)

1. **Placement enforcement:** `placement: SAME_INFRA` is parsed but not enforced
2. **Preemption recovery:** JobGroup jobs may fail rather than recover on preemption
3. **DAG execution:** Only parallel execution supported, not arbitrary DAG

---

## Cleanup Commands

```bash
# Cancel all running jobs
sky jobs cancel -a

# Check for orphaned clusters
sky status --all

# Clean up any remaining clusters
sky down --all -y

# Verify cleanup
kubectl get pods -A | grep sky
```

---

## Appendix: Quick Test Commands

```bash
# Run all unit tests
pytest tests/test_job_groups/ -v

# Quick smoke test
sky jobs launch tests/test_job_groups/test_job_group.yaml -y

# Check status
sky jobs queue

# View logs
sky jobs logs <job_id>

# Cancel
sky jobs cancel <job_id>
```
