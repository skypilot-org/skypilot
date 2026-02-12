.. _recipes:

SkyPilot Recipes
================

This guide provides a comprehensive collection of practical recipes and patterns for common SkyPilot use cases. Each recipe is a ready-to-use example that you can adapt to your needs.

.. tip::
   New to SkyPilot? Start with the :ref:`Quickstart <quickstart>` and :ref:`Tutorial <tutorial>` first.

.. contents:: Table of Contents
   :local:
   :depth: 2

Basic Recipes
-------------

Hello World
~~~~~~~~~~~

The simplest possible SkyPilot task - launch a cluster and run a command.

**Recipe**:

.. code-block:: yaml

   name: hello-world

   resources:
     cpus: 2+

   run: |
     echo "Hello from SkyPilot!"
     echo "Running on: $(hostname)"
     echo "Current directory: $(pwd)"

**Launch**:

.. code-block:: bash

   sky launch -c hello hello-world.yaml

**Use case**: Quick tests, learning SkyPilot, running simple scripts.

Minimal GPU Task
~~~~~~~~~~~~~~~~

Launch a GPU cluster and verify GPU availability.

**Recipe**:

.. code-block:: yaml

   name: gpu-check

   resources:
     accelerators: V100:1

   run: |
     nvidia-smi
     echo "GPU detected: $CUDA_VISIBLE_DEVICES"

**Launch**:

.. code-block:: bash

   sky launch -c gpu-test gpu-check.yaml

**Use case**: Verify GPU setup, test CUDA installation, benchmark GPUs.

.. tip::
   Use ``accelerators: V100`` as a shorthand for ``V100:1`` (one GPU).

Specific Cloud Provider
~~~~~~~~~~~~~~~~~~~~~~~

Launch on a specific cloud provider and region.

**Recipe**:

.. code-block:: yaml

   name: aws-task

   resources:
     infra: aws/us-west-2
     cpus: 4+
     memory: 16+

   run: |
     aws --version
     curl http://169.254.169.254/latest/meta-data/placement/availability-zone

**Variations**:

.. code-block:: yaml

   # GCP specific
   resources:
     infra: gcp/us-central1

   # Azure specific
   resources:
     infra: azure/eastus

   # Kubernetes
   resources:
     infra: kubernetes

**Use case**: Region-specific compliance, data locality, testing multi-cloud deployments.

Data & Storage Recipes
----------------------

Local Directory Sync
~~~~~~~~~~~~~~~~~~~~

Sync local code and data to remote clusters.

**Recipe**:

.. code-block:: yaml

   name: local-sync

   workdir: .  # Sync current directory

   file_mounts:
     /data: ~/local-datasets
     /configs: ./config-files

   run: |
     ls -la ~/sky_workdir  # workdir synced here
     ls -la /data
     ls -la /configs

**Launch**:

.. code-block:: bash

   sky launch -c sync-test local-sync.yaml

**Use case**: Development workflows, uploading datasets, syncing configuration files.

.. note::
   ``workdir`` is synced to ``~/sky_workdir`` on the cluster. The ``setup`` and ``run`` commands execute in this directory.

S3/GCS Bucket Mounting
~~~~~~~~~~~~~~~~~~~~~~

Mount cloud storage buckets for reading data.

**Recipe**:

.. code-block:: yaml

   name: s3-mount

   file_mounts:
     # Mount existing S3 bucket (read-only for public buckets)
     /datasets:
       source: s3://my-dataset-bucket
       mode: MOUNT

     # Mount GCS bucket
     /models:
       source: gs://my-model-bucket
       mode: MOUNT

   run: |
     ls /datasets
     ls /models
     python train.py --data /datasets --model-dir /models

**Key differences**:

- **MOUNT mode**: Streams files on-demand, saves disk space
- **COPY mode**: Copies all files to local disk at launch (default)

**Use case**: Large datasets, shared data across multiple jobs, cloud-native storage.

Creating Persistent Storage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a persistent bucket that survives cluster deletion.

**Recipe**:

.. code-block:: yaml

   name: persistent-outputs

   file_mounts:
     # Create a new bucket and mount it
     /outputs:
       name: my-experiment-outputs  # Must be globally unique
       store: s3  # or gcs
       mode: MOUNT
       persistent: true

   run: |
     python train.py --output-dir /outputs/run-$(date +%s)
     echo "Outputs saved to /outputs"

**Check storage**:

.. code-block:: bash

   sky storage ls
   sky storage delete my-experiment-outputs  # Cleanup when done

**Use case**: Saving experiment results, checkpoints, logs to cloud storage that persists after cluster shutdown.

Multi-Source Storage
~~~~~~~~~~~~~~~~~~~~

Upload multiple local directories to the same bucket.

**Recipe**:

.. code-block:: yaml

   name: multi-source

   file_mounts:
     /workspace:
       name: my-multi-source-bucket
       source:
         - ~/code/src
         - ~/code/configs
         - ~/data/samples
       store: s3
       mode: COPY

   run: |
     ls -R /workspace

**Use case**: Combining code, configs, and data from different local locations.

Copy Large Public Datasets
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Efficiently copy large public datasets using COPY mode.

**Recipe**:

.. code-block:: yaml

   name: dataset-copy

   resources:
     disk_size: 512  # GB

   file_mounts:
     /dataset:
       source: s3://large-public-dataset
       mode: COPY  # Copy to local disk for faster access

   setup: |
     echo "Dataset size:"
     du -sh /dataset

   run: |
     python preprocess.py --input /dataset --output /processed

**Use case**: Preprocessing large datasets, training with frequent file access.

Development Recipes
-------------------

Interactive Development
~~~~~~~~~~~~~~~~~~~~~~~

Launch a cluster for interactive development with Jupyter.

**Recipe**:

.. code-block:: yaml

   name: jupyter-dev

   resources:
     accelerators: T4:1
     ports: 8888

   setup: |
     pip install jupyter jupyterlab matplotlib numpy pandas torch

   run: |
     jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root

**Launch and connect**:

.. code-block:: bash

   sky launch -c dev jupyter-dev.yaml

   # Get the URL with token
   sky logs dev | grep "http://127.0.0.1:8888"

   # Or SSH directly
   ssh dev

**Use case**: Interactive ML development, debugging, exploratory data analysis.

.. seealso::
   See :ref:`interactive-development` for more details on interactive workflows.

VS Code Remote Development
~~~~~~~~~~~~~~~~~~~~~~~~~~

Use VS Code's Remote-SSH extension with SkyPilot clusters.

**Recipe**:

.. code-block:: yaml

   name: vscode-remote

   resources:
     accelerators: V100:1

   setup: |
     # Install dependencies for your project
     pip install torch transformers datasets

   run: |
     echo "Ready for VS Code Remote-SSH"
     echo "Connect using: ssh vscode-remote"

**Setup VS Code**:

.. code-block:: bash

   # Launch cluster
   sky launch -c vscode-remote vscode-remote.yaml

   # In VS Code:
   # 1. Install "Remote - SSH" extension
   # 2. Press F1, select "Remote-SSH: Connect to Host"
   # 3. Enter: vscode-remote
   # 4. Open your workspace at ~/sky_workdir

**Use case**: Full IDE experience, debugging with breakpoints, integrated terminal.

Docker Container Development
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Develop using custom Docker containers.

**Recipe**:

.. code-block:: yaml

   name: docker-dev

   resources:
     accelerators: A100:1

   setup: |
     # Build custom Docker image
     sudo docker build -t my-ml-env:latest - <<'EOF'
     FROM pytorch/pytorch:2.0.1-cuda11.8-cudnn8-runtime
     RUN pip install transformers datasets accelerate wandb
     WORKDIR /workspace
     EOF

   run: |
     sudo docker run --gpus all --rm -it \
       -v $(pwd):/workspace \
       my-ml-env:latest \
       python train.py

**Use case**: Reproducible environments, complex dependencies, GPU-accelerated containers.

.. seealso::
   See :ref:`docker-containers` for more Docker examples.

Using Environment Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pass configuration via environment variables.

**Recipe**:

.. code-block:: yaml

   name: env-vars

   envs:
     MODEL_NAME: bert-base-uncased
     BATCH_SIZE: 32
     LEARNING_RATE: 2e-5
     WANDB_PROJECT: my-experiments
     HF_CACHE_DIR: /tmp/hf_cache

   run: |
     echo "Training ${MODEL_NAME}"
     echo "Batch size: ${BATCH_SIZE}"
     python train.py \
       --model $MODEL_NAME \
       --batch-size $BATCH_SIZE \
       --lr $LEARNING_RATE

**Override at launch**:

.. code-block:: bash

   sky launch -c exp1 env-vars.yaml --env BATCH_SIZE=64 --env LEARNING_RATE=1e-5

**Use case**: Parameterized experiments, configuration management, hyperparameter sweeps.

Training Recipes
----------------

Single-Node GPU Training
~~~~~~~~~~~~~~~~~~~~~~~~

Train a model on a single GPU.

**Recipe**:

.. code-block:: yaml

   name: single-gpu-train

   resources:
     accelerators: V100:1
     disk_size: 256

   workdir: .

   file_mounts:
     /checkpoints:
       name: my-checkpoints-bucket
       mode: MOUNT
       store: s3

   setup: |
     pip install torch torchvision transformers datasets accelerate

   run: |
     python train.py \
       --data-dir /tmp/data \
       --checkpoint-dir /checkpoints \
       --epochs 10 \
       --batch-size 32

**Use case**: Small to medium models, prototyping, single-GPU fine-tuning.

Multi-GPU Training (Single Node)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Train on multiple GPUs using PyTorch DDP.

**Recipe**:

.. code-block:: yaml

   name: multi-gpu-train

   resources:
     accelerators: A100:8
     disk_size: 512

   setup: |
     pip install torch torchvision transformers accelerate

   run: |
     # PyTorch DDP with torchrun
     torchrun --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
       train.py \
       --distributed

**Use case**: Large models, faster training, 8-GPU nodes.

.. tip::
   SkyPilot sets ``SKYPILOT_NUM_GPUS_PER_NODE`` automatically based on your resource spec.

Multi-Node Distributed Training
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scale training across multiple nodes with PyTorch DDP.

**Recipe**:

.. code-block:: yaml

   name: multi-node-train

   num_nodes: 4

   resources:
     accelerators: A100:8

   setup: |
     pip install torch torchvision transformers accelerate

   run: |
     # Multi-node PyTorch DDP
     torchrun \
       --nnodes=$SKYPILOT_NUM_NODES \
       --nproc_per_node=$SKYPILOT_NUM_GPUS_PER_NODE \
       --master_addr=$SKYPILOT_NODE_IPS_0 \
       --master_port=8008 \
       --node_rank=$SKYPILOT_NODE_RANK \
       train.py \
       --distributed

**SkyPilot environment variables**:

- ``SKYPILOT_NUM_NODES``: Total number of nodes
- ``SKYPILOT_NODE_RANK``: Current node rank (0, 1, 2, ...)
- ``SKYPILOT_NODE_IPS_0``: IP of head node
- ``SKYPILOT_NUM_GPUS_PER_NODE``: GPUs per node

**Use case**: Very large models (70B+ parameters), faster training of medium models.

.. seealso::
   See :ref:`distributed-jobs` for more details on multi-node training.

Training with Checkpointing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Automatically save and resume from checkpoints.

**Recipe**:

.. code-block:: yaml

   name: checkpoint-training

   resources:
     accelerators: V100:1

   file_mounts:
     /checkpoints:
       name: my-checkpoint-storage
       mode: MOUNT
       store: s3
       persistent: true

   setup: |
     pip install torch transformers

   run: |
     # Training script should handle checkpoint loading
     python train.py \
       --checkpoint-dir /checkpoints/experiment-1 \
       --resume-from-latest

**Training script pattern**:

.. code-block:: python

   import os
   import glob

   checkpoint_dir = "/checkpoints/experiment-1"
   os.makedirs(checkpoint_dir, exist_ok=True)

   # Resume from latest checkpoint
   checkpoints = glob.glob(f"{checkpoint_dir}/checkpoint-*.pt")
   if checkpoints:
       latest = max(checkpoints, key=os.path.getctime)
       model.load_state_dict(torch.load(latest))
       print(f"Resumed from {latest}")

   # Save checkpoints periodically
   for epoch in range(num_epochs):
       train_one_epoch(model)
       torch.save(model.state_dict(),
                  f"{checkpoint_dir}/checkpoint-epoch-{epoch}.pt")

**Use case**: Long-running training, recovery from preemptions, experiment continuity.

.. seealso::
   See :ref:`checkpointing` for automatic checkpointing with managed jobs.

Fine-tuning Hugging Face Models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Fine-tune transformer models from Hugging Face.

**Recipe**:

.. code-block:: yaml

   name: hf-finetune

   resources:
     accelerators: A100:1

   envs:
     MODEL_NAME: meta-llama/Llama-2-7b-hf
     DATASET: imdb
     HF_TOKEN: ${HF_TOKEN}  # Set locally: export HF_TOKEN=hf_xxx

   setup: |
     pip install torch transformers datasets accelerate bitsandbytes

     # Login to Hugging Face if needed for gated models
     if [ ! -z "$HF_TOKEN" ]; then
       pip install huggingface_hub
       huggingface-cli login --token $HF_TOKEN
     fi

   run: |
     python - <<EOF
     from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer
     from datasets import load_dataset

     # Load model and tokenizer
     model = AutoModelForCausalLM.from_pretrained("${MODEL_NAME}")
     tokenizer = AutoTokenizer.from_pretrained("${MODEL_NAME}")

     # Load dataset
     dataset = load_dataset("${DATASET}")

     # Training
     trainer = Trainer(
         model=model,
         args=TrainingArguments(
             output_dir="/tmp/outputs",
             num_train_epochs=3,
             per_device_train_batch_size=4,
         ),
         train_dataset=dataset["train"],
     )
     trainer.train()
     EOF

**Use case**: LLM fine-tuning, transfer learning, domain adaptation.

Cost Optimization Recipes
--------------------------

Using Spot Instances
~~~~~~~~~~~~~~~~~~~~

Save 60-80% on costs using spot/preemptible instances.

**Recipe**:

.. code-block:: yaml

   name: spot-training

   resources:
     accelerators: V100:1
     use_spot: true

   file_mounts:
     /checkpoints:
       name: spot-checkpoints
       mode: MOUNT
       store: s3

   setup: |
     pip install torch transformers

   run: |
     # Save checkpoints frequently for spot recovery
     python train.py \
       --checkpoint-dir /checkpoints \
       --checkpoint-every 100

**Launch**:

.. code-block:: bash

   sky launch -c spot-job spot-training.yaml

**Use case**: Cost-sensitive workloads, fault-tolerant jobs, training with checkpointing.

.. warning::
   Spot instances can be preempted. Always save checkpoints to persistent storage.

Managed Spot Jobs
~~~~~~~~~~~~~~~~~

Automatically recover from spot preemptions.

**Recipe**:

.. code-block:: yaml

   name: managed-spot

   resources:
     accelerators: V100:1
     use_spot: true
     job_recovery: spot

   file_mounts:
     /checkpoints:
       name: managed-spot-checkpoints
       mode: MOUNT

   setup: |
     pip install torch transformers

   run: |
     python train.py \
       --checkpoint-dir /checkpoints \
       --resume-from-latest

**Launch as managed job**:

.. code-block:: bash

   sky jobs launch managed-spot.yaml

   # Monitor
   sky jobs queue
   sky jobs logs <job-id>

**Use case**: Long-running training, production workloads on spot instances, automatic fault recovery.

.. seealso::
   See :ref:`managed-jobs` for more details on managed jobs.

Auto-stop Idle Clusters
~~~~~~~~~~~~~~~~~~~~~~~~

Automatically stop clusters when idle to save costs.

**Recipe**:

.. code-block:: yaml

   name: auto-stop

   resources:
     accelerators: T4:1
     autostop:
       idle_minutes: 10  # Stop after 10 minutes idle
       wait_for: none    # Don't wait for anything

   setup: |
     pip install jupyter

   run: |
     jupyter lab --ip=0.0.0.0 --port=8888 --no-browser

**Use case**: Development clusters, interactive work, cost control.

.. seealso::
   See :ref:`auto-stop` for more auto-stop configurations.

Multi-Cloud Failover
~~~~~~~~~~~~~~~~~~~~

Try multiple clouds to maximize availability and minimize cost.

**Recipe**:

.. code-block:: yaml

   name: failover-training

   resources:
     accelerators: V100:1

     # Try these in order
     any_of:
       - infra: aws/us-west-2
       - infra: gcp/us-central1
       - infra: azure/eastus

   setup: |
     pip install torch transformers

   run: |
     python train.py

**Launch**:

.. code-block:: bash

   sky launch -c failover failover-training.yaml

SkyPilot will try each cloud in order until it finds available resources.

**Use case**: GPU shortages, multi-cloud strategies, cost optimization.

.. seealso::
   See :ref:`auto-failover` for more failover strategies.

Advanced Recipes
----------------

Custom Docker Images
~~~~~~~~~~~~~~~~~~~~

Use pre-built Docker images for faster setup.

**Recipe**:

.. code-block:: yaml

   name: custom-image

   resources:
     accelerators: A100:1
     image_id: docker:nvcr.io/nvidia/pytorch:23.10-py3

   run: |
     # Image already has PyTorch installed
     python --version
     python -c "import torch; print(f'PyTorch {torch.__version__}')"
     nvidia-smi

**Cloud-specific images**:

.. code-block:: yaml

   # AWS AMI
   resources:
     infra: aws
     image_id: ami-0abcdef1234567890

   # GCP image
   resources:
     infra: gcp
     image_id: projects/my-project/global/images/my-image

**Use case**: Faster cluster launch, pre-configured environments, custom software.

Using TPUs
~~~~~~~~~~

Train on Google Cloud TPUs.

**Recipe**:

.. code-block:: yaml

   name: tpu-training

   resources:
     infra: gcp
     accelerators: tpu-v3-8
     accelerator_args:
       runtime_version: tpu-vm-v4-base

   setup: |
     pip install torch torch_xla cloud-tpu-client

   run: |
     python train_tpu.py

**Use case**: TPU-optimized workloads, JAX/PyTorch XLA training, cost-effective large models.

.. seealso::
   See :ref:`tpu` for comprehensive TPU documentation.

Running on Kubernetes
~~~~~~~~~~~~~~~~~~~~~

Deploy to existing Kubernetes clusters.

**Recipe**:

.. code-block:: yaml

   name: k8s-job

   resources:
     infra: kubernetes
     accelerators: A100:1
     labels:
       team: ml-platform
       project: experiment-1

   setup: |
     pip install torch transformers

   run: |
     python train.py

**Configure Kubernetes context**:

.. code-block:: bash

   # List available contexts
   sky check kubernetes

   # Launch
   sky launch -c k8s-exp k8s-job.yaml

**Use case**: On-premise GPUs, existing K8s infrastructure, enterprise deployments.

.. seealso::
   See :ref:`Kubernetes <kubernetes-overview>` for complete Kubernetes setup.

SSH into Running Pods
~~~~~~~~~~~~~~~~~~~~~

Interactive debugging on Kubernetes pods.

**Recipe**:

.. code-block:: yaml

   name: k8s-debug

   resources:
     infra: kubernetes
     accelerators: V100:1

   setup: |
     pip install torch ipython

   run: |
     echo "Cluster ready. SSH in with: ssh k8s-debug"
     sleep infinity

**Usage**:

.. code-block:: bash

   sky launch -c k8s-debug k8s-debug.yaml

   # SSH into the pod
   ssh k8s-debug

   # Or run commands
   sky exec k8s-debug "python my_script.py"

**Use case**: Debugging K8s deployments, interactive development, troubleshooting.

Private Git Repositories
~~~~~~~~~~~~~~~~~~~~~~~~

Clone private repositories using SSH or HTTPS authentication.

**Recipe (SSH)**:

.. code-block:: yaml

   name: private-repo-ssh

   workdir:
     url: git@github.com:myorg/private-repo.git
     ref: main

   setup: |
     pip install -r requirements.txt

   run: |
     python main.py

**Setup SSH authentication**:

.. code-block:: bash

   # Set SSH key path
   export GIT_SSH_KEY_PATH=~/.ssh/id_ed25519

   # Launch
   sky launch -c private private-repo-ssh.yaml

**Recipe (HTTPS with token)**:

.. code-block:: yaml

   workdir:
     url: https://github.com/myorg/private-repo.git
     ref: main

**Setup HTTPS authentication**:

.. code-block:: bash

   # Set GitHub token
   export GIT_TOKEN=ghp_xxxxxxxxxxxx

   # Launch
   sky launch -c private private-repo-https.yaml

**Use case**: Private company repos, proprietary code, confidential projects.

Custom Network Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Configure ports and network access.

**Recipe**:

.. code-block:: yaml

   name: web-service

   resources:
     cpus: 4+
     ports:
       - 8080
       - 8443
       - 9090-9100

   setup: |
     pip install fastapi uvicorn

   run: |
     uvicorn main:app --host 0.0.0.0 --port 8080

**Access the service**:

.. code-block:: bash

   sky launch -c webapp web-service.yaml

   # Ports are automatically forwarded
   curl http://localhost:8080

**Use case**: Web services, APIs, custom applications, model serving.

.. seealso::
   See :ref:`ports` for more port configuration options.

Using Secrets
~~~~~~~~~~~~~

Securely pass sensitive data to jobs.

**Recipe**:

.. code-block:: yaml

   name: secrets-example

   secrets:
     WANDB_API_KEY: your-wandb-key
     HF_TOKEN: your-hf-token
     DATABASE_PASSWORD: db-password

   envs:
     WANDB_PROJECT: my-project  # Non-secret config

   setup: |
     # Secrets are available as env vars
     pip install wandb
     echo $WANDB_API_KEY | wandb login

   run: |
     python train.py  # Uses WANDB_API_KEY automatically

**Best practices**:

.. code-block:: bash

   # Store secrets in .env file (add to .gitignore!)
   cat > .env <<EOF
   WANDB_API_KEY=xxx
   HF_TOKEN=yyy
   DATABASE_PASSWORD=zzz
   EOF

   # Load and launch
   export $(cat .env | xargs)
   sky launch -c exp secrets-example.yaml

**Use case**: API keys, passwords, tokens, sensitive configuration.

.. warning::
   Never commit secrets to version control. Use environment variables or secret managers.

Job Groups & Pipelines
~~~~~~~~~~~~~~~~~~~~~~

Run multiple related jobs as a pipeline.

**Recipe**:

.. code-block:: yaml

   name: ml-pipeline

   resources:
     accelerators: V100:1

   file_mounts:
     /data:
       name: pipeline-data
       mode: MOUNT
       store: s3

   setup: |
     pip install pandas scikit-learn torch

   run: |
     # Step 1: Data preprocessing
     python preprocess.py --input /data/raw --output /data/processed

     # Step 2: Feature extraction
     python extract_features.py --input /data/processed --output /data/features

     # Step 3: Training
     python train.py --input /data/features --output /data/models

     # Step 4: Evaluation
     python evaluate.py --model /data/models/best.pt --output /data/results

**Use case**: ML pipelines, ETL workflows, multi-stage processing.

.. seealso::
   See :ref:`job-groups` for parallel job execution.

Testing & Debugging Recipes
----------------------------

Dry Run (Local Testing)
~~~~~~~~~~~~~~~~~~~~~~~~

Test YAML configuration without launching.

.. code-block:: bash

   # Check YAML syntax
   sky show my-task.yaml

   # See what resources would be used
   sky show my-task.yaml --cloud aws

   # Estimate cost
   sky cost-estimate my-task.yaml

**Use case**: Validating configs, cost estimation, testing changes.

Quick Cluster Validation
~~~~~~~~~~~~~~~~~~~~~~~~~

Quickly test that a cluster setup works.

**Recipe**:

.. code-block:: yaml

   name: quick-test

   resources:
     accelerators: T4:1

   setup: |
     echo "Setup phase"
     which python
     which pip
     pip --version

   run: |
     echo "Run phase"
     nvidia-smi
     python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"
     df -h
     free -h

**Use case**: Smoke tests, environment validation, debugging setup issues.

Resource Inspection
~~~~~~~~~~~~~~~~~~~

Inspect available resources and cluster status.

**Recipe**:

.. code-block:: yaml

   name: resource-check

   run: |
     echo "=== System Info ==="
     uname -a

     echo -e "\n=== CPU Info ==="
     lscpu | grep -E "Model name|Socket|Core|Thread"

     echo -e "\n=== Memory ==="
     free -h

     echo -e "\n=== Disk ==="
     df -h

     echo -e "\n=== GPU Info ==="
     nvidia-smi

     echo -e "\n=== Network ==="
     ip addr show

     echo -e "\n=== Environment ==="
     env | grep SKYPILOT

     echo -e "\n=== Python ==="
     python --version
     which python

**Use case**: Debugging, resource verification, environment inspection.

Common Patterns & Best Practices
---------------------------------

Excluding Files from Sync
~~~~~~~~~~~~~~~~~~~~~~~~~~

Exclude large files or directories from ``workdir`` sync.

Create ``.skyignore`` in your workdir:

.. code-block:: text

   # .skyignore
   .git
   __pycache__
   *.pyc
   .venv
   venv
   node_modules
   data/
   checkpoints/
   *.weights
   *.pt
   *.safetensors

**Use case**: Faster sync, avoid uploading large files, bandwidth optimization.

.. seealso::
   See :ref:`exclude-uploading-files` for more details.

Separating Setup and Run
~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``setup`` for one-time installation, ``run`` for execution.

**Pattern**:

.. code-block:: yaml

   name: proper-separation

   setup: |
     # One-time setup (cached across sky exec)
     pip install torch transformers datasets
     wget https://example.com/large-model.pt -O /tmp/model.pt

   run: |
     # Actual task (runs every time)
     python train.py --model /tmp/model.pt

**Why?**

- ``setup`` is cached and won't re-run on ``sky exec``
- ``run`` executes every time you run the task
- Faster iterations during development

**Use case**: Development workflows, repeated executions, faster iteration.

Conditional Cloud Logic
~~~~~~~~~~~~~~~~~~~~~~~

Execute different commands based on the cloud provider.

**Pattern**:

.. code-block:: yaml

   name: cloud-conditional

   run: |
     # Detect cloud provider
     if curl -s -m 1 http://169.254.169.254/latest/meta-data/ > /dev/null; then
       echo "Running on AWS"
       export CLOUD=aws
     elif curl -s -m 1 -H "Metadata-Flavor: Google" http://169.254.169.254/computeMetadata/v1/ > /dev/null; then
       echo "Running on GCP"
       export CLOUD=gcp
     else
       echo "Running on other cloud"
       export CLOUD=other
     fi

     python train.py --cloud $CLOUD

**Use case**: Multi-cloud applications, cloud-specific optimizations.

Parameterized Tasks
~~~~~~~~~~~~~~~~~~~

Create reusable task templates with parameters.

**Template** (``train-template.yaml``):

.. code-block:: yaml

   name: ${EXPERIMENT_NAME}

   resources:
     accelerators: ${GPU_TYPE}:${GPU_COUNT}

   envs:
     MODEL_NAME: ${MODEL_NAME}
     BATCH_SIZE: ${BATCH_SIZE}
     LEARNING_RATE: ${LR}

   setup: |
     pip install torch transformers

   run: |
     python train.py \
       --model ${MODEL_NAME} \
       --batch-size ${BATCH_SIZE} \
       --lr ${LEARNING_RATE}

**Usage**:

.. code-block:: bash

   # Set parameters
   export EXPERIMENT_NAME=bert-large-exp1
   export GPU_TYPE=A100
   export GPU_COUNT=4
   export MODEL_NAME=bert-large-uncased
   export BATCH_SIZE=32
   export LR=2e-5

   # Launch
   envsubst < train-template.yaml > train-instance.yaml
   sky launch -c exp1 train-instance.yaml

**Use case**: Experiment management, hyperparameter sweeps, team templates.

Common Troubleshooting
----------------------

Setup Failures
~~~~~~~~~~~~~~

**Problem**: Setup command fails during launch.

**Solution**:

.. code-block:: yaml

   setup: |
     set -e  # Exit on error
     set -x  # Print commands (debugging)

     # Install with error handling
     pip install torch || (echo "PyTorch install failed" && exit 1)

     # Verify installation
     python -c "import torch; assert torch.cuda.is_available()"

**Check logs**:

.. code-block:: bash

   sky logs <cluster-name> --sync-down

Out of Disk Space
~~~~~~~~~~~~~~~~~

**Problem**: Task fails due to insufficient disk space.

**Solution**:

.. code-block:: yaml

   resources:
     disk_size: 512  # Increase to 512 GB
     disk_tier: high  # Use faster SSD

   setup: |
     # Check available space
     df -h

     # Clean up if needed
     pip cache purge
     conda clean --all -y

**Use case**: Large datasets, model downloads, intermediate files.

SSH Connection Issues
~~~~~~~~~~~~~~~~~~~~~

**Problem**: Cannot SSH into cluster.

**Debug**:

.. code-block:: bash

   # Check cluster status
   sky status

   # View detailed status
   sky status --show-all

   # Try SSH with verbose output
   ssh -vvv <cluster-name>

   # Reset SSH config
   sky down <cluster-name>
   sky launch -c <cluster-name> <yaml>

Slow File Syncing
~~~~~~~~~~~~~~~~~

**Problem**: ``workdir`` or ``file_mounts`` sync is very slow.

**Solutions**:

1. **Use .skyignore**:

   .. code-block:: text

      # Exclude large directories
      data/
      checkpoints/
      *.pt

2. **Use cloud storage**:

   .. code-block:: yaml

      file_mounts:
        /large-data:
          source: s3://my-bucket/large-data
          mode: MOUNT  # Stream instead of copy

3. **Reduce workdir size**:

   .. code-block:: yaml

      # Only sync necessary code
      workdir: ./src  # Instead of .

GPU Not Detected
~~~~~~~~~~~~~~~~

**Problem**: CUDA not available in the task.

**Debug**:

.. code-block:: yaml

   run: |
     # Check GPU visibility
     nvidia-smi
     echo "CUDA_VISIBLE_DEVICES: $CUDA_VISIBLE_DEVICES"

     # Check PyTorch CUDA
     python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')"

     # Check CUDA version
     nvcc --version

**Solutions**:

- Ensure ``accelerators`` is specified
- Use matching PyTorch CUDA version
- Check if Docker container has GPU access (``--gpus all``)

Cost Overruns
~~~~~~~~~~~~~

**Problem**: Unexpectedly high cloud costs.

**Prevention**:

.. code-block:: yaml

   resources:
     autostop:
       idle_minutes: 30  # Auto-stop after 30 min idle
     use_spot: true      # Use spot instances

**Monitoring**:

.. code-block:: bash

   # Check running clusters
   sky status

   # Stop all idle clusters
   sky autostop --all 30

   # Delete unused clusters
   sky down --all

.. warning::
   Always configure auto-stop to prevent forgotten clusters from running indefinitely.

Next Steps
----------

Now that you've seen these recipes, explore more:

- :ref:`Examples <examples>` - Full application examples
- :ref:`YAML Specification <yaml-spec>` - Complete YAML reference
- :ref:`CLI Reference <cli>` - All SkyPilot commands
- :ref:`FAQ <faq>` - Common questions

.. tip::
   Join the `SkyPilot Slack <http://slack.skypilot.co>`_ to share your recipes and get help from the community!
