GPU-backed Jupyter Notebooks
============================

Jupyter notebooks are a useful tool for interactive development, debugging, and
visualization. Sky makes the process of running a GPU-backed Jupyter notebook
simple by automatically managing provisioning and port forwarding.

To get a machine with a GPU attached, we recommend using an interactive **GPU node**.
You can read more about interactive nodes :ref:`here <interactive-nodes>`.

.. code-block:: bash

   # Launch a VM with 1 NVIDIA GPU and forward port 8888 to localhost
   sky gpunode -p 8888 -c jupyter-vm --gpus K80:1

.. note::

  View the supported GPUs with the :code:`sky show-gpus` command.


The above command will automatically log in to the cluster once the cluster is provisioned (or re-use an existing one).

On the VM, you can run the following command to start a Jupyter session:

.. code-block:: bash

   # Start a Jupyter notebook
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

Notebooks in Sky tasks
-----------------------
Jupyter notebooks can also be used in Sky tasks, allowing access to the full
range of Sky's features including mounted storage and autostop.

The following :code:`jupyter.yaml` is an example of a task specification that can launch notebooks with Sky.

.. code:: yaml

  # jupyter.yaml

  name: jupyter

  resources:
    accelerators: K80:1

  file_mounts:
    /covid:
      source: s3://fah-public-data-covid19-cryptic-pockets
      mode: MOUNT

  setup: |
    pip install --upgrade pip
    conda init bash
    conda activate jupyter
    conda create -n jupyter python=3.9 -y
    conda activate jupyter
    pip install jupyter

  run: |
    cd ~/sky_workdir
    conda activate jupyter
    jupyter notebook --port 8888

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
