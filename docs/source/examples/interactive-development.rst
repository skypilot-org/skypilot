.. _dev-cluster:

Start a Development Cluster
===========================


SkyPilot makes interactive development easy on Kubernetes or cloud VMs. It helps you:

#. :ref:`Launch <dev-launch>`: Quickly get a cluster with GPUs or other resources with a single command.
#. :ref:`Autostop <dev-autostop>`: Automatically stop the cluster after some idle time for cost savings.
#. :ref:`Connect <dev-connect>`: Easily connect to the cluster using the cluster name:

   - :ref:`SSH <dev-ssh>`
   - :ref:`VSCode <dev-vscode>`
   - :ref:`Jupyter Notebooks <dev-notebooks>`
   - :ref:`marimo Notebooks <marimo-notebooks>`

.. _dev-launch:

Launch
------

To launch a cluster with a cheap GPU for development:

.. code-block:: bash

  # Launch a cluster with 1 NVIDIA GPU and sync the local working directory to the
  # cluster.
  sky launch -c dev --gpus L4 --workdir .

This can be launched as a pod in your Kubernetes cluster or a VM on any cloud.

.. tab-set::

  .. tab-item:: Kubernetes

    .. figure:: ../images/k8s-pod.png
      :align: center
      :width: 80%
      :alt: Launch a cluster as a pod in Kubernetes

      Launch a cluster as a pod in Kubernetes

  .. tab-item:: Cloud

    .. figure:: ../images/gcp-vm.png
      :align: center
      :width: 80%
      :alt: Launch a cluster as a VM on GCP

      Launch a cluster as a VM on GCP

.. note::

  View the supported GPUs with the :code:`sky show-gpus` command.

.. _dev-autostop:

Autostop
--------

SkyPilot allows you to automatically stop the cluster after a period of idle time to save costs. You can set the autostop time with a single command:

.. code-block:: bash

  # Auto stop the cluster after 5 hours
  sky autostop -i 300 dev

Or add an additional flag :code:`-i` during the launch:

.. code-block:: bash

  # Launch a cluster with auto stop after 5 hours
  sky launch -c dev --gpus L4 --workdir . -i 300

For more details of auto stopping, check out: :ref:`auto-stop`. This feature is designed
to prevent idle clusters from incurring unnecessary costs, ensuring your cluster
stops automatically, whether it's overnight or throughout the weekend.


.. _dev-connect:

Connect
-------

A user can easily connect to the cluster using your method of choice:

.. _dev-ssh:

SSH
~~~

SkyPilot will automatically configure the SSH setting for a cluster, so that users can connect to the cluster with the cluster name:

.. code-block:: bash

  ssh dev


.. _dev-vscode:

VSCode
~~~~~~

A common use case for interactive development is to connect a local IDE to a remote cluster and directly edit code that lives on the cluster.
This is supported by simply connecting VSCode to the cluster with the cluster name:

#. Click on the top bar, type: :code:`> remote-ssh`, and select :code:`Remote-SSH: Connect Current Window to Host...`
#. Select the cluster name (e.g., ``dev``) from the list of hosts.
#. Open folder: :code:`sky_workdir` and find your code.

For more details, please refer to the `VSCode documentation <https://code.visualstudio.com/docs/remote/ssh-tutorial>`__.

.. image:: https://i.imgur.com/8mKfsET.gif
  :align: center
  :alt: Connect to the cluster with VSCode

.. _dev-notebooks:

Jupyter notebooks
~~~~~~~~~~~~~~~~~

Jupyter notebooks are a useful tool for interactive development, debugging, and
visualization.

Connect to the machine and forward the port used by jupyter notebook:

.. code-block:: bash

   ssh -L 8888:localhost:8888 dev

Inside the cluster, you can run the following commands to start a Jupyter session:

.. code-block:: bash

   pip install jupyter
   jupyter notebook

In your local browser, you should now be able to access :code:`localhost:8888` and see the following screen:

.. dropdown:: Jupyter authentication page

  .. image:: ../images/jupyter-auth.png
    :width: 100%
    :alt: Jupyter authentication page

Enter the password or token and you will be directed to a page where you can create a new notebook.

.. dropdown:: Jupyter home page

  .. image:: ../images/jupyter-create.png
    :width: 100%
    :alt: Create a new Jupyter notebook

You can verify that this notebook is running on the GPU-backed instance using :code:`nvidia-smi`.

.. dropdown:: nvidia-smi in Jupyter notebook

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

.. dropdown:: jupyter.yaml

  .. code:: yaml

    # jupyter.yaml

    name: jupyter

    resources:
      accelerators: L4:1

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

.. dropdown:: Jupyter notebook page

  .. image:: ../images/jupyter-covid.png
    :width: 100%
    :alt: accessing covid data from notebook

.. _marimo-notebooks:

marimo notebooks
~~~~~~~~~~~~~~~~~

To start a marimo notebook interactively via ``sky``, you can connect to the machine and forward the
port that you want marimo to use:

.. code-block:: bash

   ssh -L 8080:localhost:8080 dev

Inside the cluster, you can run the following commands to start marimo.

.. note::
    By starting the notebook this way it runs in a completely sandboxed environment. The ``uvx`` command ensures that
    we can use ``marimo`` without installing it in a pre-existing environment and the ``--sandbox`` flag
    makes sure that any dependencies of the notebook are installed in a separate environment too.

.. code-block:: bash

   pip install uv
   uvx marimo edit --sandbox demo.py --port 8080 --token-password=supersecret

In your local browser, you should now be able to access :code:`localhost:8080` and see the following screen:

.. dropdown:: marimo authentication page

  .. image:: ../images/marimo-auth.png
    :width: 100%
    :alt: marimo authentication page

Enter the password or token and you will be directed to your notebook.

.. dropdown:: marimo notebook page

  .. image:: ../images/marimo-use.png
    :width: 100%
    :alt: What a newly created marimo notebook looks like

You can verify that this notebook is running on the GPU-backed instance using :code:`nvidia-smi` in
the terminal that marimo provides from the browser.

.. dropdown:: nvidia-smi in marimo notebook

  .. image:: ../images/marimo-nvidea.png
    :width: 100%
    :alt: nvidia-smi in marimo notebook

marimo as SkyPilot jobs
^^^^^^^^^^^^^^^^^^^^^^^

Because marimo notebooks are stored as Python scripts on disk, you can immediately use it as a SkyPilot job too.

To demonstrate this, let's consider the following marimo notebook:

.. dropdown:: marimo notebook example

  .. image:: ../images/marimo-example.png
    :width: 100%
    :alt: marimo notebook example

This is the underlying code:

.. dropdown:: marimo notebook example code

  .. code-block:: python

      # /// script
      # requires-python = ">=3.12"
      # dependencies = [
      #     "marimo",
      # ]
      # ///

      import marimo

      __generated_with = "0.18.1"
      app = marimo.App(sql_output="polars")


      @app.cell
      def _():
          import marimo as mo
          return (mo,)


      @app.cell
      def _(mo):
          print(mo.cli_args())
          return


      if __name__ == "__main__":
          app.run()

This notebook uses :code:`mo.cli_args()` to parse any command-line arguments passed to the notebook.
A more real-life use-case would take such arguments to train a PyTorch model, but this
tutorial will omit the details of training a model for sake of simplicity.

You can confirm this locally by running the notebook with the following command:

.. code-block:: bash

    uv run demo.py --hello world --demo works --lr 0.01

This will print the command-line arguments passed to the notebook.

.. code-block:: bash

    {'hello': 'world', 'demo': 'works', 'lr': '0.01'}

To use a notebook like this as a job you'll want to configure a notebook
yaml file like this:

.. dropdown:: marimo-demo.yaml

  .. code-block:: yaml

    # marimo-demo.yaml
      name: marimo-demo

      # Specify specific resources for this job here
      resources:

      # This needs to point to the folder that has the marimo notebook
      workdir: scripts

      # Fill in any env keys, like wandb
      envs:
        WANDB_API_KEY: "key"

      # We only need to install uv
      setup: pip install uv

      # If the notebook is sandboxed via --sandbox, uv takes care of the dependencies
      run: uv run demo.py --hello world --demo works --lr 0.01


You can now submit this job to ``sky`` using the following command:

.. code-block:: bash

    sky jobs launch -n marimo-demo marimo-demo.yaml

This command will provision cloud resources and then launch the job. You can monitor
the job status by checking logs in the terminal, but you can also check the dashboard
by running :code:`sky dashboard`.

This is what the dashboard of the job looks like after it is done.

.. dropdown:: SkyPilot jobs dashboard

  .. image:: ../images/marimo-job.png
    :align: center
    :alt: marimo job completed

The resources used during the job will also turn off automatically after
the resource detects a configurable amount of inactivity. You can learn more
about how to configure this behavior on the :ref:`managed-jobs` guide.

Working with clusters
---------------------

To see a typical workflow of working with clusters, you can refer to :ref:`quickstart`.
