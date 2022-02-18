YAML Configuration
==================

Sky provides the ability to specify a task, its resource requirements, and take
advantage of many other features provided using a YAML interface. Below, we
describe all fields available.

.. code-block:: yaml

    # Task name (optional), used in the job queue.
    name: my-task

    # Working directory (optional), synced each time launch or exec is run
    # with the yaml file.
    workdir: ~/my-task-code

    # Number of nodes (optional), including the head node. If not specified,
    # defaults to 1. The resource requirements are identical across all nodes.
    num_nodes: 4

    # Resource requirements.
    resources:
      cloud: aws
      accelerators:
        V100: 4
      accelerator_args:
        tf_version: 2.5.0
      use_spot: False

    storage:
      foo: bar

    file_mounts:
      foo: bar

    setup: |
      #!/bin/bash
      echo "Hello, world!"

    run: |
      #!/bin/bash
      echo "Hello, world!"


