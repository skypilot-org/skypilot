
.. _env-vars:

Secrets and Environment Variables
================================================

Environment variables are a powerful way to pass configuration and secrets to your tasks. There are two types of environment variables in SkyPilot:

- :ref:`User-specified environment variables <user-specified-env-vars>`: Passed by users to tasks, useful for secrets and configurations.
- :ref:`SkyPilot environment variables <sky-env-vars>`: Predefined by SkyPilot with information about the current cluster and task.

.. _user-specified-env-vars:

User-specified environment variables
------------------------------------------------------------------

User-specified environment variables are useful for passing secrets and any arguments or configurations needed for your tasks. They are made available in ``file_mounts``, ``setup``, and ``run``.

You can specify environment variables to be made available to a task in two ways:

- ``envs`` field (dict) in a :ref:`task YAML <yaml-spec>`:

  .. code-block:: yaml

    envs:
      MYVAR: val
  
- ``--env`` flag in ``sky launch/exec`` :ref:`CLI <cli>` (takes precedence over the above)

.. tip::

  To mark an environment variable as required and make SkyPilot forcefully check
  its existence (errors out if not specified), set it to an empty string or
  ``null`` in the task YAML. For example, ``WANDB_API_KEY`` and ``HF_TOKEN`` in
  the following task YAML are marked as required:

  .. code-block:: yaml

    envs:
      WANDB_API_KEY:
      HF_TOKEN: null
      MYVAR: val

The ``file_mounts``, ``setup``, and ``run`` sections of a task YAML can access the variables via the ``${MYVAR}`` syntax.

.. _passing-secrets:

Passing secrets
~~~~~~~~~~~~~~~

We recommend passing secrets to any node(s) executing your task by first making
it available in your current shell, then using ``--env SECRET`` to pass it to SkyPilot:

.. code-block:: console

  $ sky launch -c mycluster --env HF_TOKEN --env WANDB_API_KEY task.yaml
  $ sky exec mycluster --env WANDB_API_KEY task.yaml

.. tip::

  You do not need to pass the value directly such as ``--env
  WANDB_API_KEY=1234``. When the value is not specified (e.g., ``--env WANDB_API_KEY``),
  SkyPilot reads it from local environment variables.


Using in ``file_mounts``
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

    # Sets default values for some variables; can be overridden by --env.
    envs:
      MY_BUCKET: skypilot-temp-gcs-test
      MY_LOCAL_PATH: tmp-workdir
      MODEL_SIZE: 13b

    file_mounts:
        /mydir:
            name: ${MY_BUCKET}  # Name of the bucket.
            mode: MOUNT

        /another-dir2:
            name: ${MY_BUCKET}-2
            source: ["~/${MY_LOCAL_PATH}"]

        /checkpoint/${MODEL_SIZE}: ~/${MY_LOCAL_PATH}

The values of these variables are filled in by SkyPilot at task YAML parse time.

Read more at `examples/using_file_mounts_with_env_vars.yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/using_file_mounts_with_env_vars.yaml>`_.

Using in ``setup`` and ``run``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

All user-specified environment variables are exported to a task's ``setup`` and ``run`` commands (i.e., accessible when they are being run).

For example, this is useful for passing secrets (see below) or passing configurations:

.. code-block:: yaml

    # Sets default values for some variables; can be overridden by --env.
    envs:
      MODEL_NAME: decapoda-research/llama-65b-hf

    run: |
      python train.py --model_name ${MODEL_NAME} <other args>

.. code-block:: console

    $ sky launch --env MODEL_NAME=decapoda-research/llama-7b-hf task.yaml  # Override.

See complete examples at `llm/vllm/serve.yaml <https://github.com/skypilot-org/skypilot/blob/596c1415b5039adec042594f45b342374e5e6a00/llm/vllm/serve.yaml#L4-L5>`_ and `llm/vicuna/train.yaml <https://github.com/skypilot-org/skypilot/blob/596c1415b5039adec042594f45b342374e5e6a00/llm/vicuna/train.yaml#L111-L116>`_.



.. _sky-env-vars:

SkyPilot environment variables
------------------------------------------------------------------

SkyPilot exports several predefined environment variables made available during a task's execution. These variables contain information about the current cluster or task, which can be useful for distributed frameworks such as
torch.distributed, OpenMPI, etc. See examples in :ref:`dist-jobs` and :ref:`managed-jobs`.

The values of these variables are filled in by SkyPilot at task execution time.
You can access these variables in the following ways:

* In the task YAML's ``setup``/``run`` commands (a Bash script), access them using the ``${MYVAR}`` syntax;
* In the program(s) launched in ``setup``/``run``, access them using the language's standard method (e.g., ``os.environ`` for Python).

The ``setup`` and ``run`` stages can access different sets of SkyPilot environment variables:

Environment variables for ``setup``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


.. list-table::
   :widths: 20 40 10
   :header-rows: 1

   * - Name
     - Definition
     - Example
   * - ``SKYPILOT_SETUP_NODE_RANK``
     - Rank (an integer ID from 0 to :code:`num_nodes-1`) of the node being set up.
     - 0
   * - ``SKYPILOT_SETUP_NODE_IPS``
     - A string of IP addresses of the nodes in the cluster with the same order as the node ranks, where each line contains one IP address.
     
       Note that this is not necessarily the same as the nodes in ``run`` stage: the ``setup`` stage runs on all nodes of the cluster, while the ``run`` stage can run on a subset of nodes.
     -      
       .. code-block:: text

         1.2.3.4
         3.4.5.6

   * - ``SKYPILOT_NUM_NODES``
     - Number of nodes in the cluster. Same value as ``$(echo "$SKYPILOT_NODE_IPS" | wc -l)``.
     - 2
   * - ``SKYPILOT_TASK_ID``
     - A unique ID assigned to each task.
       
       This environment variable is available only when the task is submitted 
       with :code:`sky launch --detach-setup`, or run as a managed spot job.
       
       Refer to the description in the :ref:`environment variables for run <env-vars-for-run>`.
     - sky-2023-07-06-21-18-31-563597_myclus_1
     
       For managed spot jobs: sky-managed-2023-07-06-21-18-31-563597_my-job-name_1-0
   * - ``SKYPILOT_CLUSTER_INFO``
     - A JSON string containing information about the cluster. To access the information, you could parse the JSON string in bash ``echo $SKYPILOT_CLUSTER_INFO | jq .cloud`` or in Python :

       .. code-block:: python
         
         import json
         json.loads(
           os.environ['SKYPILOT_CLUSTER_INFO']
         )['cloud']

     - {"cluster_name": "my-cluster-name", "cloud": "GCP", "region": "us-central1", "zone": "us-central1-a"}
   * - ``SKYPILOT_SERVE_REPLICA_ID``
     - The ID of a replica within the service (starting from 1). Available only for a :ref:`service <sky-serve>`'s replica task.
     - 1

Since setup commands always run on all nodes of a cluster, SkyPilot ensures both of these environment variables (the ranks and the IP list) never change across multiple setups on the same cluster.

.. _env-vars-for-run:

Environment variables for ``run``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 20 40 10
   :header-rows: 1

   * - Name
     - Definition
     - Example
   * - ``SKYPILOT_NODE_RANK``
     - Rank (an integer ID from 0 to :code:`num_nodes-1`) of the node executing the task. Read more :ref:`here <dist-jobs>`.
     - 0
   * - ``SKYPILOT_NODE_IPS``
     - A string of IP addresses of the nodes reserved to execute the task, where each line contains one IP address. Read more :ref:`here <dist-jobs>`.
     - 
       .. code-block:: text

         1.2.3.4

   * - ``SKYPILOT_NUM_NODES``
     - Number of nodes assigned to execute the current task. Same value as ``$(echo "$SKYPILOT_NODE_IPS" | wc -l)``. Read more :ref:`here <dist-jobs>`.
     - 1
   * - ``SKYPILOT_NUM_GPUS_PER_NODE``
     - Number of GPUs reserved on each node to execute the task; the same as the
       count in ``accelerators: <name>:<count>`` (rounded up if a fraction). Read
       more :ref:`here <dist-jobs>`.
     - 0
   * - ``SKYPILOT_TASK_ID``
     - A unique ID assigned to each task in the format "sky-<timestamp>_<cluster-name>_<task-id>".
       Useful for logging purposes: e.g., use a unique output path on the cluster; pass to Weights & Biases; etc.
       Each task's logs are stored on the cluster at ``~/sky_logs/${SKYPILOT_TASK_ID%%_*}/tasks/*.log``.

       If a task is run as a :ref:`managed spot job <spot-jobs>`, then all
       recoveries of that job will have the same ID value. The ID is in the format "sky-managed-<timestamp>_<job-name>(_<task-name>)_<job-id>-<task-id>", where ``<task-name>`` will appear when a pipeline is used, i.e., more than one task in a managed spot job. Read more :ref:`here <spot-jobs-end-to-end>`.
     - sky-2023-07-06-21-18-31-563597_myclus_1
     
       For managed spot jobs: sky-managed-2023-07-06-21-18-31-563597_my-job-name_1-0
   * - ``SKYPILOT_CLUSTER_INFO``
     - A JSON string containing information about the cluster. To access the information, you could parse the JSON string in bash ``echo $SKYPILOT_CLUSTER_INFO | jq .cloud``  or in Python :

       .. code-block:: python
         
         import json
         json.loads(
           os.environ['SKYPILOT_CLUSTER_INFO']
         )['cloud']
     - {"cluster_name": "my-cluster-name", "cloud": "GCP", "region": "us-central1", "zone": "us-central1-a"}
   * - ``SKYPILOT_SERVE_REPLICA_ID``
     - The ID of a replica within the service (starting from 1). Available only for a :ref:`service <sky-serve>`'s replica task.
     - 1
