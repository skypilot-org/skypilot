YAML Configuration
==================

.. code-block:: yaml

    name: my-task
    workdir: ~/my-task-code

    num_nodes: 4

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


