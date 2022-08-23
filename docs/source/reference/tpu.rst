.. _tpu:

Cloud TPU
================================

SkyPilot supports running jobs on Google's `Cloud TPU <https://cloud.google.com/tpu/docs/intro-to-tpu>`_.
Two different TPU architectures are available on GCP:

- `TPU Nodes <https://cloud.google.com/tpu/docs/system-architecture-tpu-vm#tpu-node>`_
- `TPU VMs <https://cloud.google.com/tpu/docs/system-architecture-tpu-vm#tpu-vm>`_

Both are supported by SkyPilot. The two architectures differ as follows.
For TPU Nodes, a host VM communicates with the TPU host over gRPC.
For TPU VMs, you can SSH directly into a VM that is physically connected to the TPU device.
For more details please refer to GCP `documentation <https://cloud.google.com/tpu/docs/system-architecture-tpu-vm#tpu-arch>`_.


.. note::

   We encourage researchers to apply free TPU access through `TPU Research Cloud (TRC) <https://sites.research.google/trc/about/>`_ program.


Getting TPUs in one command
--------------------------------

Like :ref:`GPUs <interactive-nodes>`, SkyPilot provides a simple command to quickly get TPUs for development:

.. code-block:: bash

   sky tpunode                                # By default TPU v3-8 is used
   sky tpunode --use-spot                     # Preemptible TPUs
   sky tpunode --tpus tpu-v2-8                # Change TPU type to tpu-v2-8
   sky tpunode --instance-type n1-highmem-16  # Change the host VM type to n1-highmem-16
   sky tpunode --tpu-vm                       # Use TPU VM (instead of TPU Node)

After the command has finished, you will be dropped into the host VM and can start develop code right away!

Below we demonstrate how to run MNIST training on both TPU Nodes and TPU VMs with SkyPilot YAML.

TPU Nodes
--------------------------------

To use TPU Node, a host CPU VM needs to be created together with a TPU node and configured correctly to connect with each other.
SkyPilot automates the above process with a simple interface:

.. code-block:: yaml

   resources:
      instance_type: n1-highmem-8
      accelerators: tpu-v2-8
      accelerator_args:
         runtime_version: 2.5.0 # TPU software version to be used.

The above YAML considers :code:`n1-highmem-8` as the host machine and :code:`tpu-v2-8` as the TPU node resource.
You may modify the host instance type or TPU type as you wish.
To show more TPU accelerators, you may run the command :code:`sky show-gpus`.

Now, we show a complete YAML for running `MNIST training <https://cloud.google.com/tpu/docs/tutorials/mnist-2.x>`_ on TPU node with TensorFlow.

.. code-block:: yaml

   # Task name (optional), used for display purposes.
   name: mnist-tpu-node

   resources:
      accelerators: tpu-v2-8
      accelerator_args:
         runtime_version: 2.5.0 # TPU software version to be used.

   # TPU node requires loading data from a GCS bucket.
   file_mounts:
      /dataset:
         name: mnist-tpu-node
         store: gcs
         mode: MOUNT

   # The setup command.  Will be run under the working directory.
   setup: |
      git clone https://github.com/tensorflow/models.git

      conda activate mnist
      if [ $? -eq 0 ]; then
         echo 'conda env exists'
      else
         conda create -n mnist python=3.8 -y
         conda activate mnist
         pip install tensorflow==2.5.0 tensorflow-datasets tensorflow-model-optimization cloud-tpu-client
      fi

   # The command to run.  Will be run under the working directory.
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

   TPU node requires loading data from a GCS bucket, so we add a :code:`file_mounts` to create a new bucket.
   Check :ref:`SkyPilot Storage <sky-storage>` for more details.

With the above YAML, you should be able to launch the training job with :code:`sky launch`!

.. code-block:: console

   $ sky launch mnist-tpu-node.yaml -c mycluster
   ...
   (mnist-tpu-node pid=28961) Epoch 9/10
   (mnist-tpu-node pid=28961) 58/58 [==============================] - 1s 19ms/step - loss: 0.1181 - sparse_categorical_accuracy: 0.9646 - val_loss: 0.0921 - val_sparse_categorical_accuracy: 0.9719
   (mnist-tpu-node pid=28961) Epoch 10/10
   (mnist-tpu-node pid=28961) 58/58 [==============================] - 1s 20ms/step - loss: 0.1139 - sparse_categorical_accuracy: 0.9655 - val_loss: 0.0831 - val_sparse_categorical_accuracy: 0.9742
   ...
   (mnist-tpu-node pid=28961) {'accuracy_top_1': 0.9741753339767456, 'eval_loss': 0.0831054300069809, 'loss': 0.11388632655143738, 'training_accuracy_top_1': 0.9654667377471924}



TPU VMs
--------------------------------

To use TPU VMs, user only needs to add :code:`tpu_vm: True` and the desired TPU runtime version in :code:`accelerator_args` shown below:

.. code-block:: yaml

   resources:
      accelerators: tpu-v2-8
      accelerator_args:
         runtime_version: tpu-vm-base
         tpu_vm: True


Note that :code:`instance_type` is no longer needed because TPU VMs is a standalone host VM that physically connects to the TPU device.

Now we show an example of running `mnist training <https://cloud.google.com/tpu/docs/run-calculation-jax#running_jax_code_on_a_tpu_vm>`_ on TPU VM with JAX.

.. code-block:: yaml

   name: mnist-tpu-vm

   resources:
      accelerators: tpu-v2-8
      accelerator_args:
         runtime_version: tpu-vm-base
         tpu_vm: True

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
      fi

   run: |
      conda activate flax
      cd flax/examples/mnist
      python3 main.py --workdir=/tmp/mnist \
      --config=configs/default.py \
      --config.learning_rate=0.05 \
      --config.num_epochs=10

A GCS bucket is not required as the TPU VM is physically linked to the TPU device, which can access data directly.
You are expected to see the below outputs when the job finishes.

.. code-block:: console

   $ sky launch examples/tpu/tpuvm_mnist.yaml -c mycluster
   ...
   (mnist-tpu-vm pid=10155) I0823 07:49:25.468526 139641357117440 train.py:146] epoch:  9, train_loss: 0.0120, train_accuracy: 99.64, test_loss: 0.0278, test_accuracy: 99.02
   (mnist-tpu-vm pid=10155) I0823 07:49:26.966874 139641357117440 train.py:146] epoch: 10, train_loss: 0.0095, train_accuracy: 99.73, test_loss: 0.0264, test_accuracy: 99.19