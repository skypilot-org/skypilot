.. _local-job:
Submitting On-Prem Jobs
======================

Registering Local Clusters
-------------------

To register a local cluster in Sky, regulars users should obtain a **distributable** cluster YAML from the system administrator or follow the steps in the :ref:`prior section <local-setup>`.

The cluster YAML should be stored in :code:`~/.sky/local/`.

Listing Registered Clusters
-------------------

To list all registered local clusters, run:

.. code-block:: console

  $ sky status

This may show multiple local clusters, if you have created several:

.. code-block::

  Listing all local clusters:
  NAME              CLUSTER_USER  CLUSTER_RESOURCES              COMMAND                                                  
  my-local-cluster  my_user       [{'V100': 4}, {'V100': 4}]     sky launch -c my-local-cluster ..
  ml-research       daniel        [{'K80': 8}]                   sky exec ml-research ..
  test              -             -                              -

Local clusters that have ben ran with ``sky launch`` have all table columns populated.


Launching Task YAML
-------------------

Let's define a simple task to be submitted to the local cluster :code:`my-local-cluster`.

In this example, the user has already registered the local cluster :code:`my-local-cluster` (by moving the cluster YAML to :code:`~/.sky/local/my-local-cluster.yaml`).

Copy the following YAML into a ``local_example.yaml`` file:

.. code-block:: yaml
  
  resources:
    # All local clusters fall under local cloud
    cloud: local
    # Task resources: 1x NVIDIA V100 GPU
    accelerators: V100:1

  # How a regular user authenticates into the cluster
  auth:
    ssh_user: my_user
    ssh_private_key: ~/.ssh/id_rsa

  # Working directory (optional) containing the project codebase.
  # Its contents are synced to ~/sky_workdir/ on the cluster.
  workdir: .

  # Invoked under the workdir (i.e., can use its files).
  setup: |
    echo "Running setup."

  # Invoked under the workdir (i.e., can use its files).
  run: |
    echo "Hello, Sky On-Prem!"
    conda env list

This defines a task to be run on the Local cloud. The task takes up 1 V100 GPU.

To connect to the local cluster ``my-local-cluster`` and run a task, use :code:`sky launch`:

.. code-block:: console

  $ sky launch -c my-local-cluster local_example.yaml

The above command sets up the user's work environment on ``my_user`` and runs the task. Here, the name of the cluster **must match** the name of the local cluster.


Executing Multiple Jobs
-------------------

Tasks can be quickly submitted via :code:`sky exec`. Each task submitted by :code:`sky exec` is automatically managed by Sky's cluster manager.

.. code-block:: bash

   # Launch the job 5 times.
   sky exec my-local-cluster task.yaml -d --gpus=V100:1
   sky exec my-local-cluster task.yaml -d --gpus=V100:3
   sky exec my-local-cluster task.yaml -d --gpus=V100:4
   sky exec my-local-cluster task.yaml -d --gpus=V100:2

Refer to :ref:`Job Queue <job-queue>` for more details regarding job submission.





