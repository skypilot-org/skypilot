.. _tpu:

=========
Cloud TPU
=========

SkyPilot supports running jobs on Google's `Cloud TPU <https://cloud.google.com/tpu>`_, a specialized hardware accelerator for ML workloads.


Free TPUs via TPU Research Cloud (TRC)
======================================

ML researchers and students are encouraged to apply for free TPU access through `TPU Research Cloud (TRC) <https://sites.research.google/trc/about/>`_ program!


Getting TPUs in one command
===========================

Use one command to quickly get TPU nodes for development:

.. code-block:: bash

   # Use latest TPU v6 (Trillium) VMs:
   sky launch --gpus tpu-v6e-8
   # Use TPU v4 (Titan) VMs:
   sky launch --gpus tpu-v4-8
   # Preemptible TPUs:
   sky launch --gpus tpu-v6e-8 --use-spot

After the command finishes, you will be dropped into a TPU host VM and can start developing code right away.

Below, we show examples of using SkyPilot to (1) train LLMs on TPU VMs/Pods and (2) train MNIST on TPU nodes (legacy).

TPU architectures
=================

Two different TPU architectures are available on GCP:

- `TPU VMs/Pods <https://cloud.google.com/tpu/docs/system-architecture-tpu-vm#tpu-vm>`_
- `TPU Nodes <https://cloud.google.com/tpu/docs/system-architecture-tpu-vm#tpu-node>`_

Both are supported by SkyPilot. We recommend TPU VMs and Pods which are newer architectures encouraged by GCP.

The two architectures differ as follows.

* For TPU VMs/Pods, you can directly SSH into the "TPU host" VM that is physically connected to the TPU device.
* For TPU Nodes, a user VM (an `n1` instance) must be separately provisioned to communicate with an inaccessible TPU host over gRPC.

More details can be found on GCP `documentation <https://cloud.google.com/tpu/docs/system-architecture-tpu-vm#tpu-arch>`_.


.. _tpu-vms:

TPU VMs/Pods
------------

Google's latest TPU v6 (Trillium) VMs offers great performance and it is now supported by SkyPilot.

To use TPU VMs/Pods, set the following in a task YAML's ``resources`` field:

.. code-block:: yaml

   resources:
      accelerators: tpu-v6e-8
      accelerator_args:
         runtime_version: v2-alpha-tpuv6e  # optional

The ``accelerators`` field specifies the TPU type, and the :code:`accelerator_args` dict includes the optional :code:`tpu_vm` bool (defaults to true, which means TPU VM is used), and an optional TPU ``runtime_version`` field.
To show what TPU types are supported, run :code:`sky show-gpus`.

Here is a complete task YAML that trains a `Llama 3 model <https://ai.meta.com/blog/meta-llama-3/>`_ on a TPU VM using Torch XLA.

.. code-block:: yaml

   resources:
      accelerators: tpu-v6e-8 # Fill in the accelerator type you want to use

   secrets:
      HF_TOKEN: null # fill in your huggingface token

   workdir: .

   setup: |
      pip3 install huggingface_hub
      python3 -c "import huggingface_hub; huggingface_hub.login('${HF_TOKEN}')"

      # Setup TPU
      pip3 install cloud-tpu-client
      sudo apt update
      sudo apt install -y libopenblas-base
      pip3 install --pre torch==2.6.0.dev20240916+cpu torchvision==0.20.0.dev20240916+cpu \
         --index-url https://download.pytorch.org/whl/nightly/cpu
      pip install "torch_xla[tpu]@https://storage.googleapis.com/pytorch-xla-releases/wheels/tpuvm/torch_xla-2.6.0.dev20240916-cp310-cp310-linux_x86_64.whl" \
         -f https://storage.googleapis.com/libtpu-releases/index.html
      pip install torch_xla[pallas] \
         -f https://storage.googleapis.com/jax-releases/jax_nightly_releases.html \
         -f https://storage.googleapis.com/jax-releases/jaxlib_nightly_releases.html

      # Setup runtime for training
      git clone -b flash_attention https://github.com/pytorch-tpu/transformers.git
      cd transformers
      pip3 install -e .
      pip3 install datasets evaluate scikit-learn accelerate

   run: |
      unset LD_PRELOAD
      PJRT_DEVICE=TPU XLA_USE_SPMD=1 ENABLE_PJRT_COMPATIBILITY=true \
      python3 transformers/examples/pytorch/language-modeling/run_clm.py \
         --dataset_name wikitext \
         --dataset_config_name wikitext-2-raw-v1 \
         --per_device_train_batch_size 16 \
         --do_train \
         --output_dir /home/$USER/tmp/test-clm \
         --overwrite_output_dir \
         --config_name /home/$USER/sky_workdir/config-8B.json \
         --cache_dir /home/$USER/cache \
         --tokenizer_name meta-llama/Meta-Llama-3-8B \
         --block_size 8192 \
         --optim adafactor \
         --save_strategy no \
         --logging_strategy no \
         --fsdp "full_shard" \
         --fsdp_config /home/$USER/sky_workdir/fsdp_config.json \
         --torch_dtype bfloat16 \
         --dataloader_drop_last yes \
         --flash_attention \
         --max_steps 20

This YAML lives under the `SkyPilot repo <https://github.com/skypilot-org/skypilot/blob/tpu-v6/examples/tpu/v6e/train-llama3-8b.yaml>`__, or you can paste it into a local file.

Launch it with:

.. code-block:: console

   $ HF_TOKEN=<your-huggingface-token> sky launch train-llama3-8b.yaml -c llama-3-train --secret HF_TOKEN

You should see the following outputs when the job finishes.

.. code-block:: console

   $ sky launch train-llama3-8b.yaml -c llama-3-train
   (task, pid=17499) ***** train metrics *****
   (task, pid=17499)   epoch                    =      1.1765
   (task, pid=17499)   total_flos               = 109935420GF
   (task, pid=17499)   train_loss               =     10.6011
   (task, pid=17499)   train_runtime            =  0:11:12.77
   (task, pid=17499)   train_samples            =         282
   (task, pid=17499)   train_samples_per_second =       0.476
   (task, pid=17499)   train_steps_per_second   =        0.03


Multi-Host TPU Pods
-------------------

A `TPU Pod <https://cloud.google.com/tpu/docs/training-on-tpu-pods>`_ is a collection of TPU devices connected by dedicated high-speed network interfaces for high-performance training.

To use a TPU Pod, simply change the ``accelerators`` field in the task YAML  (e.g., :code:`tpu-v6e-8` -> :code:`tpu-v6e-32`).

.. code-block:: yaml
   :emphasize-lines: 2-2

   resources:
      accelerators: tpu-v6e-32  # Pods have > 8 cores (the last number)

.. note::

   Both TPU architectures, TPU VMs and TPU Nodes, can be used with TPU Pods. The example below is based on TPU VMs.

To show all available TPU Pod types, run :code:`sky show-gpus` (more than 8 cores means Pods):

.. code-block:: console

   GOOGLE_TPU    AVAILABLE_QUANTITIES
   tpu-v6e-8     1
   tpu-v6e-32    1
   tpu-v6e-128   1
   tpu-v6e-256   1
   ...

After creating a TPU Pod, multiple host VMs (e.g., :code:`tpu-v6e-32` comes with 4 host VMs) are launched.
Normally, the user needs to SSH into all hosts to prepare files and setup environments, and
then launch the job on each host, which is a tedious and error-prone process.

SkyPilot automates away this complexity. From your laptop, a single :code:`sky launch` command will perform:

- workdir/file_mounts syncing; and
- execute the setup/run commands on every host of the pod.

We can run the same Llama 3 training job in on a TPU Pod with the following command, with a slight change to the YAML (``--per_device_train_batch_size`` from 16 to 32):

.. code-block:: console

   $ HF_TOKEN=<your-huggingface-token> sky launch -c tpu-pod --gpus tpu-v6e-32 train-llama3-8b.yaml --secret HF_TOKEN

You should see the following output.

.. code-block:: console

   (head, rank=0, pid=17894) ***** train metrics *****
   (head, rank=0, pid=17894)   epoch                    =         2.5
   (head, rank=0, pid=17894)   total_flos               = 219870840GF
   (head, rank=0, pid=17894)   train_loss               =     10.1527
   (head, rank=0, pid=17894)   train_runtime            =  0:11:13.18
   (head, rank=0, pid=17894)   train_samples            =         282
   (head, rank=0, pid=17894)   train_samples_per_second =       0.951
   (head, rank=0, pid=17894)   train_steps_per_second   =        0.03

   (worker1, rank=1, pid=15406, ip=10.164.0.57) ***** train metrics *****
   (worker1, rank=1, pid=15406, ip=10.164.0.57)   epoch                    =         2.5
   (worker1, rank=1, pid=15406, ip=10.164.0.57)   total_flos               = 219870840GF
   (worker1, rank=1, pid=15406, ip=10.164.0.57)   train_loss               =     10.1527
   (worker1, rank=1, pid=15406, ip=10.164.0.57)   train_runtime            =  0:11:15.08
   (worker1, rank=1, pid=15406, ip=10.164.0.57)   train_samples            =         282
   (worker1, rank=1, pid=15406, ip=10.164.0.57)   train_samples_per_second =       0.948
   (worker1, rank=1, pid=15406, ip=10.164.0.57)   train_steps_per_second   =        0.03

   (worker2, rank=2, pid=16552, ip=10.164.0.58) ***** train metrics *****
   (worker2, rank=2, pid=16552, ip=10.164.0.58)   epoch                    =         2.5
   (worker2, rank=2, pid=16552, ip=10.164.0.58)   total_flos               = 219870840GF
   (worker2, rank=2, pid=16552, ip=10.164.0.58)   train_loss               =     10.1527
   (worker2, rank=2, pid=16552, ip=10.164.0.58)   train_runtime            =  0:11:15.61
   (worker2, rank=2, pid=16552, ip=10.164.0.58)   train_samples            =         282
   (worker2, rank=2, pid=16552, ip=10.164.0.58)   train_samples_per_second =       0.947
   (worker2, rank=2, pid=16552, ip=10.164.0.58)   train_steps_per_second   =        0.03

   (worker3, rank=3, pid=17469, ip=10.164.0.59) ***** train metrics *****
   (worker3, rank=3, pid=17469, ip=10.164.0.59)   epoch                    =         2.5
   (worker3, rank=3, pid=17469, ip=10.164.0.59)   total_flos               = 219870840GF
   (worker3, rank=3, pid=17469, ip=10.164.0.59)   train_loss               =     10.1527
   (worker3, rank=3, pid=17469, ip=10.164.0.59)   train_runtime            =  0:11:15.10
   (worker3, rank=3, pid=17469, ip=10.164.0.59)   train_samples            =         282
   (worker3, rank=3, pid=17469, ip=10.164.0.59)   train_samples_per_second =       0.948
   (worker3, rank=3, pid=17469, ip=10.164.0.59)   train_steps_per_second   =        0.03


To submit more jobs to  the same TPU Pod, use :code:`sky exec`:

.. code-block:: console

   $ HF_TOKEN=<your-huggingface-token> sky exec tpu-pod train-llama3-8b.yaml --secret HF_TOKEN


**You can find more useful examples for Serving LLMs on TPUs in** `SkyPilot repo <https://github.com/skypilot-org/skypilot/tree/master/examples/tpu/v6e>`__.



TPU nodes (legacy)
------------------

In a TPU Node, a normal CPU VM (an `n1` instance) needs to be provisioned to communicate with the TPU host/device.

To use a TPU Node, set the following in a task YAML's ``resources`` field:

.. code-block:: yaml

   resources:
      instance_type: n1-highmem-8
      accelerators: tpu-v2-8
      accelerator_args:
         runtime_version: 2.12.0  # optional, TPU runtime version.
         tpu_vm: False

The above YAML considers :code:`n1-highmem-8` as the host machine and :code:`tpu-v2-8` as the TPU node resource.
You can modify the host instance type or the TPU type.

Here is a complete task YAML that runs `MNIST training <https://cloud.google.com/tpu/docs/run-calculation-jax#running_jax_code_on_a_tpu_vm>`_ on a TPU Node using TensorFlow.


.. code-block:: yaml

   name: mnist-tpu-node

   resources:
      accelerators: tpu-v2-8
      accelerator_args:
         runtime_version: 2.12.0  # optional, TPU runtime version.
         tpu_vm: False

   # TPU node requires loading data from a GCS bucket.
   # We use SkyPilot bucket mounting to mount a GCS bucket to /dataset.
   file_mounts:
      /dataset:
         name: mnist-tpu-node
         store: gcs
         mode: MOUNT

   setup: |
      git clone https://github.com/tensorflow/models.git

      conda activate mnist
      if [ $? -eq 0 ]; then
         echo 'conda env exists'
      else
         conda create -n mnist python=3.8 -y
         conda activate mnist
         pip install tensorflow==2.12.0 tensorflow-datasets tensorflow-model-optimization cloud-tpu-client
      fi

   run: |
      conda activate mnist
      cd models/official/legacy/image_classification/

      export STORAGE_BUCKET=gs://mnist-tpu-node
      export MODEL_DIR=${STORAGE_BUCKET}/mnist
      export DATA_DIR=${STORAGE_BUCKET}/data

      export PYTHONPATH=/home/gcpuser/sky_workdir/models

      python3 mnist_main.py \
         --tpu=${TPU_NAME} \
         --model_dir=${MODEL_DIR} \
         --data_dir=${DATA_DIR} \
         --train_epochs=10 \
         --distribution_strategy=tpu \
         --download

.. note::

   TPU node requires loading data from a GCS bucket. The :code:`file_mounts` spec above simplifies this by using :ref:`SkyPilot bucket mounting <sky-storage>` to create a new bucket/mount an existing bucket.
   If you encounter a bucket :code:`Permission denied` error,
   make sure the bucket is created in the same region as the Host VM/TPU Nodes and IAM permission for Cloud TPU is
   correctly setup (follow instructions `here <https://cloud.google.com/tpu/docs/storage-buckets#using_iam_permissions_for_alternative>`_).

.. note::
   The special environment variable :code:`$TPU_NAME` is automatically set by SkyPilot at run time, so it can be used in the ``run`` commands.


This YAML lives under the `SkyPilot repo <https://github.com/skypilot-org/skypilot/tree/master/examples/tpu>`_ (``examples/tpu/tpu_node_mnist.yaml``). Launch it with:

.. code-block:: console

   $ sky launch examples/tpu/tpu_node_mnist.yaml  -c mycluster
   ...
   (mnist-tpu-node pid=28961) Epoch 9/10
   (mnist-tpu-node pid=28961) 58/58 [==============================] - 1s 19ms/step - loss: 0.1181 - sparse_categorical_accuracy: 0.9646 - val_loss: 0.0921 - val_sparse_categorical_accuracy: 0.9719
   (mnist-tpu-node pid=28961) Epoch 10/10
   (mnist-tpu-node pid=28961) 58/58 [==============================] - 1s 20ms/step - loss: 0.1139 - sparse_categorical_accuracy: 0.9655 - val_loss: 0.0831 - val_sparse_categorical_accuracy: 0.9742
   ...
   (mnist-tpu-node pid=28961) {'accuracy_top_1': 0.9741753339767456, 'eval_loss': 0.0831054300069809, 'loss': 0.11388632655143738, 'training_accuracy_top_1': 0.9654667377471924}
