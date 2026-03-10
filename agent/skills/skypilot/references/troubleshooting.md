# SkyPilot Troubleshooting Guide

This guide covers common issues when using SkyPilot and provides concrete solutions with exact commands to run.

---

## 1. Installation and Credentials

### pip install conflicts

**Symptom**: `pip install skypilot` fails with dependency conflicts or resolution errors.

**Solutions**:

```bash
# Use a clean virtual environment to avoid conflicts
uv venv --seed --python 3.11
source .venv/bin/activate

# Install with all cloud support
uv pip install "skypilot[all]"

# Or install with specific clouds only (reduces dependency conflicts)
uv pip install "skypilot[aws,gcp]"
uv pip install "skypilot[kubernetes]"
```

If you see `ResolutionImpossible` or version conflicts:

```bash
# Force reinstall in a fresh environment
uv pip install --force-reinstall "skypilot[all]"
```

**Python version**: SkyPilot supports Python 3.8-3.11. If using Python 3.12+, downgrade:

```bash
uv venv --seed --python 3.11
```

### Per-cloud credential setup

**AWS**:

```bash
# Configure credentials
aws configure
# Verify
aws sts get-caller-identity

# If using IAM roles (e.g., on EC2), no explicit configure needed.
# SkyPilot auto-detects instance profiles.
```

Common AWS issues:
- `NoCredentialError`: Run `aws configure` or set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.
- Insufficient IAM permissions: The IAM user/role needs EC2, IAM, and S3 permissions. See SkyPilot docs for the minimal IAM policy.

**GCP**:

```bash
# Login and set project
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID

# Verify
gcloud auth list
```

Common GCP issues:
- `GOOGLE_APPLICATION_CREDENTIALS` set to a service account key may conflict with `gcloud auth`. Unset it if using user credentials.
- Missing APIs: Enable Compute Engine API via `gcloud services enable compute.googleapis.com`.

**Azure**:

```bash
# Login
az login

# Set subscription
az account set --subscription YOUR_SUBSCRIPTION_ID

# Verify
az account show
```

**Kubernetes**:

```bash
# Ensure kubeconfig is set
kubectl config current-context
kubectl get nodes

# If using multiple kubeconfigs, set KUBECONFIG
export KUBECONFIG=~/.kube/config:~/.kube/other-config
```

### Interpreting `sky check` output

```bash
# Check all clouds
sky check -o json

# Check specific clouds
sky check aws gcp kubernetes -o json
```

The output shows each cloud as `enabled` or `disabled` with a reason. Common reasons for disabled:
- **Credentials not found**: Set up credentials as shown above.
- **Required package not installed**: Install the cloud extras, e.g., `pip install "skypilot[aws]"`.
- **API not enabled**: For GCP, enable required APIs.

### Enabling specific clouds only

To restrict SkyPilot to certain clouds, install only those extras:

```bash
uv pip install "skypilot[aws,kubernetes]"
```

Or use `~/.sky/config.yaml` to restrict allowed clouds:

```yaml
allowed_clouds:
  - aws
  - kubernetes
```

---

## 2. Cluster Launch Failures

### Quota exceeded

**Symptom**: Launch fails with `QUOTA_EXCEEDED` or quota-related errors.

**Solutions by cloud**:

**AWS**: Request quota increases via the AWS Service Quotas console:

```
AWS Console > Service Quotas > Amazon EC2 > Running On-Demand Standard instances
```

Check current limits:

```bash
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-1216C47A
```

**GCP**: Request quota increases via the GCP Console:

```
GCP Console > IAM & Admin > Quotas > Filter by "GPUs"
```

For `GPUS_ALL_REGIONS` quota errors, you must request a global GPU quota increase.

**Azure**: Request quota increases via the Azure Portal:

```
Azure Portal > Subscriptions > Usage + quotas > Request increase
```

### GPU unavailable

**Symptom**: `No launchable resource found` when requesting GPUs.

**Solutions**:

```bash
# Check GPU availability across clouds
sky gpus list --all -o json

# Check availability for a specific GPU on a cloud
sky gpus list H100 --infra aws -o json

# Check all regions for a GPU
sky gpus list H100 --all-regions -o json
```

Use `any_of` in your YAML to specify fallback options:

```yaml
resources:
  any_of:
    - infra: aws
      accelerators: H100:8
    - infra: gcp
      accelerators: H100:8
    - infra: aws
      accelerators: A100-80GB:8
```

Or use `ordered` to specify a strict preference order:

```yaml
resources:
  ordered:
    - infra: aws/us-east-1
      accelerators: H100:8
    - infra: aws/us-west-2
      accelerators: H100:8
    - infra: gcp/us-central1
      accelerators: A100-80GB:8
```

### Instance type errors

**Symptom**: `Invalid instance type` or similar errors.

```bash
# List valid instance types for a cloud
sky gpus list --infra aws --all -o json

# Check a specific accelerator's available instance types
sky gpus list A100 --infra gcp -o json
```

Ensure the instance type matches the region. Not all instance types are available in all regions.

### Region and zone issues

**Symptom**: Launch fails in a specific region or zone.

Specify a different region or zone:

```yaml
resources:
  infra: aws/us-west-2
  # Or with a specific zone:
  # infra: aws/us-west-2a
  accelerators: A100:4
```

Let SkyPilot auto-select by omitting the region:

```yaml
resources:
  infra: aws
  accelerators: A100:4
```

### Cluster stuck in INIT state

**Symptom**: `sky status` shows a cluster in INIT state and it never transitions to UP.

**Solutions**:

```bash
# Force teardown the stuck cluster
sky down -p mycluster

# The -p (--purge) flag forces cleanup even if the cluster is in a bad state.
# After cleanup, relaunch:
sky launch mycluster.yaml
```

If the cluster keeps getting stuck:

```bash
# Check for errors in the launch logs
sky logs mycluster

# Refresh cluster status from the cloud provider
sky status --refresh -o json
```

### Permission denied during provisioning

**Symptom**: Provisioning fails with permission or authorization errors.

- **AWS**: Ensure your IAM user/role has `ec2:RunInstances`, `ec2:CreateSecurityGroup`, `iam:PassRole`, and related permissions.
- **GCP**: Ensure `roles/compute.admin` or equivalent is granted.
- **Azure**: Ensure `Contributor` role on the subscription.
- **Kubernetes**: Ensure your kubeconfig user has permissions to create pods, services, and secrets in the target namespace.

```bash
# Verify cloud permissions
sky check aws
sky check gcp
```

### VPC and networking issues

**Symptom**: Cluster launches but nodes cannot communicate, or SSH fails.

**AWS**:
- Ensure the default VPC exists, or configure a specific VPC in `~/.sky/config.yaml`.
- Check security groups allow SSH (port 22) inbound from your IP.

**GCP**:
- Ensure the `default` network has firewall rules allowing SSH.
- Check if your organization enforces VPC Service Controls.

```yaml
# ~/.sky/config.yaml — specify a custom VPC (AWS example)
aws:
  vpc_name: my-vpc
```

---

## 3. Setup Command Failures

### pip install failures in setup

**Symptom**: `setup` commands fail when installing Python packages.

**Solutions**:

```yaml
setup: |
  # Pin versions to avoid resolution conflicts
  pip install torch==2.1.0 transformers==4.36.0

  # Use --no-build-isolation if compilation fails
  pip install flash-attn --no-build-isolation

  # If you hit "No space left on device" during pip install
  pip install --cache-dir /tmp/pip-cache mypackage
```

For packages that need compilation (e.g., flash-attn, apex):

```yaml
setup: |
  # Ensure build tools are present
  sudo apt-get update && sudo apt-get install -y build-essential ninja-build
  pip install flash-attn --no-build-isolation
```

### CUDA version mismatches

**Symptom**: PyTorch reports CUDA unavailable, or `RuntimeError: CUDA error` at runtime.

**Diagnosing**:

```yaml
run: |
  # Check driver CUDA version
  nvidia-smi

  # Check PyTorch's CUDA version
  python -c "import torch; print(torch.version.cuda); print(torch.cuda.is_available())"

  # These must be compatible: PyTorch CUDA version <= driver CUDA version
```

**Solutions**:

```yaml
setup: |
  # Install PyTorch matching the driver's CUDA version
  # For CUDA 12.1 driver:
  pip install torch --index-url https://download.pytorch.org/whl/cu121

  # For CUDA 11.8 driver:
  pip install torch --index-url https://download.pytorch.org/whl/cu118
```

To check which CUDA version the image provides:

```bash
ssh mycluster "nvidia-smi | head -4"
```

### Conda environment issues

**Symptom**: Setup runs in the wrong conda environment, or conda activate fails.

**Solutions**:

```yaml
setup: |
  # Conda activate does not work in non-interactive shells by default.
  # Use conda run instead, or source conda.sh explicitly:
  source ~/miniconda3/etc/profile.d/conda.sh
  conda activate myenv
  pip install -r requirements.txt
```

Or avoid conda and use pip with venv:

```yaml
setup: |
  python -m venv ~/myenv
  source ~/myenv/bin/activate
  pip install -r requirements.txt

run: |
  source ~/myenv/bin/activate
  python train.py
```

### Setup caching behavior

SkyPilot caches the setup commands and only reruns them when:

- The `setup` field in the YAML changes.
- The cluster was fully torn down (`sky down`) and relaunched.

Setup is NOT rerun when:

- Using `sky exec` (exec never runs setup).
- Using `sky launch` on an existing cluster with the same setup commands.

To force setup to rerun:

```bash
# Tear down and relaunch (setup will run fresh)
sky down mycluster
sky launch mycluster.yaml
```

### Debugging setup failures

When setup fails, the error output is streamed to your terminal. For additional debugging:

```bash
# SSH into the cluster and inspect logs
ssh mycluster

# Setup logs are stored at a path like:
# ~/sky_logs/sky-<timestamp>/setup-<node_id>.log
ls ~/sky_logs/

# Look at the most recent setup log
ls -lt ~/sky_logs/ | head -5
```

You can also directly run setup commands interactively:

```bash
# SSH into the cluster and run commands manually
ssh mycluster
pip install -r requirements.txt  # test interactively
```

---

## 4. Distributed Training Issues

### NCCL errors and node communication failures

**Symptom**: `NCCL error`, `NCCL timeout`, or nodes unable to connect during multi-node training.

**Common causes and solutions**:

1. **Firewall blocking inter-node traffic**: Ensure security groups or firewall rules allow all traffic between cluster nodes.

```yaml
# SkyPilot automatically opens ports between nodes in the same cluster.
# If you still see issues, check cloud-specific firewall settings.
```

2. **Wrong network interface**: NCCL may pick the wrong network interface.

```yaml
run: |
  export NCCL_SOCKET_IFNAME=eth0
  # Or for InfiniBand:
  # export NCCL_IB_HCA=mlx5

  torchrun --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --nnodes=$SKYPILOT_NUM_NODES \
    --node_rank=$SKYPILOT_NODE_RANK \
    --master_addr=$(echo $SKYPILOT_NODE_IPS | head -n1) \
    --master_port=12345 \
    train.py
```

3. **NCCL debug logging**: Enable verbose NCCL logging to diagnose:

```yaml
run: |
  export NCCL_DEBUG=INFO
  export NCCL_DEBUG_SUBSYS=ALL
  torchrun ...
```

### torchrun failures

**Symptom**: `torchrun` fails with connection refused, wrong master address, or port conflicts.

**Correct usage with SkyPilot environment variables**:

```yaml
num_nodes: 2

resources:
  accelerators: H100:8

run: |
  torchrun \
    --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
    --nnodes=$SKYPILOT_NUM_NODES \
    --node_rank=$SKYPILOT_NODE_RANK \
    --master_addr=$(echo $SKYPILOT_NODE_IPS | head -n1) \
    --master_port=12345 \
    train.py
```

**Key environment variables set by SkyPilot**:

| Variable | Description |
|----------|-------------|
| `SKYPILOT_NODE_RANK` | Rank of the current node (0-indexed) |
| `SKYPILOT_NODE_IPS` | Newline-separated list of all node IPs |
| `SKYPILOT_NUM_NODES` | Total number of nodes |
| `SKYPILOT_NUM_GPUS_PER_NODE` | Number of GPUs on the current node |

**Port conflicts**: If port 12345 is in use, pick a different `--master_port`:

```yaml
run: |
  torchrun --master_port=29500 ...
```

### DeepSpeed configuration issues

**Symptom**: DeepSpeed fails to initialize, hangs, or crashes.

```yaml
run: |
  # Use SkyPilot env vars for DeepSpeed
  deepspeed \
    --num_nodes=$SKYPILOT_NUM_NODES \
    --num_gpus=$SKYPILOT_NUM_GPUS_PER_NODE \
    --node_rank=$SKYPILOT_NODE_RANK \
    --master_addr=$(echo $SKYPILOT_NODE_IPS | head -n1) \
    --master_port=29500 \
    --hostfile="" \
    train.py --deepspeed ds_config.json
```

Common DeepSpeed issues:
- **Hostfile conflicts**: Pass `--hostfile=""` to prevent DeepSpeed from reading `/etc/deepspeed/hostfile`.
- **SSH between nodes**: SkyPilot automatically configures passwordless SSH between nodes. If DeepSpeed launcher needs it, it should work out of the box.

### OOM errors (GPU and CPU)

**GPU OOM** (`CUDA out of memory`):

```yaml
run: |
  # Reduce batch size
  python train.py --batch_size 4

  # Enable gradient checkpointing
  python train.py --gradient_checkpointing

  # Use mixed precision
  python train.py --bf16
```

Request a larger GPU if needed:

```yaml
resources:
  accelerators: A100-80GB:8  # 80GB instead of 40GB
```

**CPU OOM** (`Killed` or `oom-kill`):

```yaml
resources:
  # Request more memory
  memory: 128+
  # Or use a larger instance
  instance_type: p4d.24xlarge
```

For data loading OOM, reduce number of workers:

```python
dataloader = DataLoader(dataset, num_workers=2)  # instead of 8
```

### Hung training (NCCL timeout)

**Symptom**: Training hangs and eventually times out with `Watchdog caught collective operation timeout` or NCCL timeout errors.

**Solutions**:

```yaml
run: |
  # Increase NCCL timeout (default is often 30 minutes)
  export NCCL_TIMEOUT=3600000  # 1 hour in milliseconds

  # For PyTorch distributed, increase timeout in init_process_group
  # In your training script:
  # dist.init_process_group(..., timeout=timedelta(hours=1))

  # Debug: check if all nodes are reachable
  echo "Node rank: $SKYPILOT_NODE_RANK"
  echo "All IPs: $SKYPILOT_NODE_IPS"

  torchrun ...
```

Common causes of hung training:
- **Uneven data**: Ensure all nodes process the same number of batches per epoch. Uneven data causes one node to finish first and wait forever.
- **Deadlock in collectives**: Check that all ranks call the same collective operations in the same order.
- **Network issues**: Try `NCCL_P2P_DISABLE=1` to fall back to network-based communication.

---

## 5. File Mount and Storage Issues

### Mount failures

**Symptom**: File mounts fail with permission errors or "bucket does not exist."

**Solutions**:

```yaml
file_mounts:
  # For existing buckets, ensure credentials have read access
  /data:
    source: s3://my-bucket
    mode: MOUNT

  # For local files, ensure the path exists
  /remote/code: ./local/code
```

- **Bucket does not exist**: Create the bucket first, or let SkyPilot create it by specifying `name` and `store` instead of `source`.
- **Permission denied on bucket**: Ensure your cloud credentials have `s3:GetObject` / `storage.objects.get` permissions.

### Slow file sync (large workdir)

**Symptom**: `sky launch` or `sky exec` takes a long time syncing files.

**Solutions**:

```bash
# Exclude large files/directories with .gitignore
# SkyPilot respects .gitignore in your workdir

# Or use a .skyignore file (same syntax as .gitignore)
echo "data/" >> .skyignore
echo "*.bin" >> .skyignore
echo "wandb/" >> .skyignore
```

For very large datasets, use cloud storage mounts instead of workdir:

```yaml
file_mounts:
  /data:
    source: s3://my-dataset-bucket
    mode: MOUNT
```

### Data persistence: stop/start vs down

**Important distinctions**:

| Operation | Disk data | File mounts | Cloud storage |
|-----------|-----------|-------------|---------------|
| `sky stop` / `sky launch` (restart) | Preserved | Re-synced | Preserved |
| `sky down` / `sky launch` (new) | Lost | Re-synced | Preserved |
| `sky exec` | Preserved | Re-synced | Preserved |

To persist data across `sky down`, write to cloud storage:

```yaml
file_mounts:
  /checkpoints:
    source: s3://my-checkpoints
    mode: MOUNT
```

### Storage mounting modes

| Mode | Behavior | Use case |
|------|----------|----------|
| `MOUNT` | Streams data on access (FUSE mount). Default. | Large datasets you read sequentially |
| `COPY` | Downloads all data at setup time | Small datasets for fast random access |
| `MOUNT_CACHED` | FUSE mount with local caching via rclone | Repeated access to same files |

```yaml
file_mounts:
  /data-stream:
    source: s3://my-bucket
    mode: MOUNT

  /data-local:
    source: s3://my-bucket/subset
    mode: COPY

  /data-cached:
    source: s3://my-bucket
    mode: MOUNT_CACHED
```

---

## 6. Managed Job Issues

### Job stuck in PENDING

**Symptom**: `sky jobs queue` shows a job stuck in PENDING.

**Causes**:
- The jobs controller is still being provisioned. The first managed job launch creates a controller VM.
- Insufficient quota for both the controller and the job's requested resources.

**Solutions**:

```bash
# Check controller status
sky status -o json

# View controller provisioning logs
sky jobs logs --controller <job_id>

# If controller is stuck, tear it down and retry
sky down sky-jobs-controller-<user_hash> -p -y
sky jobs launch myjob.yaml
```

### Recovery not working

**Symptom**: Spot instance is preempted but the job does not recover.

**Checklist**:
1. Ensure `use_spot: true` and `job_recovery` is set:

```yaml
resources:
  use_spot: true
  job_recovery: eager_next_region
  # Options: eager_next_region (default for spot), failover, none
```

2. If `job_recovery: none` is set (or the task uses on-demand instances), recovery is disabled by design.

3. Check controller logs for recovery errors:

```bash
sky jobs logs --controller <job_id>
```

### Checkpoint not found after recovery

**Symptom**: Job recovers on a new VM but cannot find the checkpoint.

**Solution**: Checkpoints must be saved to persistent cloud storage, not local disk (local disk is lost on preemption).

```yaml
file_mounts:
  /checkpoints:
    source: s3://my-checkpoints-bucket
    mode: MOUNT

run: |
  python train.py \
    --checkpoint_dir /checkpoints \
    --resume_from_checkpoint
```

Ensure your training script:
- Saves checkpoints to `/checkpoints` (the mounted bucket path).
- Resumes from the latest checkpoint on startup.

### Controller errors

```bash
# View controller (provisioning/recovery) logs for a job
sky jobs logs --controller <job_id>

# View the job's own stdout/stderr logs
sky jobs logs <job_id>
```

### Canceling stuck jobs

```bash
# Cancel a specific job by ID
sky jobs cancel <job_id>

# Cancel a job by name
sky jobs cancel -n my-job-name

# Cancel all jobs
sky jobs cancel -a

# If cancel hangs, force teardown the controller
sky down sky-jobs-controller-<user_hash> -p -y
```

---

## 7. SkyServe Issues

### Service not accessible (readiness probe failing)

**Symptom**: `sky serve status` shows replicas but the service endpoint returns errors, or replicas stay in PROVISIONING/INITIALIZING.

**Solutions**:

1. Check that your readiness probe path and port are correct:

```yaml
service:
  readiness_probe:
    path: /health
    # The port should match what your server listens on
  replicas: 2

resources:
  ports: 8080

run: |
  python -m vllm.entrypoints.openai.api_server \
    --port 8080 --host 0.0.0.0 ...
```

2. Check replica logs for startup errors:

```bash
# View logs for a specific replica
sky serve logs my-service 1

# View controller logs
sky serve logs --controller my-service
```

3. Ensure the server binds to `0.0.0.0`, not `127.0.0.1`.

### Replicas not scaling

**Symptom**: Autoscaler is not adding or removing replicas.

Check your autoscaling config:

```yaml
service:
  replica_policy:
    min_replicas: 1
    max_replicas: 4
    target_qps_per_replica: 10
  readiness_probe:
    path: /health
```

- If `min_replicas == max_replicas`, autoscaling is effectively disabled.
- The autoscaler reacts to QPS. If `target_qps_per_replica` is not set, replicas stay at `min_replicas`.

Check autoscaler decisions:

```bash
sky serve logs --load-balancer my-service
```

### Update failures

```bash
# Rolling update (default, no downtime)
sky serve update my-service new_config.yaml

# Blue-green update (spins up new replicas before tearing down old ones)
sky serve update --mode blue_green my-service new_config.yaml
```

If an update is stuck, check logs:

```bash
sky serve logs --controller my-service
```

### Getting the endpoint URL

```bash
# Show the endpoint for a service
sky serve status --endpoint my-service

# Show all services and their endpoints
sky serve status
```

---

## 8. SSH and Access Issues

### Cannot SSH into cluster

**Symptom**: `ssh mycluster` fails with connection refused or key errors.

**Solutions**:

```bash
# Refresh cluster status to update SSH config
sky status --refresh

# SkyPilot writes SSH config to ~/.ssh/sky-ssh-config
# If your SSH client doesn't include it, add to ~/.ssh/config:
#   Include ~/.ssh/sky-ssh-config

# Check if the cluster is UP
sky status
```

If the cluster shows UP but SSH still fails:
- **Security group/firewall**: Ensure port 22 is open to your IP.
- **Key issues**: SkyPilot manages keys automatically. If keys are corrupted, tear down and relaunch:

```bash
sky down mycluster -p
sky launch mycluster.yaml
```

### Port forwarding

To access services running on the cluster (e.g., Jupyter, TensorBoard):

```bash
# Forward local port 8888 to cluster port 8888
ssh -L 8888:localhost:8888 mycluster

# Forward multiple ports
ssh -L 8888:localhost:8888 -L 6006:localhost:6006 mycluster
```

Or open ports directly in the YAML (creates cloud firewall rules):

```yaml
resources:
  ports: 8888
```

Then access via the cluster's public IP (shown in `sky status`).

### VSCode Remote SSH setup

1. Install the "Remote - SSH" extension in VSCode.
2. SkyPilot automatically configures `~/.ssh/sky-ssh-config`. Ensure VSCode includes it:

Add to your `~/.ssh/config`:

```
Include ~/.ssh/sky-ssh-config
```

3. In VSCode, open the Remote SSH command palette and connect to `mycluster`.

### SSH config conflicts

**Symptom**: SSH works via `ssh mycluster` but not from other tools.

SkyPilot writes SSH configs to `~/.ssh/sky-ssh-config`. Ensure this file is included in `~/.ssh/config`:

```
Include ~/.ssh/sky-ssh-config
```

If you have conflicting Host entries, SkyPilot's entries use the cluster name as the Host alias. Rename clusters to avoid conflicts.

---

## 9. API Server Issues

### Server not starting

**Symptom**: `sky api start` fails or the server crashes immediately.

```bash
# Check server status
sky api status -o json

# Check server info
sky api info -o json

# Stop and restart
sky api stop
sky api start

# Check server logs for errors
cat ~/.sky/api_server/logs/server.log
```

Common causes:
- **Port conflict**: Another process on port 46580. Kill it or change the port.
- **Stale state**: Remove stale state and restart:

```bash
sky api stop
sky api start
```

### Connection refused

**Symptom**: CLI commands fail with connection refused errors.

```bash
# Check if the server is running
sky api info -o json

# Restart the server
sky api stop
sky api start
```

If connecting to a remote server:

```bash
# Verify the endpoint is correct
sky api login -e https://your-api-server:46580

# Check network connectivity
curl https://your-api-server:46580/api/health
```

### Remote server authentication

```bash
# Login to a remote API server (browser-based OAuth)
sky api login -e https://api.example.com

# Login with a service account token
sky api login -e https://api.example.com --token sky_abc123...

# Logout from remote server
sky api logout
```

### Stale or hung requests

**Symptom**: A CLI command hangs or a previous request is blocking.

```bash
# List active requests
sky api status -o json

# Cancel a stale request by its ID
sky api cancel <request_id>
```

---

## 10. Common Error Messages

| Error Message | Cause | Solution |
|---------------|-------|----------|
| `No launchable resource found for task` | No cloud has the requested resources available or enabled | Relax resource requirements. Run `sky gpus list --all` to check availability. Use `any_of` for fallbacks. |
| `QUOTA_EXCEEDED` / `quotaExceeded` | Cloud account quota is too low for the requested resources | Request a quota increase in the cloud console. For GCP `GPUS_ALL_REGIONS`, request a global GPU quota increase. |
| `Cluster is in INIT state` | Cluster provisioning did not complete successfully | Run `sky down -p <cluster>` to force cleanup, then relaunch with `sky launch`. |
| `Permission denied (publickey)` | SSH key mismatch or missing key | Run `sky down -p <cluster>` and relaunch. SkyPilot regenerates keys on new clusters. |
| `ResourceExhausted` | Cloud provider capacity is exhausted in the requested region | Try a different region/zone, use `any_of` with multiple regions, or try a different cloud. |
| `NCCL timeout` / `Watchdog caught collective operation timeout` | Distributed training nodes cannot communicate | Check firewall rules between nodes. Set `NCCL_SOCKET_IFNAME=eth0`. Enable `NCCL_DEBUG=INFO` for diagnostics. |
| `CUDA out of memory` | GPU memory insufficient for the workload | Reduce batch size, enable gradient checkpointing, use mixed precision, or request a larger GPU. |
| `No module named 'xxx'` | Package not installed, or setup did not run | Check your `setup` commands. Tear down and relaunch to force setup rerun. SSH in and verify the environment. |
| `Connection refused` (API server) | SkyPilot API server is not running | Run `sky api start`. Check `sky api status` for details. |
| `FileNotFoundError` on file mounts | Source path does not exist or bucket name is wrong | Verify the local path or bucket name. For buckets, check `aws s3 ls` / `gsutil ls`. |
| `sky.exceptions.ResourcesUnavailableError` | The optimizer could not find any cloud/region that satisfies the resource request | Check `sky gpus list` for the accelerator. Broaden your `infra` or add `any_of` fallbacks. |
| `Setup failed with return code` | A command in the `setup` block exited with a non-zero code | SSH into the cluster and inspect `~/sky_logs/` for the setup log. Run setup commands manually to debug. |
| `Cluster <name> does not exist` | The cluster was already torn down or never created | Run `sky status` to see active clusters. Relaunch with `sky launch`. |
| `sky.exceptions.NotSupportedError` | The requested feature is not supported on the chosen cloud | Check SkyPilot docs for cloud-specific feature support. Try a different cloud. |

### Debugging tips for any error

```bash
# Enable debug logging for more detail
export SKYPILOT_DEBUG=1
sky launch mycluster.yaml

# Check cluster and job status
sky status -o json
sky jobs queue -o json

# Check the full logs for a cluster
sky logs mycluster

# Check managed job logs
sky jobs logs <job_id>
sky jobs logs --controller <job_id>

# Check serve logs
sky serve logs my-service 1
sky serve logs --controller my-service

# Refresh cluster status from cloud
sky status --refresh -o json
```
