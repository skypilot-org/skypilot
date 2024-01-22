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

   sky launch --gpus tpu-v2-8
   # Preemptible TPUs:
   sky launch --gpus tpu-v2-8 --use-spot
   # Change TPU type to tpu-v3-8:
   sky launch --gpus tpu-v3-8
   # Change the host VM type to n1-highmem-16:
   sky launch --gpus tpu-v3-8 -t n1-highmem-16

After the command finishes, you will be dropped into a TPU host VM and can start developing code right away.

Below, we show examples of using SkyPilot to run MNIST training on (1) TPU VMs and (2) TPU Nodes.

TPU Architectures
=================

Two different TPU architectures are available on GCP:

- `TPU VMs <https://cloud.google.com/tpu/docs/system-architecture-tpu-vm#tpu-vm>`_
- `TPU Nodes <https://cloud.google.com/tpu/docs/system-architecture-tpu-vm#tpu-node>`_

Both are supported by SkyPilot. We recommend TPU VMs which is a newer architecture encouraged by GCP.

The two architectures differ as follows.
For TPU VMs, you can directly SSH into the "TPU host" VM that is physically connected to the TPU device.
For TPU Nodes, a user VM (an `n1` instance) must be separately provisioned to communicate with an inaccessible TPU host over gRPC.
More details can be found on GCP `documentation <https://cloud.google.com/tpu/docs/system-architecture-tpu-vm#tpu-arch>`_.

TPU VMs
-------

To use TPU VMs, set the following in a task YAML's ``resources`` field:

.. code-block:: yaml

   resources:
      accelerators: tpu-v2-8
      accelerator_args:
         runtime_version: tpu-vm-base  # optional

The ``accelerators`` field specifies the TPU type, and the :code:`accelerator_args` dict includes the optional :code:`tpu_vm` bool (defaults to true, which means TPU VM is used), and an optional TPU ``runtime_version`` field.
To show what TPU types are supported, run :code:`sky show-gpus`.

Here is a complete task YAML that runs `MNIST training <https://cloud.google.com/tpu/docs/run-calculation-jax#running_jax_code_on_a_tpu_vm>`_ on a TPU VM using JAX.

.. code-block:: yaml

   name: mnist-tpu-vm

   resources:
      accelerators: tpu-v2-8
      accelerator_args:
         tpu_vm: True
         runtime_version: tpu-vm-base

   setup: |
      git clone https://github.com/google/flax.git

      conda activate flax
      if [ $? -eq 0 ]; then
         echo 'conda env exists'
      else
         conda create -n flax python=3.8 -y
         conda activate flax
         # Make sure to install TPU related packages in a conda env to avoid package conflicts.
         pip install "jax[tpu]>=0.2.16" -f https://storage.googleapis.com/jax-releases/libtpu_releases.html
         pip install --upgrade clu
         pip install -e flax
         pip install tensorflow tensorflow-datasets
      fi

   run: |
      conda activate flax
      cd flax/examples/mnist
      python3 main.py --workdir=/tmp/mnist \
      --config=configs/default.py \
      --config.learning_rate=0.05 \
      --config.num_epochs=10

This YAML lives under the `SkyPilot repo <https://github.com/skypilot-org/skypilot/tree/master/examples/tpu>`_ (``examples/tpu/tpuvm_mnist.yaml``), or you can paste it into a local file.

Launch it with:

.. code-block:: console

   $ sky launch examples/tpu/tpuvm_mnist.yaml -c mycluster

You should see the following outputs when the job finishes.

.. code-block:: console

   $ sky launch examples/tpu/tpuvm_mnist.yaml -c mycluster
   ...
   (mnist-tpu-vm pid=10155) I0823 07:49:25.468526 139641357117440 train.py:146] epoch:  9, train_loss: 0.0120, train_accuracy: 99.64, test_loss: 0.0278, test_accuracy: 99.02
   (mnist-tpu-vm pid=10155) I0823 07:49:26.966874 139641357117440 train.py:146] epoch: 10, train_loss: 0.0095, train_accuracy: 99.73, test_loss: 0.0264, test_accuracy: 99.19


TPU Nodes
---------

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
   # We use SkyPilot Storage to mount a GCS bucket to /dataset.
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

   TPU node requires loading data from a GCS bucket. The :code:`file_mounts` spec above simplifies this by using :ref:`SkyPilot Storage <sky-storage>` to create a new bucket/mount an existing bucket.
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






Using TPU Pods
==============

A `TPU Pod <https://cloud.google.com/tpu/docs/training-on-tpu-pods>`_ is a collection of TPU devices connected by dedicated high-speed network interfaces for high-performance training.

To use a TPU Pod, simply change the ``accelerators`` field in the task YAML  (e.g., :code:`v2-8` -> :code:`v2-32`).

.. code-block:: yaml
   :emphasize-lines: 2-2

   resources:
      accelerators: tpu-v2-32  # Pods have > 8 cores (the last number)
      accelerator_args:
         runtime_version: tpu-vm-base

.. note::

   Both TPU architectures, TPU VMs and TPU Nodes, can be used with TPU Pods. The example below is based on TPU VMs.

To show all available TPU Pod types, run :code:`sky show-gpus` (more than 8 cores means Pods):

.. code-block:: console

   GOOGLE_TPU   AVAILABLE_QUANTITIES
   tpu-v2-8     1
   tpu-v2-32    1
   tpu-v2-128   1
   tpu-v2-256   1
   tpu-v2-512   1
   tpu-v3-8     1
   tpu-v3-32    1
   tpu-v3-64    1
   tpu-v3-128   1
   tpu-v3-256   1
   tpu-v3-512   1
   tpu-v3-1024  1
   tpu-v3-2048  1

After creating a TPU Pod, multiple host VMs (e.g., :code:`v2-32` comes with 4 host VMs) are launched.
Normally, the user needs to SSH into all hosts (depending on the architecture used, either the ``n1`` User VMs or the TPU Host VMs) to prepare files and setup environments, and
then launch the job on each host, which is a tedious and error-prone process.

SkyPilot automates away this complexity. From your laptop, a single :code:`sky launch` command will perform:

- workdir/file_mounts syncing; and
- execute the setup/run commands on every host of the pod.

Here is a task YAML for a cifar10 training job on a :code:`v2-32` TPU Pod with JAX (`code repo <https://github.com/infwinston/tpu-example>`_):

.. code-block:: yaml

   name: cifar-tpu-pod

   resources:
      accelerators: tpu-v2-32
      accelerator_args:
         runtime_version: tpu-vm-base

   setup: |
      git clone https://github.com/infwinston/tpu-example.git
      cd tpu-example
      pip install "jax[tpu]" -f https://storage.googleapis.com/jax-releases/libtpu_releases.html
      pip install -r requirements.txt

   run: |
      python -u tpu-example/train.py

Launch it with:

.. code-block:: console

   $ sky launch examples/tpu/cifar_pod.yaml -c mycluster

You should see the following output.

.. code-block:: console

   (node-0 pid=57977, ip=10.164.0.24) JAX process: 1 / 4
   (node-3 pid=57963, ip=10.164.0.26) JAX process: 3 / 4
   (node-2 pid=57922, ip=10.164.0.25) JAX process: 2 / 4
   (node-1 pid=63223) JAX process: 0 / 4
   ...
   (node-0 pid=57977, ip=10.164.0.24) [  1000/100000]      time  0.034 ( 0.063)    data  0.008 ( 0.008)    loss  1.215 ( 1.489)    acc 68.750 (46.163)

.. note::

   By default, outputs from all hosts are shown with the ``node-<i>`` prefix. Use :code:`jax.process_index()` to control which host to print messages.

To submit more jobs to  the same TPU Pod, use :code:`sky exec`:

.. code-block:: console

   $ sky exec mycluster examples/tpu/cifar_pod.yaml
