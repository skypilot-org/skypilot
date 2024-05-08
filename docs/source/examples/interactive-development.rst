Start a Development Cluster
===========================

SkyPilot makes interactive development easy on Kubernetes or cloud VMs. It helps you:

#. Launch: Quickly get a cluster with GPU or other resource requirement with a single command.
#. Autostop: Automatically stop the cluster after some idle time for cost savings.
#. Connect: Easily connect to the cluster using the cluster name (e.g., ``ssh dev``).

Launch
------

To launch a cluster with a cheap GPU for developemnt:

.. code-block:: bash

  # Launch a cluster with 1 NVIDIA GPU and sync the local working directory to the
  # cluster. This can be launched as a pod in your Kubernetes cluster or a cloud VM.
  sky launch -c dev --gpus T4 --workdir .


.. note::

  View the supported GPUs with the :code:`sky show-gpus` command.


Autostop
--------

SkyPilot allows you to automatically stop the cluster after a period of idle time to save costs. You can set the autostop time with a single command:

.. code-block:: bash

  # Auto stop the cluster after 5 hours
  sky autostop -i 300 dev

Or add an additional flag :code:`-i` during the launch:

.. code-block:: bash

  # Launch a cluster with auto stop after 5 hours
  sky launch -c dev --gpus T4 --workdir . -i 300

For more details of auto stopping, check out: :ref:`auto-stop`. It helps you avoid idle machines from costing you a fortune, when you want a
machine to stop automatically after you go to bed or forget to stop it.


Connect
-------

A user can easily connect to the cluster using your method of choice:

SSH
~~~

SkyPilot will automatically configure the SSH setting for a cluster, so that users can connect to the cluster with the cluster name:

.. code-block:: bash
  
  ssh dev


VSCode
~~~~~~

A common use case for interactive development is to connect a local IDE to a remote cluster and directly edit code that lives on the cluster. 
This is supported by simply connecting VSCode to the cluster with the cluster name:

#. Click on the top bar, type: :code:`> remote-ssh`, and select :code:`Remote-SSH: Connect Current Window to Host...`
#. Select the cluster name (e.g., ``dev``) from the list of hosts.
#. Open folder: :code:`sky_workdir` and find your code.

.. image:: https://imgur.com/8mKfsET.gif
  :align: center
  :alt: Connect to the cluster with VSCode

Jupyter Notebooks
~~~~~~~~~~~~~~~~~

Jupyter notebooks are a useful tool for interactive development, debugging, and
visualization.

To get a machine with a GPU attached, use:

.. code-block:: bash

   ssh -L 8888:localhost:8888 dev

Use ``ssh dev`` to SSH into the cluster. Inside the cluster, you can run the
following commands to start a Jupyter session:

.. code-block:: bash

   pip install jupyter
   jupyter notebook

In your local browser, you should now be able to access :code:`localhost:8888` and see the following screen:

.. image:: ../images/jupyter-auth.png
  :width: 100%
  :alt: Jupyter authentication window

Enter the password or token and you will be directed to a page where you can create a new notebook.

.. image:: ../images/jupyter-create.png
  :width: 100%
  :alt: Create a new Jupyter notebook

You can verify that this notebook is running on the GPU-backed instance using :code:`nvidia-smi`.

.. image:: ../images/jupyter-gpu.png
  :width: 100%
  :alt: nvidia-smi in notebook

The GPU node is a normal SkyPilot cluster, so you can use the usual CLI commands on it.  For example, run ``sky down/stop`` to terminate or stop it, and ``sky exec`` to execute a task.

Notebooks in SkyPilot tasks
^^^^^^^^^^^^^^^^^^^^^^^^^^^
Jupyter notebooks can also be used in SkyPilot tasks, allowing access to the full
range of SkyPilot's features including :ref:`mounted storage <sky-storage>` and
:ref:`autostop <auto-stop>`.

The following :code:`jupyter.yaml` is an example of a task specification that can launch notebooks with SkyPilot.

.. code:: yaml

  # jupyter.yaml

  name: jupyter

  resources:
    accelerators: T4:1

  file_mounts:
    /covid:
      source: s3://fah-public-data-covid19-cryptic-pockets
      mode: MOUNT

  setup: |
    pip install --upgrade pip
    conda init bash
    conda create -n jupyter python=3.9 -y
    conda activate jupyter
    pip install jupyter

  run: |
    cd ~/sky_workdir
    conda activate jupyter
    jupyter notebook --port 8888 &

Launch the GPU-backed Jupyter notebook:

.. code:: bash

  sky launch -c jupyter jupyter.yaml

To access the notebook locally, use SSH port forwarding.

.. code:: bash

  ssh -L 8888:localhost:8888 jupyter

You can verify that this notebook has access to the mounted storage bucket.

.. image:: ../images/jupyter-covid.png
  :width: 100%
  :alt: accessing covid data from notebook



