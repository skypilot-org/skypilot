Quickstart
==========

Sky is a tool to run any workload seamlessly across different cloud providers
through a unified interface. No knowledge of cloud offerings is required or
expected -- you simply define the workload and its resource requirements,
and Sky will automatically execute it on AWS, Google Cloud Platform or Microsoft
Azure.

Please follow the installation instructions before continuing with this guide.

Key Features
------------
- **Run your code on the cloud with zero code changes**
- **Easy provisionioning of VMs** across multiple cloud platforms (AWS, Azure or GCP)
- **Easy management of multiple clusters** to handle different projects
- **Fast and iterative development** with quick access to cloud instances for prototyping
- **Store your datasets on the cloud** and access them like you would on a local filesystem
- **No cloud lock-in** - transparently run your code across AWS, Google Cloud, and Azure


Provisioning your first cluster
--------------------------------
We'll start by launching our first cluster on Sky using an interactive node.
Interactive nodes are standalone machines that can be used like any other VM instance,
but are easy to configure without any additional setup. Sky also handles provisioning
these nodes with your specified resources as cheaply and quickly as possible using an
:ref:`auto-failover provisioner <auto-failover>`.

Let's provision an instance with a single K80 GPU.

.. code-block:: bash

  # Provisions/reuses an interactive node with a single K80 GPU.
  # Any of the interactive node commands (gpunode, tpunode, cpunode)
  # will automatically log in to the cluster.
  sky gpunode -c mygpu --gpus K80

  Last login: Wed Feb 23 22:35:47 2022 from 136.152.143.101
  ubuntu@ip-172-31-86-108:~$ gpustat
  ip-172-31-86-108     Wed Feb 23 22:42:43 2022  450.142.00
  [0] Tesla K80        | 31°C,   0 % |     0 / 11441 MB |
  ubuntu@ip-172-31-86-108:~$
  ^D

  # View the machine in the cluster table.
  sky status

  NAME   LAUNCHED        RESOURCES                     COMMAND                          STATUS
  mygpu  a few secs ago  1x Azure(Standard_NC6_Promo)  sky gpunode -c mygpu --gpus K80  UP

After you are done, run :code:`sky down mygpu` to terminate the cluster. Find more details
on managing the lifecycle of your cluster :ref:`here <interactive-nodes>`.

Sky can also provision interactive CPU and TPU nodes with :code:`cpunode` and :code:`tpunode`.
Please see our :ref:`CLI reference <cli>` for all configuration options. For more information on
using and managing interactive nodes, check out our :ref:`reference documentation <interactive-nodes>`.


Hello, Sky!
-----------
You can also define tasks to be executed by Sky. We'll define our very first task
to be a simple hello world program.

We can specify the following task attributes with a YAML file:

- :code:`resources` (optional): what cloud resources the task must be run on (e.g., accelerators, instance type, etc.)
- :code:`workdir` (optional): specifies working directory containing project code that is synced with the provisioned instance(s)
- :code:`setup` (optional): commands that must be run before the task is executed
- :code:`run` (optional): specifies the commands that must be run as the actual ask

.. note::

    For large, multi-gigabyte workdirs (e.g. large datasets in your working directory), uploading may take time as the files are synced to the remote VM with :code:`rsync`. If you have certain files in your workdir that you would like to have excluded from upload, consider including them in your `.gitignore` file. For large datasets and files, consider using :ref:`Sky Storage <sky-storage>` to speed up transfers.

Below is a minimal task YAML that prints "hello sky!" and shows installed Conda environments,
requiring an NVIDIA Tesla K80 GPU on AWS. See more example yaml files in the `repo <https://github.com/sky-proj/sky/tree/master/examples>`_, with a fully-complete example documented :ref:`here <yaml-spec>`.

.. code-block:: yaml

  # hello_sky.yaml

  resources:
    # Optional; if left out, pick from the available clouds.
    cloud: aws

    accelerators: V100:1 # 1x NVIDIA V100 GPU

  # Working directory (optional) containing the project codebase.
  # This directory will be synced to ~/sky_workdir on the provisioned cluster.
  workdir: .

  # Typical use: pip install -r requirements.txt
  setup: |
    echo "running setup"

  # Typical use: make use of resources, such as running training.
  run: |
    echo "hello sky!"
    conda env list

Sky handles selecting an appropriate VM based on user-specified resource
constraints, launching the cluster on an appropriate cloud provider, and
executing the task.

To launch a task based on our above YAML spec, we can use :code:`sky launch`.

.. code-block:: console

  $ sky launch -c mycluster hello_sky.yaml

The :code:`-c` option allows us to specify a cluster name. If a cluster with the
same name already exists, Sky will reuse that cluster. If no such cluster
exists, a new cluster with that name will be provisioned. If no cluster name is
provided, (e.g., :code:`sky launch hello_sky.yaml`), a cluster name will be
autogenerated.

We can view our existing clusters by running :code:`sky status`:

.. code-block:: console

  $ sky status

This may show multiple clusters, if you have created several:

.. code-block::

  NAME       LAUNCHED     RESOURCES             COMMAND                                 STATUS
  gcp        1 day ago    1x GCP(n1-highmem-8)  sky cpunode -c gcp --cloud gcp          STOPPED
  mycluster  12 mins ago  1x AWS(p2.xlarge)     sky launch -c mycluster hello_sky.yaml  UP

If you would like to log into the a cluster, Sky provides convenient SSH access via :code:`ssh <cluster_name>`:

.. code-block:: console

  $ ssh mycluster

If you would like to transfer files to and from the cluster, *rsync* or *scp* can be used:

.. code-block:: console

    $ rsync -Pavz /local/path/source mycluster:/remote/dest  # copy files to remote VM
    $ rsync -Pavz mycluster:/remote/source /local/dest       # copy files from remote VM

After you are done, run :code:`sky down mycluster` to terminate the cluster. Find more details
on managing the lifecycle of your cluster :ref:`here <interactive-nodes>`.

Sky is more than a tool for easily provisioning and managing multiple clusters
on different clouds.  It also comes with features for :ref:`storing and moving data <sky-storage>`,
:ref:`queueing multiple jobs <job-queue>`, :ref:`iterative development <iter-dev>`, and :ref:`interactive nodes <interactive-nodes>` for
debugging.
