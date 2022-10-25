.. _benchmark-cli:

CLI
===

Workflow
--------

You can use SkyPilot Benchmark by simply replacing your ``sky launch`` command with ``sky bench launch``:

.. code-block:: bash

    # Launch mytask on a V100 VM and a T4 VM
    $ sky bench launch mytask.yaml --gpus V100,T4 --benchmark mybench

The second command will launch ``mytask.yaml`` on a V100 VM and a T4 VM simultaneously, with a benchmark name ``mybench``.
After the task finishes, you can check the benchmark results using ``sky bench show``:

.. code-block:: bash

    # Show the benchmark report on `mybench`
    $ sky bench show mybench

    CLUSTER              RESOURCES                          STATUS    DURATION  SPENT($)  STEPS  SEC/STEP  $/STEP  EST(hr)  EST($)  
    sky-bench-mybench-0  1x GCP(n1-highmem-8, {'V100': 1})  FINISHED  12m 51s   0.6317    -       -         -       -        -       
    sky-bench-mybench-1  1x AWS(g4dn.xlarge, {'T4': 1})     FINISHED  16m 19s   0.1430    -       -         -       -        -     

In the report, SkyPilot shows the duration and cost of ``mybench`` on each VM.
The VMs can be terminated by either ``sky bench down`` or ``sky down``:

.. code-block:: bash

    # Terminate all the clusters used for `mybench`
    $ sky bench down mybench

    # Terminate all the clusters used for `mybench` except `sky-bench-mybench-0`
    $ sky bench down mybench --exclude sky-bench-mybench-0

    # Terminate individual clusters as usual
    $ sky down sky-bench-mybench-0

.. note::

    Each cluster launched by ``sky bench launch`` will automatically **stop** itself 5 minutes after the task is finished.
    However, you don't have to restart those clusters.
    Regardless of the status of the clusters, ``sky bench show`` will provide the benchmark results.

.. note::

    SkyPilot Benchmark does not consider the time/cost of provisioning and setup.
    The columns (such as ``DURATION`` and ``SPENT($)``) in the report indicate the time/cost spent in executing the ``run`` section of your task YAML.

.. note::

    Here, the columns other than ``DURATION`` and ``SPENT($)`` are empty.
    To get a complete benchmark report, please refer to :ref:`SkyCallback`.


Managing benchmark reports
---------------------------

``sky bench ls`` shows the list of the benchmark reports you have:

.. code-block:: bash

    # List all the benchmark reports
    $ sky bench ls

    BENCHMARK  TASK         LAUNCHED             CANDIDATE 1                    CANDIDATE 2            CANDIDATE 3            CANDIDATE 4               
    bert       bert_qa      2022-08-10 10:07:27  1x Standard_NC6_Promo (K80:1)  1x g4dn.xlarge (T4:1)  1x g5.xlarge (A10G:1)  1x n1-highmem-8 (V100:1)  
    mybench    mytask       2022-08-10 11:24:27  1x n1-highmem-8 (V100:1)       1x g4dn.xlarge (T4:1)

To delete a benchmark report, use ``sky bench delete``:

.. code-block:: bash

    # Delete the benchmark report on `mybench`
    $ sky bench delete mybench
