
.. _env-vars:

Using Environment Variables
================================================

User-specified environment variables
------------------------------------------------------------------

You can specify environment variables to be made available to a task in two ways:

- The ``envs`` field (dict) in a :ref:`task YAML <yaml-spec>`
- The ``--env`` flag in the ``sky launch/exec`` :ref:`CLI <cli>` (takes precedence over the above)

The ``file_mounts``, ``setup``, and ``run`` sections of a task YAML can access the variables via the ``${MYVAR}`` syntax.

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

Passing secrets
~~~~~~~~~~~~~~~~~~~~~~~~

We recommend passing secrets to any node(s) executing your task by first making
it available in your current shell, then using ``--env`` to pass it to SkyPilot:

.. code-block:: console

  $ sky launch -c mycluster --env WANDB_API_KEY task.yaml
  $ sky exec mycluster --env WANDB_API_KEY task.yaml

.. tip::

   In other words, you do not need to pass the value directly such as ``--env
   WANDB_API_KEY=1234``.





SkyPilot environment variables
------------------------------------------------------------------

SkyPilot exports these environment variables for a task's execution (while ``run`` commands are running):

.. list-table::
   :widths: 20 70 10
   :header-rows: 1

   * - Name
     - Definition
     - Example
   * - ``SKYPILOT_NODE_RANK``
     - Rank (an integer ID from 0 to :code:`num_nodes-1`) of the node executing the task. Read more :ref:`here <dist-jobs>`.
     - 0
   * - ``SKYPILOT_NODE_IPS``
     - A string of IP addresses of the nodes reserved to execute the task, where each line contains one IP address. Read more :ref:`here <dist-jobs>`.
     - 1.2.3.4
   * - ``SKYPILOT_NUM_GPUS_PER_NODE``
     - Number of GPUs reserved on each node to execute the task; the same as the
       count in ``accelerators: <name>:<count>`` (rounded up if a fraction). Read
       more :ref:`here <dist-jobs>`.
     - 0
   * - ``SKYPILOT_TASK_ID``
     - A unique ID assigned to each task.
       Useful for logging purposes: e.g., use a unique output path on the cluster; pass to Weights & Biases; etc.

       If a task is run as a :ref:`managed spot job <spot-jobs>`, then all
       recoveries of that job will have the same ID value. Read more :ref:`here <spot-jobs-end-to-end>`.
     - sky-2023-07-06-21-18-31-563597_myclus_id-1

The values of these variables are filled in by SkyPilot at task execution time.

You can access these variables in the following ways:

* In the task YAML's ``run`` commands (a Bash script), access them using the ``${MYVAR}`` syntax;
* In the program(s) launched in ``run``, access them using the
  language's standard method (e.g., ``os.environ`` for Python).
