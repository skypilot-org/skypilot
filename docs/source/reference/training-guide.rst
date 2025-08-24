.. _training-guide:

Model Training Guide
=========================

This guide covers the best practices and examples for achieving high performance distributed training using SkyPilot.

Distributed training basics
----------------------------

SkyPilot supports all distributed training frameworks, including but not limited to:

- `PyTorch Distributed Data Parallel (DDP) <https://docs.skypilot.co/en/latest/examples/training/distributed-pytorch.html>`_
- `DeepSpeed <https://docs.skypilot.co/en/latest/examples/training/deepspeed.html>`_
- `Ray Train <https://docs.skypilot.co/en/latest/examples/training/ray.html>`_
- `TensorFlow Distribution Strategies <https://docs.skypilot.co/en/latest/examples/training/distributed-tensorflow.html>`_

The choice of framework depends on your specific needs, but all can be easily configured in a SkyPilot YAML.

Best practices
--------------


Use high-performance networking
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. tab-set::

    .. tab-item:: Nebius with InfiniBand
        :sync: nebius-infiniband-tab

        InfiniBand is a high-throughput, low-latency networking standard. To accelerate ML, AI and high-performance computing (HPC) workloads that you run in your Managed Service for Kubernetes clusters or Nebius VMs, you can interconnect the GPUs using InfiniBand.

        Use ``resources.network_tier: best`` to automatically enable InfiniBand for your clusters and jobs.

        On Nebius managed Kubernetes clusters:

        .. code-block:: yaml
          :emphasize-lines: 4

          resources:
            infra: k8s
            accelerators: H100:8
            network_tier: best

          num_nodes: 2

        On Nebius VMs:

        .. code-block:: yaml
          :emphasize-lines: 4

          resources:
            infra: nebius
            accelerators: H100:8
            network_tier: best

          num_nodes: 2

        See more details in the `Nebius example <https://docs.skypilot.co/en/latest/examples/performance/nebius_infiniband.html>`_.

    .. tab-item:: AWS EFA
        :sync: aws-efa-tab

        AWS Elastic Fabric Adapter (EFA) is a network interface similar to Nvidia Infiniband that enables users to run applications requiring high levels of inter-node communications at scale on AWS. You can enable EFA on AWS HyperPod/EKS clusters with a simple additional setting in your SkyPilot YAML.

        Example configuration:

        .. code-block:: yaml

          config:
            kubernetes:
              pod_config:
                spec:
                  containers:
                    - resources:
                      limits:
                        vpc.amazonaws.com/efa: 4
                      requests:
                        vpc.amazonaws.com/efa: 4

        See `EFA example <https://docs.skypilot.co/en/latest/examples/performance/aws_efa.html>`_ for more details.

    .. tab-item:: GCP GPUDirect-TCPX
        :sync: gcp-gpu-direct-tcpx-tab

        `GPUDirect-TCPX <https://cloud.google.com/compute/docs/gpus/gpudirect>`_ is a high-performance networking technology that enables direct communication between GPUs and network interfaces for `a3-highgpu-8g` or `a3-edgegpu-8g` VMs. You can enable it with the following additional setting in your SkyPilot YAML.

        Example configuration:

        .. code-block:: yaml

          config:
            gcp:
              enable_gpu_direct: true

        See `GPUDirect-TCPX example <https://docs.skypilot.co/en/latest/examples/performance/gcp_gpu_direct_tcpx.html>`_ for more details.


Using Ray with SkyPilot
~~~~~~~~~~~~~~~~~~~~~~~

When running Ray workloads on SkyPilot:

- **Running** ``ray start`` **is fine** - Ray defaults to port 6379, while SkyPilot uses port 6380 internally
- **Never use** ``ray.init(address="auto")`` - It connects to SkyPilot's internal Ray cluster
- **Start Ray head on rank 0, workers on other ranks** - See :ref:`distributed Ray example <dist-jobs>`
- **Never call** ``ray stop`` - It may interfere with SkyPilot operations
- **To kill your Ray cluster**, use ``ray.shutdown()`` in Python or kill the Ray processes directly:
  
  .. code-block:: bash
  
     # Kill all Ray processes started by your application (not SkyPilot's internal Ray)
     pkill -f "ray start --address" 
     # Or kill specific Ray head/worker processes
     pkill -f "ray start --head --port=<your_port>"

Use ``disk_tier: best``
~~~~~~~~~~~~~~~~~~~~~~~
Fast storage is critical for loading and storing data and model checkpoints.
SkyPilot's ``disk_tier`` option supports the fastest available storage with high-performance local SSDs to reduce I/O bottlenecks.

Example configuration:

.. code-block:: yaml

  resources:
    disk_tier: best  # Use highest performance disk tier.
    disk_size: 1000  # GiB. Make the disk size large enough for checkpoints.


Use ``MOUNT_CACHED`` for checkpointing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Cloud buckets with the ``MOUNT_CACHED`` mode provides high performance writing, making it ideal for model checkpoints, logs, and other outputs with fast local writes.

Unlike ``MOUNT`` mode, it supports all write and append operations by using local disk as a cache for the files to be writen to cloud buckets. It can offer up to 9x writing speed of large checkpoints compared to the `MOUNT` mode.

Example configuration:

.. code-block:: yaml

    file_mounts:
      /checkpoints:
        name: my-checkpoint-bucket
        mode: MOUNT_CACHED


For more on the differences between ``MOUNT`` and ``MOUNT_CACHED``, see :ref:`storage mounting modes <storage-mounting-modes>`.

High performance instances
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Choose high performance instances for optimal training performance. SkyPilot allows you to specify instance types with powerful GPUs and high-bandwidth networking:

- Use the latest GPU accelerators (A100, H100, etc.) for faster training
- Consider instances with higher memory bandwidth and higher device memory for large models

Example configuration:

.. code-block:: yaml

  resources:
    accelerators:
      A100:1
      A100-80GB:1
      H100:1

Robust checkpointing for spot instances
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When using spot instances, robust checkpointing is crucial for recovering from preemptions. Your job should follow two key principles:

1. **Write checkpoints periodically** during training to save your progress
2. **Always attempt to load checkpoints on startup**, regardless of whether it's the first run or a restart after preemption

This approach ensures your job can seamlessly resume from where it left off after preemption. On the first run, no checkpoints will exist, but on subsequent restarts, your job will automatically recover its state.

Basic checkpointing
^^^^^^^^^^^^^^^^^^^^

Saving to the bucket is easy -- simply save to the mounted directory ``/checkpoints`` specified above as if it is a local disk.


.. code-block:: python

    def save_checkpoint(step: int, model: torch.nn.Module):
        # save checkpoint to local disk with step number
        torch.save(model.state_dict(), f'/checkpoints/model_{step}.pt')

To make loading checkpoint robust against preemptions and incomplete checkpoitns, here is the recipe:

- Always try loading from the latest checkpoint first
- If the latest checkpoint is found to be corrupted or incomplete,  fallback to earlier checkpoints

Here's a simplified example showing the core concepts for :code:`torch.save`:



.. code-block:: python

    def load_checkpoint(save_dir: str='/checkpoints'):
        try:
            # Find all checkpoints, sorted by step (newest first)
            checkpoints = sorted(
                [f for f in Path(save_dir).glob("checkpoint_*.pt")],
                key=lambda x: int(x.stem.split('_')[-1]),
                reverse=True
            )

            # Try each checkpoint from newest to oldest
            for checkpoint in checkpoints:
                try:
                    step = int(checkpoint.stem.split('_')[-1])
                    result = load_checkpoint(checkpoint) # need to fill in
                    return result
                except Exception as e:
                    logger.warning(f"Failed to load checkpoint {step}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Failed to find checkpoints: {e}")
            return None

Robust checkpointing with error handling
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
For a complete implementation with additional features like custom prefixes, extended metadata, and more detailed error handling, see the code below:

.. dropdown:: Full Implementation
    :animate: fade-in-slide-down

    .. code-block:: python

        from datetime import datetime
        import functools
        import json
        import logging
        import os
        from pathlib import Path
        from typing import Any, Callable, Dict, Optional, TypeVar, Union

        import torch

        logger = logging.getLogger(__name__)

        T = TypeVar('T')

        def save_checkpoint(
            save_dir: str,
            max_checkpoints: int = 5,
            checkpoint_prefix: str = "checkpoint",
        ):
            """
            Decorator for saving checkpoints with fallback mechanism.

            Args:
                save_dir: Directory to save checkpoints
                max_checkpoints: Maximum number of checkpoints to keep
                checkpoint_prefix: Prefix for checkpoint files

            Examples:
                # Basic usage with a simple save function
                @save_checkpoint(save_dir="checkpoints")
                def save_model(step: int, model: torch.nn.Module):
                    torch.save(model.state_dict(), f"checkpoints/model_{step}.pt")

                # With custom save function that includes optimizer
                @save_checkpoint(save_dir="checkpoints")
                def save_training_state(step: int, model: torch.nn.Module, optimizer: torch.optim.Optimizer):
                    torch.save({
                        'model': model.state_dict(),
                        'optimizer': optimizer.state_dict(),
                        'step': step
                    }, f"checkpoints/training_{step}.pt")

                # With additional data and custom prefix
                @save_checkpoint(save_dir="checkpoints", checkpoint_prefix="experiment1")
                def save_with_metrics(step: int, model: torch.nn.Module, metrics: Dict[str, float]):
                    torch.save({
                        'model': model.state_dict(),
                        'metrics': metrics,
                        'step': step
                    }, f"checkpoints/experiment1_step_{step}.pt")
            """
            def decorator(func: Callable[..., T]) -> Callable[..., T]:
                # Initialize state
                save_dir_path = Path(save_dir)
                save_dir_path.mkdir(parents=True, exist_ok=True)

                @functools.wraps(func)
                def wrapper(*args, **kwargs) -> T:
                    # Get current step from kwargs or args
                    step = kwargs.get('step', args[0] if args else None)
                    if step is None:
                        return func(*args, **kwargs)

                    try:
                        # Call the original save function
                        result = func(*args, **kwargs)

                        # Save metadata
                        metadata = {
                            'step': step,
                            'timestamp': datetime.now().isoformat(),
                            'model_type': kwargs.get('model', args[1] if len(args) > 1 else None).__class__.__name__,
                        }

                        metadata_path = save_dir_path / f"{checkpoint_prefix}_step_{step}_metadata.json"
                        with open(metadata_path, 'w') as f:
                            json.dump(metadata, f)

                        # Cleanup old checkpoints
                        checkpoints = sorted(
                            [f for f in save_dir_path.glob(f"{checkpoint_prefix}_step_*.pt")],
                            key=lambda x: int(x.stem.split('_')[-1])
                        )

                        while len(checkpoints) > max_checkpoints:
                            oldest_checkpoint = checkpoints.pop(0)
                            oldest_checkpoint.unlink()
                            metadata_path = oldest_checkpoint.with_suffix('_metadata.json')
                            if metadata_path.exists():
                                metadata_path.unlink()

                        logger.info(f"Saved checkpoint at step {step}")
                        return result

                    except Exception as e:
                        logger.error(f"Failed to save checkpoint at step {step}: {str(e)}")
                        return func(*args, **kwargs)

                return wrapper
            return decorator

        def load_checkpoint(
            save_dir: str,
            checkpoint_prefix: str = "checkpoint",
        ):
            """
            Decorator for loading checkpoints with fallback mechanism.
            Tries to load from the latest checkpoint, if that fails tries the second latest, and so on.

            Args:
                save_dir: Directory containing checkpoints
                checkpoint_prefix: Prefix for checkpoint files

            Examples:
                # Basic usage with a simple load function
                @load_checkpoint(save_dir="checkpoints")
                def load_model(step: int, model: torch.nn.Module):
                    model.load_state_dict(torch.load(f"checkpoints/model_{step}.pt"))

                # Loading with optimizer
                @load_checkpoint(save_dir="checkpoints")
                def load_training_state(step: int, model: torch.nn.Module, optimizer: torch.optim.Optimizer):
                    checkpoint = torch.load(f"checkpoints/training_{step}.pt")
                    model.load_state_dict(checkpoint['model'])
                    optimizer.load_state_dict(checkpoint['optimizer'])
                    return checkpoint['step']

                # Loading with custom prefix and additional data
                @load_checkpoint(save_dir="checkpoints", checkpoint_prefix="experiment1")
                def load_with_metrics(step: int, model: torch.nn.Module):
                    checkpoint = torch.load(f"checkpoints/experiment1_step_{step}.pt")
                    model.load_state_dict(checkpoint['model'])
                    return checkpoint['metrics']
            """
            def decorator(func: Callable[..., T]) -> Callable[..., T]:
                save_dir_path = Path(save_dir)

                @functools.wraps(func)
                def wrapper(*args, **kwargs) -> T:
                    try:
                        # Find available checkpoints
                        checkpoints = sorted(
                            [f for f in save_dir_path.glob(f"{checkpoint_prefix}_step_*.pt")],
                            key=lambda x: int(x.stem.split('_')[-1]),
                            reverse=True  # Sort in descending order (newest first)
                        )

                        if not checkpoints:
                            logger.warning("No checkpoints found")
                            return func(*args, **kwargs)

                        # Try each checkpoint from newest to oldest
                        for checkpoint in checkpoints:
                            try:
                                step = int(checkpoint.stem.split('_')[-1])

                                # Call the original load function with the current step
                                if 'step' in kwargs:
                                    kwargs['step'] = step
                                elif args:
                                    args = list(args)
                                    args[0] = step
                                    args = tuple(args)

                                result = func(*args, **kwargs)
                                logger.info(f"Successfully loaded checkpoint from step {step}")
                                return result

                            except Exception as e:
                                logger.warning(f"Failed to load checkpoint at step {step}, trying previous checkpoint: {str(e)}")
                                continue

                        # If we get here, all checkpoints failed
                        logger.error("Failed to load any checkpoint")
                        return func(*args, **kwargs)

                    except Exception as e:
                        logger.error(f"Failed to find checkpoints: {str(e)}")
                        return func(*args, **kwargs)

                return wrapper
            return decorator

Here are some common ways to use the checkpointing system:

Basic model saving:

.. code-block:: python

    @save_checkpoint(save_dir="checkpoints")
    def save_model(step: int, model: torch.nn.Module):
        torch.save(model.state_dict(), f"checkpoints/model_{step}.pt")

Saving with optimizer state:

.. code-block:: python

    @save_checkpoint(save_dir="checkpoints")
    def save_training_state(step: int, model: torch.nn.Module, optimizer: torch.optim.Optimizer):
        torch.save({
            'model': model.state_dict(),
            'optimizer': optimizer.state_dict(),
            'step': step
        }, f"checkpoints/training_{step}.pt")

Saving with metrics and custom prefix:

.. code-block:: python

    @save_checkpoint(save_dir="checkpoints", checkpoint_prefix="experiment1")
    def save_with_metrics(step: int, model: torch.nn.Module, metrics: Dict[str, float]):
        torch.save({
            'model': model.state_dict(),
            'metrics': metrics,
            'step': step
        }, f"checkpoints/experiment1_step_{step}.pt")

Loading checkpoints:

.. code-block:: python

    # Basic model loading
    @load_checkpoint(save_dir="checkpoints")
    def load_model(step: int, model: torch.nn.Module):
        model.load_state_dict(torch.load(f"checkpoints/model_{step}.pt"))

    # Loading with optimizer
    @load_checkpoint(save_dir="checkpoints")
    def load_training_state(step: int, model: torch.nn.Module, optimizer: torch.optim.Optimizer):
        checkpoint = torch.load(f"checkpoints/training_{step}.pt")
        model.load_state_dict(checkpoint['model'])
        optimizer.load_state_dict(checkpoint['optimizer'])
        return checkpoint['step']

    # Loading with custom prefix and metrics
    @load_checkpoint(save_dir="checkpoints", checkpoint_prefix="experiment1")
    def load_with_metrics(step: int, model: torch.nn.Module):
        checkpoint = torch.load(f"checkpoints/experiment1_step_{step}.pt")
        model.load_state_dict(checkpoint['model'])
        return checkpoint['metrics']



Examples
--------

.. _bert:

BERT end-to-end
~~~~~~~~~~~~~~~

We can take the SkyPilot YAML for BERT fine-tuning from :ref:`above <managed-job-quickstart>`, and add checkpointing/recovery to get everything working end-to-end.

.. note::

  You can find all the code for this example `in the documentation <https://docs.skypilot.co/en/latest/examples/spot/bert_qa.html>`_

In this example, we fine-tune a BERT model on a question-answering task with HuggingFace.

This example:

- has SkyPilot find a V100 instance on any cloud,
- uses spot instances to save cost, and
- uses checkpointing to recover preempted jobs quickly.

.. code-block:: yaml
  :emphasize-lines: 9-12

  # bert_qa.yaml
  name: bert-qa

  resources:
    accelerators: V100:1
    use_spot: true  # Use spot instances to save cost.
    disk_tier: best # using highest performance disk tier

  file_mounts:
    /checkpoint:
      name: # NOTE: Fill in your bucket name
      mode: MOUNT_CACHED

  envs:
    # Fill in your wandb key: copy from https://wandb.ai/authorize
    # Alternatively, you can use `--env WANDB_API_KEY=$WANDB_API_KEY`
    # to pass the key in the command line, during `sky jobs launch`.
    WANDB_API_KEY:

  # Assume your working directory is under `~/transformers`.
  workdir: ~/transformers

  setup: |
    pip install -e .
    cd examples/pytorch/question-answering/
    pip install -r requirements.txt torch==1.12.1+cu113 --extra-index-url https://download.pytorch.org/whl/cu113
    pip install wandb

  run: |
    cd examples/pytorch/question-answering/
    python run_qa.py \
      --model_name_or_path bert-base-uncased \
      --dataset_name squad \
      --do_train \
      --do_eval \
      --per_device_train_batch_size 12 \
      --learning_rate 3e-5 \
      --num_train_epochs 50 \
      --max_seq_length 384 \
      --doc_stride 128 \
      --report_to wandb \
      --output_dir /checkpoint/bert_qa/ \
      --run_name $SKYPILOT_TASK_ID \
      --save_total_limit 10 \
      --save_steps 1000

The highlighted lines add a bucket for checkpoints.
As HuggingFace has built-in support for periodic checkpointing, we just need to pass the highlighted arguments to save checkpoints to the bucket.
(See more on `Huggingface API <https://huggingface.co/docs/transformers/main_classes/trainer#transformers.TrainingArguments.save_steps>`__).
To see another example of periodic checkpointing with PyTorch, check out `our ResNet example <https://github.com/skypilot-org/skypilot/tree/master/examples/spot/resnet_ddp>`__.

We also set :code:`--run_name` to :code:`$SKYPILOT_TASK_ID` so that the logs for all recoveries of the same job will be saved
to the same run in Weights & Biases.

.. note::
  The environment variable :code:`$SKYPILOT_TASK_ID` (example: "sky-managed-2022-10-06-05-17-09-750781_bert-qa_8-0") can be used to identify the same job, i.e., it is kept identical across all
  recoveries of the job.
  It can be accessed in the task's :code:`run` commands or directly in the program itself (e.g., access
  via :code:`os.environ` and pass to Weights & Biases for tracking purposes in your training script). It is made available to
  the task whenever it is invoked. See more about :ref:`environment variables provided by SkyPilot <sky-env-vars>`.

With the highlighted changes, the managed job can now resume training after preemption! We can enjoy the benefits of
cost savings from spot instances without worrying about preemption or losing progress.

.. code-block:: console

  $ sky jobs launch -n bert-qa bert_qa.yaml


Real-world examples
~~~~~~~~~~~~~~~~~~~

* `Vicuna <https://vicuna.lmsys.org/>`_ LLM chatbot: `instructions <https://docs.skypilot.co/en/latest/llm/vicuna.html>`_, `YAML <https://docs.skypilot.co/en/latest/llm/vicuna/train.html>`__
* `Large-scale vector database ingestion <https://docs.skypilot.co/en/latest/examples/vector_database.html>`__, and the `blog post about it <https://blog.skypilot.co/large-scale-vector-database/>`__
* BERT (shown above): `YAML <https://docs.skypilot.co/en/latest/examples/spot/bert_qa.html>`__
* PyTorch DDP, ResNet: `YAML <https://docs.skypilot.co/en/latest/examples/spot/resnet.html>`__
* PyTorch Lightning DDP, CIFAR-10: `YAML <https://docs.skypilot.co/en/latest/examples/spot/lightning_cifar10.html>`__
