Iterative Development
=====================

In addition to providing interactive nodes for easy provisioning of development machines,
we also provide native functions within Sky to handle iterative development and cluster reuse.

Cluster Management
----------------
We can launch a cluster by running: :code:`sky launch -c mycluster task.yaml`. This will provision and setup a new cluster and execute the task immediately after. Running the above command
a second time will cause the setup and execution phases to repeat (provisioning will be skipped since the cluster
already exists).

Once you're done using your cluster, you can either use :code:`sky stop mycluster` to temporarily halt it (but not destroy it) or
:code:`sky down mycluster` if you would like to destroy it.

To restart a stopped a cluster, you can use :code:`sky start mycluster`.


Remote Execution
----------------
In situations where you are running a task many times or running many similar tasks, you may not want
to repeat the setup process as done in :code:`sky launch`. For this purpose, we provide `sky exec`, which
executes a task or a shell command on an existing cluster.

.. code-block:: bash

  # mycluster already exists
  # the following will re-run the `run` script defined in the task.yaml
  # without re-running the `setup` script
  sky exec mycluster task.yaml

  # run a shell command on mycluster
  sky exec mycluster -- echo "hello sky!"

