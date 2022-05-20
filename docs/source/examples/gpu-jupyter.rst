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

  You can view the GPUs supported with the :code:`sky show-gpus` command.


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