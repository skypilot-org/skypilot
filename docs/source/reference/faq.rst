.. _sky-faq:

Frequently Asked Questions
==========================


.. contents::
    :local:
    :depth: 2
    :backlinks: none


Git and GitHub
--------------

How to clone private GitHub repositories in a job?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``workdir`` to clone private GitHub repositories in a job.

.. code-block:: yaml

  # your_task.yaml
  workdir:
    url: git@github.com:your-proj/your-repo.git
    ref: main

  run: |
    cd your-repo
    git pull

**Authentication**:

*For HTTPS URLs*: Set the ``GIT_TOKEN`` environment variable. SkyPilot will automatically use this token for authentication.

*For SSH/SCP URLs*: SkyPilot will attempt to authenticate using SSH keys in the following order:

1. SSH key specified by the ``GIT_SSH_KEY_PATH`` environment variable
2. SSH key configured in ``~/.ssh/config`` for the git host
3. Default SSH key at ``~/.ssh/id_rsa``
4. Default SSH key at ``~/.ssh/id_ed25519`` (if ``~/.ssh/id_rsa`` does not exist)

How to ensure my workdir's ``.git`` is synced up for managed spot jobs?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Currently, there is a difference in whether ``.git`` is synced up depending on the command used:

- For regular ``sky launch``, the workdir's ``.git`` is synced up by default.
- For managed jobs ``sky jobs launch``, the workdir's ``.git`` is excluded by default.

In the second case, to ensure the workdir's ``.git`` is synced up for managed spot jobs, you can explicitly add a file mount to sync it up:

.. code-block:: yaml

  workdir: .
  file_mounts:
    ~/sky_workdir/.git: .git

This can be useful if your jobs use certain experiment tracking tools that depend on the ``.git`` directory to track code changes.

File mounting (``file_mounts``)
-------------------------------

How to make SkyPilot clusters use my Weights & Biases credentials?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Install the wandb library on your laptop and login to your account via ``wandb login``.
Then, add the following lines in your task yaml file:

.. code-block:: yaml

  file_mounts:
    ~/.netrc: ~/.netrc

How to mount additional files into a cloned repository?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you want to mount additional files into a path that will be ``git clone``-ed (either in ``setup`` or ``run``), cloning will fail and complain that the target path is not empty:

.. code-block:: yaml

  file_mounts:
    ~/code-repo/tmp.txt: ~/tmp.txt
  setup: |
    # Fail! Git will complain the target dir is not empty:
    #    fatal: destination path 'code-repo' already exists and is not an empty directory.
    # This is because file_mounts are processed before `setup`.
    git clone git@github.com:your-id/your-repo.git ~/code-repo/

To get around this, mount the files to a different path, then symlink to them.  For example:

.. code-block:: yaml

  file_mounts:
    /tmp/tmp.txt: ~/tmp.txt
  setup: |
    git clone git@github.com:your-id/your-repo.git ~/code-repo/
    ln -s /tmp/tmp.txt ~/code-repo/


How to update an existing cluster's ``file_mounts`` without rerunning ``setup``?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you have edited the ``file_mounts`` section (e.g., by adding some files) and would like to have it reflected on an existing cluster, running ``sky launch -c <cluster> ..`` would work, but it would rerun the ``setup`` commands.

To avoid rerunning the ``setup`` commands, pass the ``--no-setup`` flag to ``sky launch``.


Region settings
---------------

How to launch VMs in a subset of regions only (e.g., Europe only)?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When defining a task, you can use the ``resources.any_of`` field to specify a set of regions you want to launch VMs in.

For example, to launch VMs in Europe only (which can help with GDPR compliance), you can use the following task definition:

.. code-block:: yaml

  resources:
    # SkyPilot will perform cost optimization among the specified regions.
    any_of:
      # AWS:
      - region: eu-central-1
      - region: eu-west-1
      - region: eu-west-2
      - region: eu-west-3
      - region: eu-north-1
      # GCP:
      - region: europe-central2
      - region: europe-north1
      - region: europe-southwest1
      - region: europe-west1
      - region: europe-west10
      - region: europe-west12
      - region: europe-west2
      - region: europe-west3
      - region: europe-west4
      - region: europe-west6
      - region: europe-west8
      - region: europe-west9
      # Or put in other clouds' Europe regions.

See more details about the ``resources.any_of`` field :ref:`here <multiple-resources>`.

(Advanced) How to make SkyPilot use all global regions?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, SkyPilot supports most global regions on AWS and only supports the US regions on GCP and Azure. If you want to utilize all global regions, please run the following command:

.. code-block:: bash

  version=$(python -c 'import sky; print( sky.skylet.constants.CATALOG_SCHEMA_VERSION)')
  mkdir -p ~/.sky/catalogs/${version}
  cd ~/.sky/catalogs/${version}
  # GCP
  pip install lxml
  # Fetch U.S. regions for GCP
  python -m sky.catalog.data_fetchers.fetch_gcp
  # Fetch the specified zones for GCP
  python -m sky.catalog.data_fetchers.fetch_gcp --zones northamerica-northeast1-a us-east1-b us-east1-c
  # Fetch U.S. zones for GCP, excluding the specified zones
  python -m sky.catalog.data_fetchers.fetch_gcp --exclude us-east1-a us-east1-b
  # Fetch all regions for GCP
  python -m sky.catalog.data_fetchers.fetch_gcp --all-regions
  # Run in single-threaded mode. This is useful when multiple processes don't work well with the GCP client due to SSL issues.
  python -m sky.catalog.data_fetchers.fetch_gcp --single-threaded

  # Azure
  # Fetch U.S. regions for Azure
  python -m sky.catalog.data_fetchers.fetch_azure
  # Fetch all regions for Azure
  python -m sky.catalog.data_fetchers.fetch_azure --all-regions
  # Run in single-threaded mode. This is useful when multiple processes don't work well with the Azure client due to SSL issues.
  python -m sky.catalog.data_fetchers.fetch_azure --single-threaded
  # Fetch the specified regions for Azure
  python -m sky.catalog.data_fetchers.fetch_azure --regions japaneast australiaeast uksouth
  # Fetch U.S. regions for Azure, excluding the specified regions
  python -m sky.catalog.data_fetchers.fetch_azure --exclude centralus eastus

To make your managed spot jobs potentially use all global regions, please log into the spot controller with ``ssh sky-spot-controller-<hash>``
(the full name can be found in ``sky status``), and run the commands above.


(Advanced) How to edit or update the regions or pricing information used by SkyPilot?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SkyPilot stores regions and pricing information for different cloud resource types in CSV files known as
`"service catalogs" <https://github.com/skypilot-org/skypilot-catalog>`_.
These catalogs are cached in the ``~/.sky/catalogs/<schema-version>/`` directory.
Check out your schema version by running the following command:

.. code-block:: bash

  python -c "from sky.skylet import constants; print(constants.CATALOG_SCHEMA_VERSION)"

You can customize the catalog files to your needs.
For example, if you have access to special regions of GCP, add the data to ``~/.sky/catalogs/<schema-version>/gcp.csv``.
Also, you can update the catalog for a specific cloud by deleting the CSV file (e.g., ``rm ~/.sky/catalogs/<schema-version>/gcp.csv``).
SkyPilot will automatically download the latest catalog in the next run.

Package installation
---------------------

Unable to import PyTorch in a SkyPilot task.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
For `PyTorch <https://pytorch.org/>`_ installation, if you are using the default SkyPilot images (not passing in `--image-id`), ``pip install torch`` should work.

But if you use your own image which has an older NVIDIA driver (535.161.08 or lower) and you install the default PyTorch, you may encounter the following error:

.. code-block:: bash

  ImportError: /home/azureuser/miniconda3/lib/python3.10/site-packages/torch/lib/../../nvidia/cusparse/lib/libcusparse.so.12: undefined symbol: __nvJitLinkComplete_12_4, version libnvJitLink.so.12

You will need to install a PyTorch version that is compatible with your NVIDIA driver, e.g., ``pip install torch --index-url https://download.pytorch.org/whl/cu121``.


Miscellaneous
-------------

Why can't I use ``ray.init(address="auto")`` directly in my SkyPilot job?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SkyPilot uses Ray internally on port 6380 for cluster management. When you use ``ray.init(address="auto")``, Ray connects to SkyPilot's internal cluster, causing resource bundling conflicts where user workloads interfere with SkyPilot's resource management.

**Always start your own Ray cluster** on a different port (e.g., 6379). See the :ref:`distributed Ray example <dist-jobs>` for the correct pattern:

.. code-block:: yaml

  run: |
    head_ip=`echo "$SKYPILOT_NODE_IPS" | head -n1`
    if [ "$SKYPILOT_NODE_RANK" == "0" ]; then
      ray start --head
      python your_script.py
    else
      ray start --address $head_ip:6379
    fi

How can I launch a VS Code tunnel using a SkyPilot task definition?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To launch a VS Code tunnel using a SkyPilot task definition, you can use the following task definition:

.. code-block:: yaml

    setup: |
      sudo snap install --classic code
      # if `snap` is not available, you can try the following commands instead:
      # wget https://go.microsoft.com/fwlink/?LinkID=760868 -O vscode.deb
      # sudo apt install ./vscode.deb -y
      # rm vscode.deb
    run: |
      code tunnel --accept-server-license-terms

Note that you'll be prompted to authenticate with your GitHub account to launch a VS Code tunnel.


.. _upgrade-skypilot:

Upgrading SkyPilot
------------------

As SkyPilot runs an API server in the background, whenever you upgrade SkyPilot you will
need to manually stop the old API server to have the new version take effect.

.. code-block:: bash

  sky api stop


.. _migration-0.8.1:

Migration from ``SkyPilot<=0.8.1``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

After ``SkyPilot v0.8.1``, SkyPilot has moved to a new client-server architecture, which is more flexible and powerful.
It also introduces the :ref:`asynchronous execution model <async>`, which may cause compatibility issues with user programs using  previous SkyPilot SDKs.


Asynchronous execution
^^^^^^^^^^^^^^^^^^^^^^
All SkyPilot SDKs (except log related functions: ``sky.tail_logs``, ``sky.jobs.tail_logs``, ``sky.serve.tail_logs``) are now asynchronous, and they return a request ID that can be used to manage the request.

**Action needed**: Wrapping all SkyPilot SDK function calls with ``sky.stream_and_get()`` will make your program behave mostly the same as before:

``SkyPilot<=0.8.1``:

.. code-block:: python

  task = sky.Task(run="echo hello SkyPilot")
  job_id, handle = sky.launch(task)
  sky.tail_logs(job_id)

``SkyPilot>0.8.1``:

.. code-block:: python
  :emphasize-lines: 2

  task = sky.Task(run="echo hello SkyPilot")
  job_id, handle = sky.stream_and_get(sky.launch(task))
  sky.tail_logs(job_id)

Removed arguments: :code:`detach_setup`/:code:`detach_run`
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:code:`detach_setup`/:code:`detach_run` in :code:`sky.launch` were removed after
:code:`0.8.1`, because setup and run are now detached by default with Python SDK.
If you would like to view the logs for the jobs submitted to a cluster, you can
explicitly call ``sky.tail_logs(job_id)`` as shown above.
