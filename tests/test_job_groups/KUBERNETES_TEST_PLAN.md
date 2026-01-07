# JobGroup Kubernetes Test Plan

This document provides a comprehensive test plan for testing the JobGroup feature on Kubernetes clusters, specifically targeting Google Kubernetes Engine (GKE).

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

## GKE Cluster Setup

This section covers setting up a GKE cluster with the necessary node pools for testing JobGroups with heterogeneous resources.

### Prerequisites

```bash
# Install gcloud CLI if not already installed
# https://cloud.google.com/sdk/docs/install

# Authenticate with GCP
gcloud auth login
gcloud auth application-default login

# Set your project
export GCP_PROJECT="your-gcp-project-id"
gcloud config set project $GCP_PROJECT

# Set region/zone
export GCP_REGION="us-central1"
export GCP_ZONE="us-central1-a"
gcloud config set compute/region $GCP_REGION
gcloud config set compute/zone $GCP_ZONE
```

### Step 1: Create GKE Cluster

```bash
# Cluster configuration
export CLUSTER_NAME="skypilot-jobgroup-test"
export K8S_VERSION="1.29"  # Use a recent stable version

# Create the GKE cluster with a default node pool
gcloud container clusters create $CLUSTER_NAME \
    --zone $GCP_ZONE \
    --num-nodes 2 \
    --machine-type e2-standard-4 \
    --enable-autoscaling \
    --min-nodes 1 \
    --max-nodes 5 \
    --cluster-version $K8S_VERSION \
    --workload-pool="${GCP_PROJECT}.svc.id.goog" \
    --enable-ip-alias \
    --tags=skypilot

# Get credentials for kubectl
gcloud container clusters get-credentials $CLUSTER_NAME --zone $GCP_ZONE

# Verify cluster is accessible
kubectl cluster-info
kubectl get nodes
```

### Step 2: Create Node Pools for Heterogeneous Workloads

Create specialized node pools for different resource requirements:

#### CPU Node Pool (General Purpose)

```bash
# General-purpose CPU nodes for lightweight jobs
gcloud container node-pools create cpu-pool \
    --cluster $CLUSTER_NAME \
    --zone $GCP_ZONE \
    --machine-type e2-standard-4 \
    --num-nodes 2 \
    --enable-autoscaling \
    --min-nodes 0 \
    --max-nodes 10 \
    --node-labels="skypilot.co/node-type=cpu"
```

#### High-Memory Node Pool

```bash
# High-memory nodes for replay buffer and memory-intensive jobs
gcloud container node-pools create highmem-pool \
    --cluster $CLUSTER_NAME \
    --zone $GCP_ZONE \
    --machine-type e2-highmem-8 \
    --num-nodes 1 \
    --enable-autoscaling \
    --min-nodes 0 \
    --max-nodes 5 \
    --node-labels="skypilot.co/node-type=highmem"
```

#### High-CPU Node Pool

```bash
# High-CPU nodes for compute-intensive jobs
gcloud container node-pools create highcpu-pool \
    --cluster $CLUSTER_NAME \
    --zone $GCP_ZONE \
    --machine-type e2-highcpu-16 \
    --num-nodes 1 \
    --enable-autoscaling \
    --min-nodes 0 \
    --max-nodes 5 \
    --node-labels="skypilot.co/node-type=highcpu"
```

#### GPU Node Pool (Optional - for GPU tests)

```bash
# GPU nodes for trainer jobs (T4 GPUs)
# Note: Check GPU availability in your zone
gcloud container node-pools create gpu-pool \
    --cluster $CLUSTER_NAME \
    --zone $GCP_ZONE \
    --machine-type n1-standard-4 \
    --accelerator type=nvidia-tesla-t4,count=1 \
    --num-nodes 0 \
    --enable-autoscaling \
    --min-nodes 0 \
    --max-nodes 4 \
    --node-labels="skypilot.co/node-type=gpu"

# Install NVIDIA GPU drivers (required for GPU nodes)
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/container-engine-accelerators/master/nvidia-driver-installer/cos/daemonset-preloaded.yaml
```

### Step 3: Verify Node Pools

```bash
# List all node pools
gcloud container node-pools list --cluster $CLUSTER_NAME --zone $GCP_ZONE

# Check nodes and their labels
kubectl get nodes --show-labels

# Check node resources
kubectl describe nodes | grep -A 10 "Allocatable:"

# Verify GPU availability (if GPU pool created)
kubectl get nodes -l skypilot.co/node-type=gpu
```

### Step 4: Scale Up Node Pools Before Testing

Before running tests, ensure sufficient nodes are available:

```bash
# Scale up CPU pool for basic tests
gcloud container clusters resize $CLUSTER_NAME \
    --node-pool cpu-pool \
    --num-nodes 3 \
    --zone $GCP_ZONE \
    --quiet

# Scale up highmem pool for RL architecture tests
gcloud container clusters resize $CLUSTER_NAME \
    --node-pool highmem-pool \
    --num-nodes 2 \
    --zone $GCP_ZONE \
    --quiet

# Scale up highcpu pool for env-worker tests
gcloud container clusters resize $CLUSTER_NAME \
    --node-pool highcpu-pool \
    --num-nodes 2 \
    --zone $GCP_ZONE \
    --quiet

# Verify scaling
kubectl get nodes
```

### Step 5: Resource Capacity Planning

For the full RL Architecture test (Phase 4), you need:

| Job | Nodes | CPUs/Node | Memory/Node | Total CPUs | Total Memory |
|-----|-------|-----------|-------------|------------|--------------|
| trainer | 2 | 4 | 8GB | 8 | 16GB |
| data-processor | 2 | 2 | 4GB | 4 | 8GB |
| replay-buffer | 1 | 4 | 16GB | 4 | 16GB |
| env-worker | 2 | 8 | 8GB | 16 | 16GB |
| **Total** | **7** | - | - | **32** | **56GB** |

**Recommended cluster capacity:** At least 40 CPUs and 70GB memory available.

```bash
# Check current cluster capacity
kubectl top nodes

# Alternative: Check allocatable resources
kubectl get nodes -o custom-columns=\
NAME:.metadata.name,\
CPU:.status.allocatable.cpu,\
MEMORY:.status.allocatable.memory
```

### Step 6: Configure SkyPilot for GKE

```bash
# Verify SkyPilot can see the Kubernetes cluster
sky check kubernetes

# Expected output should show Kubernetes as enabled

# Check available resources via SkyPilot
sky show-gpus --cloud kubernetes
```

### GKE-Specific Configuration (Optional)

Create a SkyPilot config for GKE-specific settings:

```bash
# Create/update ~/.sky/config.yaml
cat >> ~/.sky/config.yaml << 'EOF'

kubernetes:
  # Use the GKE cluster context
  # context: gke_<project>_<zone>_<cluster-name>

  # Pod configuration
  pod_config:
    metadata:
      labels:
        app: skypilot-jobgroup-test
    spec:
      # Optional: Use specific node pools via node selector
      # nodeSelector:
      #   skypilot.co/node-type: cpu
EOF
```

---

## Test Environment Setup

### Prerequisites
```bash
# Ensure GKE cluster is set up (see above)
kubectl cluster-info

# Verify SkyPilot can access the cluster
sky check kubernetes

# Check cluster has sufficient resources
kubectl get nodes
kubectl top nodes

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

## Pre-Test Checklist

Before running tests, verify:

- [ ] GKE cluster is running: `gcloud container clusters list`
- [ ] Node pools are created: `gcloud container node-pools list --cluster $CLUSTER_NAME --zone $GCP_ZONE`
- [ ] Sufficient nodes are available: `kubectl get nodes`
- [ ] SkyPilot can access cluster: `sky check kubernetes`
- [ ] API server is running: `sky api status`
- [ ] No orphaned resources from previous tests: `kubectl get pods -A | grep sky`

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

## Phase 6: Failure and Recovery Tests

This phase tests failure handling and recovery mechanisms in JobGroups. These tests are critical for understanding system behavior under adverse conditions.

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

### 6.2 Pod Deletion / Preemption Simulation

For Kubernetes, preemption can be simulated by manually deleting pods.

#### Test Procedure

```bash
# Step 1: Launch a long-running JobGroup
cat > /tmp/k8s_preemption_test.yaml << 'EOF'
---
name: k8s-preemption-test
placement: SAME_INFRA
execution: parallel
---
name: long-running-a
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Job A starting at $(date)"
  for i in {1..60}; do
    echo "Job A heartbeat $i at $(date)"
    sleep 10
  done
  echo "Job A completed"
---
name: long-running-b
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Job B starting at $(date)"
  for i in {1..60}; do
    echo "Job B heartbeat $i at $(date)"
    sleep 10
  done
  echo "Job B completed"
EOF

sky jobs launch /tmp/k8s_preemption_test.yaml -y
JOB_ID=$?  # Capture job ID

# Step 2: Wait for jobs to be running
sleep 60
sky jobs queue

# Step 3: Find the pods
kubectl get pods -A | grep sky
# Note the namespace and pod names

# Step 4: Simulate preemption by deleting ONE pod
NAMESPACE="default"  # Update based on actual namespace
POD_NAME="<pod-name-from-step-3>"
echo "Deleting pod: $POD_NAME in namespace: $NAMESPACE"
kubectl delete pod $POD_NAME -n $NAMESPACE

# Step 5: Monitor recovery behavior
watch -n 5 "sky jobs queue && echo '---' && kubectl get pods -A | grep sky"

# Step 6: Check job status and logs
sky jobs logs $JOB_ID
```

**Verification checklist:**
- [ ] Pod deletion is detected by SkyPilot
- [ ] Job status changes (RECOVERING or FAILED)
- [ ] Document whether job recovers or fails
- [ ] Document behavior of other jobs in the group
- [ ] Check if networking is re-established after recovery

### 6.3 Node Drain Simulation

Simulate node maintenance or eviction:

```bash
# Step 1: Launch JobGroup on specific node pool
sky jobs launch /tmp/k8s_preemption_test.yaml -y

# Step 2: Wait for pods to be scheduled
sleep 60
kubectl get pods -A -o wide | grep sky

# Step 3: Identify the node running the pods
NODE_NAME=$(kubectl get pods -A -o wide | grep sky | head -1 | awk '{print $8}')
echo "Target node: $NODE_NAME"

# Step 4: Cordon the node (prevent new pods)
kubectl cordon $NODE_NAME

# Step 5: Drain the node (evict pods gracefully)
kubectl drain $NODE_NAME --ignore-daemonsets --delete-emptydir-data --force

# Step 6: Monitor job behavior
watch -n 5 sky jobs queue

# Step 7: Uncordon the node after testing
kubectl uncordon $NODE_NAME
```

**Verification:**
- [ ] Jobs detect node drain
- [ ] Document recovery or failure behavior
- [ ] Check if pods are rescheduled

### 6.4 Resource Exhaustion Test

Test behavior when cluster resources are exhausted:

```bash
# Step 1: Create resource-consuming pods to exhaust cluster
cat > /tmp/resource_hog.yaml << 'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: resource-hog-1
spec:
  containers:
  - name: hog
    image: nginx
    resources:
      requests:
        cpu: "4"
        memory: "8Gi"
EOF

# Deploy multiple resource hogs
for i in {1..5}; do
  sed "s/resource-hog-1/resource-hog-$i/" /tmp/resource_hog.yaml | kubectl apply -f -
done

# Step 2: Launch JobGroup while resources are constrained
sky jobs launch tests/test_job_groups/k8s_basic_test.yaml -y

# Step 3: Observe behavior
sky jobs queue
kubectl get pods -A | grep -E "(sky|resource-hog)"

# Step 4: Clean up resource hogs
kubectl delete pods -l app!=skypilot --field-selector=metadata.name=resource-hog-1
for i in {1..5}; do
  kubectl delete pod resource-hog-$i --ignore-not-found
done

# Step 5: Check if jobs recover
watch -n 5 sky jobs queue
```

**Verification:**
- [ ] Jobs queue or fail gracefully when resources unavailable
- [ ] Clear error messages about resource constraints
- [ ] Jobs proceed when resources become available

### 6.5 Network Partition Simulation

Test behavior when network issues occur between pods:

```bash
# Step 1: Launch networking test
sky jobs launch tests/test_job_groups/k8s_networking_test.yaml -y

# Step 2: Wait for both pods to be running
sleep 30
kubectl get pods -A | grep sky

# Step 3: Create network policy to block traffic (requires NetworkPolicy support)
cat > /tmp/network_block.yaml << 'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: block-all-traffic
spec:
  podSelector:
    matchLabels:
      # Match SkyPilot pods - adjust label as needed
      app: skypilot
  policyTypes:
  - Ingress
  - Egress
  ingress: []
  egress: []
EOF

# Apply network policy
NAMESPACE="default"  # Update based on actual namespace
kubectl apply -f /tmp/network_block.yaml -n $NAMESPACE

# Step 4: Observe behavior
sleep 30
sky jobs queue

# Step 5: Remove network policy
kubectl delete networkpolicy block-all-traffic -n $NAMESPACE

# Step 6: Check if jobs recover
watch -n 5 sky jobs queue
```

**Note:** NetworkPolicy support varies by cluster configuration.

### 6.6 Controller Process Failure

Test behavior when the SkyPilot controller encounters issues:

```bash
# Step 1: Launch a long-running JobGroup
sky jobs launch /tmp/k8s_preemption_test.yaml -y

# Step 2: Wait for jobs to be running
sleep 60
sky jobs queue

# Step 3: Restart the API server (simulates controller restart)
sky api stop
sleep 5
sky api start

# Step 4: Check if jobs continue correctly
sky jobs queue

# Step 5: Verify job completion
# Jobs should either:
# - Continue running and complete successfully
# - Be marked as needing recovery
```

**Verification:**
- [ ] API server restart doesn't crash running jobs
- [ ] Job status is correctly reflected after restart
- [ ] Jobs either complete or enter recovery state

### 6.7 Partial JobGroup Failure Recovery

Test what happens when only some jobs in a JobGroup fail:

**Test file:** `k8s_partial_failure_test.yaml`

```yaml
---
name: k8s-partial-failure
placement: SAME_INFRA
execution: parallel
---
name: stable-job-1
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Stable job 1 starting"
  for i in {1..30}; do
    echo "Stable 1 heartbeat $i"
    sleep 5
  done
  echo "Stable job 1 completed successfully"
---
name: flaky-job
resources:
  cpus: 2
  infra: kubernetes
  job_recovery:
    max_restarts_on_errors: 2
    recover_on_exit_codes: [1]
run: |
  echo "Flaky job starting"
  sleep 30
  echo "Flaky job failing intentionally"
  exit 1
---
name: stable-job-2
resources:
  cpus: 2
  infra: kubernetes
run: |
  echo "Stable job 2 starting"
  for i in {1..30}; do
    echo "Stable 2 heartbeat $i"
    sleep 5
  done
  echo "Stable job 2 completed successfully"
```

```bash
# Launch the test
sky jobs launch k8s_partial_failure_test.yaml -y

# Monitor behavior
watch -n 5 sky jobs queue

# Document:
# 1. Does flaky-job attempt recovery?
# 2. Do stable jobs continue running?
# 3. What is the final state of each job?
# 4. Are all clusters cleaned up?
```

**Verification:**
- [ ] Recovery attempts occur for flaky-job
- [ ] Stable jobs are not affected by flaky-job failure
- [ ] Final states are correctly reported
- [ ] Cleanup handles partial success/failure

### 6.8 Recovery Test Result Template

| Scenario | Expected Behavior | Actual Behavior | Pass/Fail | Notes |
|----------|-------------------|-----------------|-----------|-------|
| Pod deletion | Recovery or FAILED | | | |
| Node drain | Pods rescheduled or FAILED | | | |
| Resource exhaustion | Queued or clear error | | | |
| Network partition | Timeout/retry or FAILED | | | |
| Controller restart | Jobs continue | | | |
| Partial failure | Other jobs unaffected | | | |
| Exit code recovery | Job restarts | | | |

**Key questions to document:**
1. Does the JobGroup use all-or-nothing failure semantics?
2. Are healthy jobs affected when one job fails?
3. Is networking re-established after pod recovery?
4. How long does recovery take?
5. What happens to in-progress work when recovery occurs?

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

### SkyPilot Resource Cleanup

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

### GKE Cluster Management

#### Scale Down Node Pools (Cost Savings)

```bash
# Scale down node pools after testing to save costs
gcloud container clusters resize $CLUSTER_NAME \
    --node-pool cpu-pool \
    --num-nodes 0 \
    --zone $GCP_ZONE \
    --quiet

gcloud container clusters resize $CLUSTER_NAME \
    --node-pool highmem-pool \
    --num-nodes 0 \
    --zone $GCP_ZONE \
    --quiet

gcloud container clusters resize $CLUSTER_NAME \
    --node-pool highcpu-pool \
    --num-nodes 0 \
    --zone $GCP_ZONE \
    --quiet

# Scale down GPU pool if created
gcloud container clusters resize $CLUSTER_NAME \
    --node-pool gpu-pool \
    --num-nodes 0 \
    --zone $GCP_ZONE \
    --quiet 2>/dev/null || true
```

#### Delete GKE Cluster (Full Teardown)

```bash
# WARNING: This deletes the entire cluster and all data
gcloud container clusters delete $CLUSTER_NAME \
    --zone $GCP_ZONE \
    --quiet

# Verify deletion
gcloud container clusters list
```

#### Delete Individual Node Pools

```bash
# Delete specific node pool
gcloud container node-pools delete gpu-pool \
    --cluster $CLUSTER_NAME \
    --zone $GCP_ZONE \
    --quiet
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
