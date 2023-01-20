.. _sky-faq:

Frequently Asked Questions
------------------------------------------------


.. contents::
    :local:
    :depth: 1


Can I clone private GitHub repositories in a task's ``setup`` commands?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes, provided you have `set up SSH agent forwarding <https://docs.github.com/en/developers/overview/using-ssh-agent-forwarding>`_.
For example, run the following on your laptop:

.. code-block:: bash

   eval $(ssh-agent -s)
   ssh-add ~/.ssh/id_rsa

Then, any SkyPilot clusters launched from this machine would be able to clone private GitHub repositories. For example:

.. code-block:: yaml

    # your_task.yaml
    setup: |
      git clone git@github.com:your-proj/your-repo.git

Note: currently, cloning private repositories in the ``run`` commands is not supported yet.

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



How to make SkyPilot clusters use my Weights & Biases credentials?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Install the wandb library on your laptop and login to your account via ``wandb login``.
Then, add the following lines in your task yaml file:

.. code-block:: yaml

  file_mounts:
    ~/.netrc: ~/.netrc

How to update an existing cluster's ``file_mounts`` without rerunning ``setup``?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you have edited the ``file_mounts`` section (e.g., by adding some files) and would like to have it reflected on an existing cluster, running ``sky launch -c <cluster> ..`` would work, but it would rerun the ``setup`` commands.

To avoid rerunning the ``setup`` commands, pass the ``--no-setup`` flag to ``sky launch``.


What are the required IAM permissons on GCP for SkyPilot?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To use SkyPilot, your GCP account needs to be granted the following IAM roles:

.. code-block:: yaml

  roles/browser
  roles/compute.admin
  roles/iam.serviceAccountAdmin
  roles/iam.serviceAccountUser
  roles/serviceusage.serviceUsageConsumer
  roles/storage.admin

Optionally, to use TPUs, add the following role:

.. code-block:: yaml

  roles/tpu.admin

You can grant those accesses via the `GCP IAM console <https://console.cloud.google.com/iam-admin/iam>`_.


(Advanced) How to make SkyPilot use all global regions?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, SkyPilot supports most global regions on AWS and only supports the US regions on GCP and Azure. If you want to utilize all global regions, please run the following command:

.. code-block:: bash

  version=$(python -c 'import sky; print(sky.clouds.service_catalog.constants.CATALOG_SCHEMA_VERSION)')
  mkdir -p ~/.sky/catalogs/${version}
  cd ~/.sky/catalogs/${version}
  # Fetch all regions for GCP
  pip install lxml
  python -m sky.clouds.service_catalog.data_fetchers.fetch_gcp --all-regions
  
  # Fetch all regions for Azure
  python -m sky.clouds.service_catalog.data_fetchers.fetch_azure --all-regions

To make your managed spot jobs potentially use all global regions, please log into the spot controller with ``ssh sky-spot-controller-<hash>`` 
(the full name can be found in ``sky status``), and run the commands above.


(Advanced) How to edit or update the regions or pricing information used by SkyPilot?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SkyPilot stores regions and pricing information for different cloud resource types in CSV files known as
`"service catalogs" <https://github.com/skypilot-org/skypilot-catalog>`_.
These catalogs are cached in the ``~/.sky/catalogs/<schema-version>/`` directory.
Check out your schema version by running the following command:

.. code-block:: bash

  python -c "from sky.clouds import service_catalog; print(service_catalog.CATALOG_SCHEMA_VERSION)"

You can customize the catalog files to your needs.
For example, if you have access to special regions of GCP, add the data to ``~/.sky/catalogs/<schema-version>/gcp.csv``.
Also, you can update the catalog for a specific cloud by deleting the CSV file (e.g., ``rm ~/.sky/catalogs/<schema-version>/gcp.csv``).
SkyPilot will automatically download the latest catalog in the next run.
