.. _installation:

.. |community-badge| image:: https://img.shields.io/badge/Community%20Maintained-EAFAFF?style=flat
   :alt: Community Maintained

Installation
============

Install SkyPilot
----------------

SkyPilot supports installation with ``uv`` or ``pip``.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # Create a virtual environment with pip pre-installed (required for SkyPilot)
      # SkyPilot requires 3.9 <= python <= 3.13.
      uv venv --seed --python 3.10
      source .venv/bin/activate  # Use WSL on Windows
      uv pip install skypilot

      # install dependencies for the clouds you want to use
      uv pip install "skypilot[kubernetes,aws,gcp]"

    .. note::

      The ``--seed`` flag is **required** as it ensures ``pip`` is installed in the virtual environment.
      SkyPilot needs ``pip`` to build wheels for remote cluster setup.

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # Install as a globally available tool with pip included
      # SkyPilot requires 3.9 <= python <= 3.13.
      uv tool install --with pip skypilot

      # install dependencies for the clouds you want to use
      uv tool install --with pip "skypilot[kubernetes,aws,gcp]"

    .. note::

      The ``--with pip`` flag is **required** when using ``uv tool install``.
      Without it, SkyPilot will fail when building wheels for remote clusters.

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # Recommended: use a new conda env to avoid package conflicts.
      # SkyPilot requires 3.7 <= python <= 3.13.
      conda create -y -n sky python=3.10
      conda activate sky

      pip install skypilot

      # install dependencies for the clouds you want to use
      pip install "skypilot[kubernetes,aws,gcp]"


.. dropdown:: Install SkyPilot from nightly build or source

    SkyPilot provides nightly builds and source code for the latest features and for development.

    **Install from nightly build:**

    .. tab-set::
      .. tab-item:: uv venv
        :sync: uv-venv-tab

        .. code-block:: shell

          # Create a virtual environment with pip pre-installed (required for SkyPilot)
          # SkyPilot requires 3.7 <= python <= 3.13.
          uv venv --seed --python 3.10
          source .venv/bin/activate  # On Windows: .venv\Scripts\activate

          uv pip install skypilot-nightly

          # Build the dashboard (requires Node.js and npm)
          npm --prefix sky/dashboard install
          npm --prefix sky/dashboard run build

      .. tab-item:: uv tool
        :sync: uv-tool-tab

        .. code-block:: shell

          # Install as a globally available tool with pip included
          # SkyPilot requires 3.7 <= python <= 3.13.
          uv tool install --with pip skypilot-nightly

          # Build the dashboard (requires Node.js and npm)
          npm --prefix sky/dashboard install
          npm --prefix sky/dashboard run build

      .. tab-item:: pip
        :sync: pip-tab

        .. code-block:: shell

          # Recommended: use a new conda env to avoid package conflicts.
          # SkyPilot requires 3.7 <= python <= 3.13.
          conda create -y -n sky python=3.10
          conda activate sky

          pip install skypilot-nightly

          # Build the dashboard (requires Node.js and npm)
          npm --prefix sky/dashboard install
          npm --prefix sky/dashboard run build

    **Install from source:**

    .. code-block:: shell

      # Recommended: use a new conda env to avoid package conflicts.
      # SkyPilot requires 3.7 <= python <= 3.13.
      conda create -y -n sky python=3.10
      conda activate sky

      git clone https://github.com/skypilot-org/skypilot.git
      cd skypilot

      pip install -e .

Alternatively, we also provide a :ref:`Docker image <docker-image>` as a quick way to try out SkyPilot.

Run locally or connect to a remote API server
---------------------------------------------

SkyPilot can be run as a :ref:`standalone application <sky-api-server-local>`, or connect to a :ref:`remote API server <sky-api-server-remote>` for multi-user collaboration.

**To run SkyPilot locally:**

Refer to the :ref:`cloud setup section <cloud-account-setup>` to download the necessary dependencies for the clouds you want to use.

.. tip::

  You can install dependencies for multiple clouds at once with following commands:

  .. tab-set::
    .. tab-item:: uv venv
      :sync: uv-venv-tab

      .. code-block:: shell

        # From stable release
        uv pip install "skypilot[kubernetes,aws,gcp]"
        # From nightly build
        uv pip install "skypilot-nightly[kubernetes,aws,gcp]"

    .. tab-item:: uv tool
      :sync: uv-tool-tab

      .. code-block:: shell

        # From stable release
        uv tool install --with pip "skypilot[kubernetes,aws,gcp]"
        # From nightly build
        uv tool install --with pip "skypilot-nightly[kubernetes,aws,gcp]"

    .. tab-item:: pip
      :sync: pip-tab

      .. code-block:: shell

        # From stable release
        pip install "skypilot[kubernetes,aws,gcp]"
        # From nightly build
        pip install "skypilot-nightly[kubernetes,aws,gcp]"
        # From source
        pip install -e ".[kubernetes,aws,gcp]"

.. note::

  When using SkyPilot locally, run :code:`sky api stop` after each upgrade or dependency installation
  to enable the new version.
  See :ref:`upgrade-skypilot` for more details.

**To connect to a remote API server:**

If your team has set up a :ref:`SkyPilot remote API server <sky-api-server>`, connect to it by running:

.. code-block:: shell

  sky api login

There is no need to install any dependencies locally.

See :ref:`sky-api-server-connect` for more details.

**To deploy a remote API server:**

See :ref:`sky-api-server-deploy` for detailed instructions on how to deploy a remote API server.

.. _verify-cloud-access:

Verify cloud access
-------------------

After installation, run :code:`sky check` to verify that credentials are correctly set up:

.. code-block:: shell

  sky check

This will produce a summary like:

.. code-block:: text

  Checking credentials to enable clouds for SkyPilot.
    AWS: enabled
    GCP: enabled
    Azure: enabled
    OCI: enabled
    Lambda: enabled
    Nebius: enabled
    RunPod: enabled
    Paperspace: enabled
    Fluidstack: enabled
    Cudo: enabled
    Shadeform: enabled
    IBM: enabled
    SCP: enabled
    Seeweb: enabled
    vSphere: enabled
    Cloudflare (for R2 object store): enabled
    Kubernetes: enabled
    Slurm: enabled

If any cloud's credentials or dependencies are missing, ``sky check`` will
output hints on how to resolve them. You can also refer to the cloud setup
section :ref:`below <cloud-account-setup>`.

.. tip::

  If your clouds show ``enabled`` --- |:tada:| |:tada:| **Congratulations!** |:tada:| |:tada:| You can now head over to
  :ref:`Quickstart <quickstart>` to get started with SkyPilot.

.. tip::

  To check credentials only for specific clouds, pass the clouds as arguments: :code:`sky check aws gcp`

.. tip::

  If you are having trouble setting up credentials, it may be because the API server started before they were
  configured. Try restarting the API server by running :code:`sky api stop` and then :code:`sky api start`.


Request quotas for first time users
-----------------------------------

If your cloud account has not been used to launch instances before, the
respective quotas are likely set to zero or a low limit.  This is especially
true for GPU instances.

Please follow :ref:`Requesting Quota Increase <quota>` to check quotas and request quota
increases before proceeding.

.. _shell-completion:

Enable shell completion
-----------------------

SkyPilot supports shell completion for Bash (Version 4.4 and up), Zsh and Fish. This is only available for :code:`click` versions 8.0 and up (use :code:`pip install click==8.0.4` to install).

To enable shell completion after installing SkyPilot, you will need to modify your shell configuration.
SkyPilot automates this process using the :code:`--install-shell-completion` option, which you should call using the appropriate shell name or :code:`auto`:

.. code-block:: shell

  sky --install-shell-completion auto
  # sky --install-shell-completion zsh
  # sky --install-shell-completion bash
  # sky --install-shell-completion fish

Shell completion may perform poorly on certain shells and machines.
If you experience any issues after installation, you can use the :code:`--uninstall-shell-completion` option to uninstall it, which you should similarly call using the appropriate shell name or :code:`auto`:

.. code-block:: shell

  sky --uninstall-shell-completion auto
  # sky --uninstall-shell-completion zsh
  # sky --uninstall-shell-completion bash
  # sky --uninstall-shell-completion fish

---------

.. _cloud-account-setup:

Appendix: Cloud access for local SkyPilot
-----------------------------------------

SkyPilot can be run as a :ref:`standalone application <sky-api-server-local>`, or connect to a :ref:`remote API server <sky-api-server-remote>` for multi-user collaboration.

When running SkyPilot locally, necessary dependencies and credentials need to be set up for the clouds you want to use. You can configure access to at least one cloud using the guides below.

To configure infra access for team deployment instead, see :ref:`sky-api-server-configure-credentials`.

SkyPilot supports most major cloud providers, Kubernetes, and Slurm.


.. _kubernetes-installation:

Kubernetes
~~~~~~~~~~

Install the necessary dependencies for Kubernetes.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[kubernetes]"
      # From nightly build
      uv pip install "skypilot-nightly[kubernetes]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[kubernetes]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[kubernetes]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[kubernetes]"
      # From nightly build
      pip install "skypilot-nightly[kubernetes]"
      # From source
      pip install -e ".[kubernetes]"

SkyPilot can run workloads on on-prem or cloud-hosted Kubernetes clusters
(e.g., EKS, GKE, Nebius Managed Kubernetes, Coreweave). The only requirement is a valid kubeconfig at
:code:`~/.kube/config`.

.. code-block:: shell

  # Place your kubeconfig at ~/.kube/config
  mkdir -p ~/.kube
  cp /path/to/kubeconfig ~/.kube/config

See :ref:`SkyPilot on Kubernetes <kubernetes-overview>` for more.

.. tip::
  If you do not have access to a Kubernetes cluster, you can :ref:`deploy a local Kubernetes cluster on your laptop <kubernetes-setup-kind>` with ``sky local up``.

.. _slurm-installation:

Slurm
~~~~~

.. note::

    Slurm support is under active development. We'd love to hear from you —
    please `fill out this form <https://forms.gle/rfdWQcd9oQgp41Hm8>`_.

SkyPilot can run workloads on Slurm clusters. The only requirement is SSH access to a Slurm login node.

To configure Slurm support, create a ``~/.slurm/config`` file with your Slurm cluster configuration and add the SSH credentials to connect to the Slurm login node.

.. code-block:: shell

  # Create the Slurm config directory
  mkdir -p ~/.slurm

  # Add your Slurm cluster configuration
  cat > ~/.slurm/config << EOF
  Host mycluster
      HostName login.mycluster.myorg.com
      User myusername
      IdentityFile ~/.ssh/id_rsa
  EOF

See :ref:`SkyPilot on Slurm <slurm-overview>` for more.

.. _aws-installation:

AWS
~~~

Install the necessary dependencies for AWS.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[aws]"
      # From nightly build
      uv pip install "skypilot-nightly[aws]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[aws]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[aws]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[aws]"
      # From nightly build
      pip install "skypilot-nightly[aws]"
      # From source
      pip install -e ".[aws]"

To set up AWS credentials, log into the AWS console and `create an access key for yourself <https://docs.aws.amazon.com/IAM/latest/UserGuide/access-key-self-managed.html#Using_CreateAccessKey>`_. If you don't see the "Security credentials" link shown in the AWS instructions, you may be using SSO; see :ref:`aws-sso`.

Now configure your credentials.

.. code-block:: shell

  # Configure your AWS credentials
  aws configure

- For **AWS Access Key ID**, copy the "Access key" value from console.
- For the **AWS Secret Access Key**, copy the "Secret access key" value from console.
- The **Default region name [None]:** and **Default output format [None]:** fields are optional and can be left blank to choose defaults.

To use AWS IAM Identity Center (AWS SSO), see :ref:`here<aws-sso>` for instructions.

**Optional**: To create a new AWS user with minimal permissions for SkyPilot, see :ref:`dedicated-aws-user`.

.. _installation-gcp:

GCP
~~~

Install the necessary dependencies for GCP.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[gcp]"
      # From nightly build
      uv pip install "skypilot-nightly[gcp]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[gcp]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[gcp]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[gcp]"
      # From nightly build
      pip install "skypilot-nightly[gcp]"
      # From source
      pip install -e ".[gcp]"

.. tab-set::

    .. tab-item:: Conda
        :sync: gcp-conda-tab

        .. code-block:: shell

          # Install Google Cloud SDK via conda-forge
          conda install -c conda-forge google-cloud-sdk

          # Initialize gcloud
          gcloud init

          # Run this if you don't have a credentials file.
          # This will generate ~/.config/gcloud/application_default_credentials.json.
          gcloud auth application-default login

    .. tab-item:: Manual Install
        :sync: gcp-archive-download-tab

        For MacOS with Silicon Chips:

        .. code-block:: shell

          curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-darwin-arm.tar.gz gcloud.tar.gz
          tar -xf gcloud.tar.gz
          ./google-cloud-sdk/install.sh
          # Update your path with the newly installed gcloud

      If you are using other architecture or OS,
      follow the `Google Cloud SDK installation instructions <https://cloud.google.com/sdk/docs/install#installation_instructions>`_ to download the appropriate package.

        Be sure to complete the optional step that adds ``gcloud`` to your ``PATH``.
        This step is required for SkyPilot to recognize that your ``gcloud`` installation is configured correctly.

.. tip::

  If you are using multiple GCP projects, list all the projects by :code:`gcloud projects list` and activate one by :code:`gcloud config set project <PROJECT_ID>` (see `GCP docs <https://cloud.google.com/sdk/gcloud/reference/config/set>`_).

.. dropdown:: Common GCP installation errors

    Here some commonly encountered errors and their fixes:

    * ``RemoveError: 'requests' is a dependency of conda and cannot be removed from conda's operating environment`` when running :code:`conda install -c conda-forge google-cloud-sdk` --- run :code:`conda update --force conda` first and rerun the command.
    * ``Authorization Error (Error 400: invalid_request)`` with the url generated by :code:`gcloud auth login` --- install the latest version of the `Google Cloud SDK <https://cloud.google.com/sdk/docs/install>`_ (e.g., with :code:`conda install -c conda-forge google-cloud-sdk`) on your local machine (which opened the browser) and rerun the command.

**Optional**: To create and use a long-lived service account on your local machine, see :ref:`here<gcp-service-account>`.

**Optional**: To create a new GCP user with minimal permissions for SkyPilot, see :ref:`GCP User Creation <cloud-permissions-gcp>`.

Azure
~~~~~

Install the necessary dependencies for Azure.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      # Azure CLI has an issue with uv, and requires '--prerelease allow'.
      uv pip install --prerelease allow azure-cli
      uv pip install "skypilot[azure]"
      # From nightly build
      # Azure CLI has an issue with uv, and requires '--prerelease allow'.
      uv pip install --prerelease allow azure-cli
      uv pip install "skypilot-nightly[azure]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[azure]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[azure]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[azure]"
      # From nightly build
      pip install "skypilot-nightly[azure]"
      # From source
      pip install -e ".[azure]"

.. code-block:: shell

  # Login
  az login
  # Set the subscription to use
  az account set -s <subscription_id>

Hint: run ``az account subscription list`` to get a list of subscription IDs under your account.


.. _coreweave-installation:

CoreWeave
~~~~~~~~~

`CoreWeave <https://www.coreweave.com/>`__ integrates with SkyPilot through the :ref:`Kubernetes <kubernetes-installation>` integration. To set up:

1. Install the necessary dependencies for CoreWeave.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[coreweave]"
      # From nightly build
      uv pip install "skypilot-nightly[coreweave]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[coreweave]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[coreweave]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[coreweave]"
      # From nightly build
      pip install "skypilot-nightly[coreweave]"
      # From source
      pip install -e ".[coreweave]"

2. Launch a Coreweave CKS cluster from the CoreWeave console.
3. Get your `kubeconfig <https://docs.coreweave.com/docs/products/cks/auth-access/manage-api-access-tokens>`_ from the CoreWeave console and place it at ``~/.kube/config``.

.. tip::

  CoreWeave also offers InfiniBand networking for high-performance distributed training. You can enable InfiniBand support by adding ``network_tier: best`` to your SkyPilot task configuration.

.. _coreweave-caios-installation:

CoreWeave Object Storage (CAIOS)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You can optionally set up `CoreWeave Object Storage (CAIOS) <https://docs.coreweave.com/docs/products/storage/object-storage/get-started-caios>`_ as an S3-compatible object storage that can be used with SkyPilot for storing and accessing data in your workloads.

To get CAIOS Access Key ID and Secret Access Key:

1. Log into your `CoreWeave Cloud console <https://cloud.coreweave.com/>`__.
2. Navigate to **Object Storage** → **Keys** in the left sidebar.
3. Generate a new key pair.

SkyPilot uses separate configuration files for CAIOS to avoid conflicts with your AWS credentials. Run the following command to configure your CAIOS access credentials:

.. code-block:: shell

  AWS_SHARED_CREDENTIALS_FILE=~/.coreweave/cw.credentials aws configure --profile cw

When prompted, enter your CAIOS credentials:

.. code-block:: text

  AWS Access Key ID [None]: <your_access_key_id>
  AWS Secret Access Key [None]: <your_secret_access_key>
  Default region name [None]:
  Default output format [None]: json

Next, configure the endpoint URL and addressing style for CoreWeave Object Storage. This tells AWS CLI how to connect to CoreWeave's S3-compatible service:

.. code-block:: shell

  # For external access (outside CoreWeave CKS clusters)
  AWS_CONFIG_FILE=~/.coreweave/cw.config aws configure set endpoint_url https://cwobject.com --profile cw
  AWS_CONFIG_FILE=~/.coreweave/cw.config aws configure set s3.addressing_style virtual --profile cw

.. note::

  CAIOS offers two endpoints for different use cases. Choose the right endpoint:

  - **External access (slow but accessible from anywhere)**: Use ``https://cwobject.com`` when launching SkyPilot clusters in non-CoreWeave CKS clusters. This endpoint is accessible from anywhere and uses secure HTTPS.
  - **Internal access (fast but only accessible within CoreWeave's network)**: Use ``http://cwlota.com`` only if you are launching SkyPilot clusters inside CoreWeave CKS clusters and do not need to upload local data to the bucket. The LOTA endpoint provides faster access within CoreWeave's network but only supports HTTP and is not accessible externally. Refer to `LOTA documentation <https://docs.coreweave.com/docs/products/storage/object-storage/lota/about>`_ for more details.

Nebius
~~~~~~

`Nebius <https://nebius.com/>`__ is the ultimate cloud for AI explorers. To configure Nebius access:

Install the necessary dependencies for Nebius.

.. note::
  Nebius is only supported for Python >= 3.10

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # Nebius requires 3.10 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[nebius]"
      # From nightly build
      uv pip install "skypilot-nightly[nebius]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # Nebius requires 3.10 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[nebius]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[nebius]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # Nebius requires 3.10 <= python <= 3.13.
      # From stable release
      pip install "skypilot[nebius]"
      # From nightly build
      pip install "skypilot-nightly[nebius]"
      # From source
      pip install -e ".[nebius]"

Install and configure `Nebius CLI <https://docs.nebius.com/cli/quickstart>`__:

.. code-block:: shell

  mkdir -p ~/.nebius
  nebius iam get-access-token > ~/.nebius/NEBIUS_IAM_TOKEN.txt
  nebius --format json iam whoami|jq -r '.user_profile.tenants[0].tenant_id' > ~/.nebius/NEBIUS_TENANT_ID.txt


**Optional**: You can specify specific project ID and fabric in `~/.sky/config.yaml`, see :ref:`Configuration project_id and fabric for Nebius <config-yaml-nebius>`.

Alternatively, you can also use a service account to access Nebius, see :ref:`Using Service Account for Nebius <nebius-service-account>`.

To use `Nebius Managed Kubernetes <https://nebius.com/services/managed-kubernetes>`_, see :ref:`Kubernetes Installation <kubernetes-installation>`. Retrieve the Kubernetes credential with:

.. code-block:: shell

  nebius mk8s cluster get-credentials --id <cluster_id> --external --kubeconfig $HOME/.kube/config

Nebius also offers `Object Storage <https://nebius.com/services/storage>`_, an S3-compatible object storage without any egress charges.
SkyPilot can download/upload data to Nebius buckets and mount them as local filesystem on clusters launched by SkyPilot. To set up Nebius support, run:

.. code-block:: shell

  # Install boto
  pip install boto3
  # Configure your Nebius Object Storage credentials
  aws configure --profile nebius

In the prompt, enter your Nebius Access Key ID and Secret Access Key (see `instructions to generate Nebius credentials <https://docs.nebius.com/object-storage/quickstart#env-configure>`_). Select :code:`auto` for the default region and :code:`json` for the default output format.

.. code-block:: bash

  aws configure set aws_access_key_id $NB_ACCESS_KEY_AWS_ID --profile nebius
  aws configure set aws_secret_access_key $NB_SECRET_ACCESS_KEY --profile nebius
  aws configure set region <REGION> --profile nebius
  aws configure set endpoint_url <ENDPOINT>  --profile nebius

RunPod |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~

`RunPod <https://runpod.io/>`__ is a specialized AI cloud provider that offers low-cost GPUs. To configure RunPod access:

Install the necessary dependencies for RunPod

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[runpod]"
      # From nightly build
      uv pip install "skypilot-nightly[runpod]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[runpod]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[runpod]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[runpod]"
      # From nightly build
      pip install "skypilot-nightly[runpod]"
      # From source
      pip install -e ".[runpod]"

Go to the `Settings <https://www.runpod.io/console/user/settings>`_ page on your RunPod console and generate an **API key**. Then, run:

.. code-block:: shell

  pip install "runpod>=1.6.1"
  runpod config

OCI |community-badge|
~~~~~~~~~~~~~~~~~~~~~

To access Oracle Cloud Infrastructure (OCI):

Install the necessary dependencies for OCI.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[oci]"
      # From nightly build
      uv pip install "skypilot-nightly[oci]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[oci]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[oci]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[oci]"
      # From nightly build
      pip install "skypilot-nightly[oci]"
      # From source
      pip install -e ".[oci]"

Setup the credentials by following `this guide <https://docs.oracle.com/en-us/iaas/Content/API/Concepts/apisigningkey.htm>`__. After completing the steps in the guide, the :code:`~/.oci` folder should contain the following files:

.. code-block:: text

  ~/.oci/config
  ~/.oci/oci_api_key.pem

The :code:`~/.oci/config` file should contain the following fields:

.. code-block:: text

  [DEFAULT]
  user=ocid1.user.oc1..aaaaaaaa
  fingerprint=aa:bb:cc:dd:ee:ff:gg:hh:ii:jj:kk:ll:mm:nn:oo:pp
  tenancy=ocid1.tenancy.oc1..aaaaaaaa
  region=us-sanjose-1
  # Note that we should avoid using full home path for the key_file configuration, e.g. use ~/.oci instead of /home/username/.oci
  key_file=~/.oci/oci_api_key.pem

By default, the provisioned nodes will be in the root `compartment <https://docs.oracle.com/en/cloud/foundation/cloud_architecture/governance/compartments.html>`__. To specify the `compartment <https://docs.oracle.com/en/cloud/foundation/cloud_architecture/governance/compartments.html>`_ other than root, create/edit the file :code:`~/.sky/config.yaml`, put the compartment's OCID there, as the following:

.. code-block:: text

  oci:
    region_configs:
      default:
        compartment_ocid: ocid1.compartment.oc1..aaaaaaaa......

Lambda Cloud |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`Lambda Cloud <https://lambdalabs.com/>`_ is a cloud provider offering low-cost GPUs. To configure Lambda Cloud access:

Install the necessary dependencies for Lambda Cloud.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[lambda]"
      # From nightly build
      uv pip install "skypilot-nightly[lambda]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[lambda]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[lambda]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[lambda]"
      # From nightly build
      pip install "skypilot-nightly[lambda]"
      # From source
      pip install -e ".[lambda]"

Go to the `API Keys <https://cloud.lambdalabs.com/api-keys>`_ page on your Lambda console to generate a key and then add it to :code:`~/.lambda_cloud/lambda_keys`:

.. code-block:: shell

  mkdir -p ~/.lambda_cloud
  echo "api_key = <your_api_key_here>" > ~/.lambda_cloud/lambda_keys

Together AI |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`Together AI <https://together.ai/>`_ offers GPU *instant clusters*. Accessing them is similar to using :ref:`Kubernetes <kubernetes-installation>`:

1. Install the necessary dependencies for Kubernetes.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[kubernetes]"
      # From nightly build
      uv pip install "skypilot-nightly[kubernetes]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[kubernetes]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[kubernetes]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[kubernetes]"
      # From nightly build
      pip install "skypilot-nightly[kubernetes]"
      # From source
      pip install -e ".[kubernetes]"

2. Launch a Together `Instant Cluster <https://api.together.ai/clusters/create>`_ with cluster type selected as Kubernetes
3. Get the Kubernetes config for the cluster
4. Save the kubeconfig to a file, e.g., ``./together.kubeconfig``
5. Copy the kubeconfig to your ``~/.kube/config`` or merge the Kubernetes config with your existing kubeconfig file by running:

.. code-block:: shell

  KUBECONFIG=./together-kubeconfig:~/.kube/config kubectl config view --flatten > /tmp/merged_kubeconfig && mv /tmp/merged_kubeconfig ~/.kube/config


Paperspace |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`Paperspace <https://www.paperspace.com/>`_ is a cloud provider that provides access to GPU accelerated VMs. To configure Paperspace access:

Install the necessary dependencies for Paperspace.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[paperspace]"
      # From nightly build
      uv pip install "skypilot-nightly[paperspace]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[paperspace]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[paperspace]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[paperspace]"
      # From nightly build
      pip install "skypilot-nightly[paperspace]"
      # From source
      pip install -e ".[paperspace]"

Go to follow `these instructions to generate an API key <https://docs.digitalocean.com/reference/paperspace/api-keys/>`_. Add the API key with:

.. code-block:: shell

  mkdir -p ~/.paperspace
  echo "{'api_key' : <your_api_key_here>}" > ~/.paperspace/config.json

Vast |community-badge|
~~~~~~~~~~~~~~~~~~~~~~

`Vast <https://vast.ai/>`__ is a cloud provider that offers low-cost GPUs. To configure Vast access:

Install the necessary dependencies for Vast.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[vast]"
      # From nightly build
      uv pip install "skypilot-nightly[vast]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[vast]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[vast]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[vast]"
      # From nightly build
      pip install "skypilot-nightly[vast]"
      # From source
      pip install -e ".[vast]"

Go to the `Account <https://cloud.vast.ai/account/>`_ page on your Vast console to get your **API key**. Then, run:

.. code-block:: shell

  pip install "vastai-sdk>=0.1.12"
  mkdir -p ~/.config/vastai
  echo "<your_api_key_here>" > ~/.config/vastai/vast_api_key

Fluidstack |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`Fluidstack <https://fluidstack.io/>`__ is a cloud provider offering low-cost GPUs. To configure Fluidstack access:

Install the necessary dependencies for Fluidstack.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[fluidstack]"
      # From nightly build
      uv pip install "skypilot-nightly[fluidstack]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[fluidstack]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[fluidstack]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[fluidstack]"
      # From nightly build
      pip install "skypilot-nightly[fluidstack]"
      # From source
      pip install -e ".[fluidstack]"

Go to the `Home <https://dashboard.fluidstack.io/>`__ page on your Fluidstack console to generate an API key and then add the :code:`API key` to :code:`~/.fluidstack/api_key` :

.. code-block:: shell

  mkdir -p ~/.fluidstack
  echo "your_api_key_here" > ~/.fluidstack/api_key

Cudo Compute |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`Cudo Compute <https://www.cudocompute.com/>`__ provides low cost GPUs powered by green energy.

1. Install the necessary dependencies for Cudo Compute.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[cudo]"
      # From nightly build
      uv pip install "skypilot-nightly[cudo]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[cudo]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[cudo]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[cudo]"
      # From nightly build
      pip install "skypilot-nightly[cudo]"
      # From source
      pip install -e ".[cudo]"

2. Create a `billing account <https://www.cudocompute.com/docs/guide/billing/>`__.
3. Create a `project <https://www.cudocompute.com/docs/guide/projects/>`__.
4. Create an `API Key <https://www.cudocompute.com/docs/guide/api-keys/>`__.
5. Download and install the `cudoctl <https://www.cudocompute.com/docs/cli-tool/>`__ command line tool
6. Run :code:`cudoctl init`:

  .. code-block:: shell

    cudoctl init
      ✔ api key: my-api-key
      ✔ project: my-project
      ✔ billing account: my-billing-account
      ✔ context: default
      config file saved ~/.config/cudo/cudo.yml

    pip install "cudo-compute>=0.1.10"

If you want to want to use SkyPilot with a different Cudo Compute account or project, run :code:`cudoctl init` again.

Shadeform |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~~

`Shadeform <https://www.shadeform.ai/>`_ is a cloud GPU marketplace that offers GPUs across a variety of vetted cloud providers. To configure Shadeform access:

Install the necessary dependencies for Shadeform.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[shadeform]"
      # From nightly build
      uv pip install "skypilot-nightly[shadeform]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[shadeform]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[shadeform]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[shadeform]"
      # From nightly build
      pip install "skypilot-nightly[shadeform]"
      # From source
      pip install -e ".[shadeform]"

Go to the `API Key Management <https://platform.shadeform.ai/settings/api>`_ page within your Shadeform account to generate a key and then add it to :code:`~/.shadeform/api_key`:

.. code-block:: shell

  mkdir -p ~/.shadeform
  echo "<your_api_key_here>" > ~/.shadeform/api_key

IBM |community-badge|
~~~~~~~~~~~~~~~~~~~~~

To access `IBM's VPC service <https://www.ibm.com/cloud/vpc>`__:

Install the necessary dependencies for IBM.

.. note::
  IBM is only supported for Python <= 3.11

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # IBM requires 3.7 <= python <= 3.11.
      # From stable release
      uv pip install "skypilot[ibm]"
      # From nightly build
      uv pip install "skypilot-nightly[ibm]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # IBM requires 3.7 <= python <= 3.11.
      # From stable release
      uv tool install --with pip "skypilot[ibm]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[ibm]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # IBM requires 3.7 <= python <= 3.11.
      # From stable release
      pip install "skypilot[ibm]"
      # From nightly build
      pip install "skypilot-nightly[ibm]"
      # From source
      pip install -e ".[ibm]"

Store the following fields in ``~/.ibm/credentials.yaml``:

.. code-block:: text

  iam_api_key: <user_personal_api_key>
  resource_group_id: <resource_group_user_is_a_member_of>

- Create a new API key by following `this guide <https://www.ibm.com/docs/en/app-connect/container?topic=servers-creating-cloud-api-key>`__.
- Obtain a resource group's ID from the `web console <https://cloud.ibm.com/account/resource-groups>`_.

.. note::
  Stock images aren't currently providing ML tools out of the box.
  Create private images with the necessary tools (e.g. CUDA), by following the IBM segment in `this documentation <https://github.com/skypilot-org/skypilot/blob/master/docs/source/reference/yaml-spec.rst>`_.

To access IBM's Cloud Object Storage (COS), append the following fields to the credentials file:

.. code-block:: text

  access_key_id: <access_key_id>
  secret_access_key: <secret_key_id>

To get :code:`access_key_id` and :code:`secret_access_key` use the IBM web console:

1. Create/Select a COS instance from the `web console <https://cloud.ibm.com/objectstorage/>`__.
2. From "Service Credentials" tab, click "New Credential" and toggle "Include HMAC Credential".
3. Copy "secret_access_key" and "access_key_id" to file.

Finally, install `rclone <https://rclone.org/>`_ via: ``curl https://rclone.org/install.sh | sudo bash``

.. note::
  :code:`sky check` does not reflect IBM COS's enabled status. :code:`IBM: enabled` only guarantees that IBM VM instances are enabled.

SCP (Samsung Cloud Platform) |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Samsung Cloud Platform, or SCP, provides cloud services optimized for enterprise customers. You can learn more about SCP `here <https://cloud.samsungsds.com/>`__.

To configure SCP access:

Install the necessary dependencies for SCP.

.. note::
  SCP is only supported for Python <= 3.11

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SCP requires 3.7 <= python <= 3.11.
      # From stable release
      uv pip install "skypilot[scp]"
      # From nightly build
      uv pip install "skypilot-nightly[scp]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SCP requires 3.7 <= python <= 3.11.
      # From stable release
      uv tool install --with pip "skypilot[scp]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[scp]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SCP requires 3.7 <= python <= 3.11.
      # From stable release
      pip install "skypilot[scp]"
      # From nightly build
      pip install "skypilot-nightly[scp]"
      # From source
      pip install -e ".[scp]"

You need access keys and the ID of the project your tasks will run. Go to the `Access Key Management <https://cloud.samsungsds.com/console/#/common/access-key-manage/list?popup=true>`_ page on your SCP console to generate the access keys, and the Project Overview page for the project ID. Then, add them to :code:`~/.scp/scp_credential` by running:

.. code-block:: shell

  # Create directory if required
  mkdir -p ~/.scp
  # Add the lines for "access_key", "secret_key", and "project_id" to scp_credential file
  echo "access_key = <your_access_key>" >> ~/.scp/scp_credential
  echo "secret_key = <your_secret_key>" >> ~/.scp/scp_credential
  echo "project_id = <your_project_id>" >> ~/.scp/scp_credential

.. note::

  Multi-node clusters are currently not supported on SCP.

VMware vSphere |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To configure VMware vSphere access:

Install the necessary dependencies for VMware vSphere.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[vsphere]"
      # From nightly build
      uv pip install "skypilot-nightly[vsphere]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[vsphere]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[vsphere]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[vsphere]"
      # From nightly build
      pip install "skypilot-nightly[vsphere]"
      # From source
      pip install -e ".[vsphere]"

Store the vSphere credentials in :code:`~/.vsphere/credential.yaml`:

.. code-block:: shell

    mkdir -p ~/.vsphere
    touch ~/.vsphere/credential.yaml

Here is an example of configuration within the credential file:

.. code-block:: yaml

    vcenters:
      - name: <your_vsphere_server_ip_01>
        username: <your_vsphere_user_name>
        password: <your_vsphere_user_passwd>
        skip_verification: true # If your vcenter have valid certificate then change to 'false' here
        # Clusters that can be used by SkyPilot:
        #   [] means all the clusters in the vSphere can be used by Skypilot
        # Instead, you can specify the clusters in a list:
        # clusters:
        #   - name: <your_vsphere_cluster_name1>
        #   - name: <your_vsphere_cluster_name2>
        clusters: []
      # If you are configuring only one vSphere instance, omit the following line.
      - name: <your_vsphere_server_ip_02>
        username: <your_vsphere_user_name>
        password: <your_vsphere_user_passwd>
        skip_verification: true
        clusters: []

After configuring the vSphere credentials, ensure that the necessary preparations for vSphere are completed. Please refer to this guide for more information: :ref:`Cloud Preparation for vSphere <cloud-prepare-vsphere>`

.. _cloudflare-r2-installation:

Cloudflare R2
~~~~~~~~~~~~~

Cloudflare offers `R2 <https://www.cloudflare.com/products/r2>`_, an S3-compatible object storage without any egress charges.
SkyPilot can download/upload data to R2 buckets and mount them as local filesystem on clusters launched by SkyPilot. To set up R2 support:

Install the necessary dependencies for Cloudflare R2.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[cloudflare]"
      # From nightly build
      uv pip install "skypilot-nightly[cloudflare]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[cloudflare]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[cloudflare]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[cloudflare]"
      # From nightly build
      pip install "skypilot-nightly[cloudflare]"
      # From source
      pip install -e ".[cloudflare]"

Run the following commands:

.. code-block:: shell

  # Install boto
  pip install boto3
  # Configure your R2 credentials
  AWS_SHARED_CREDENTIALS_FILE=~/.cloudflare/r2.credentials aws configure --profile r2

In the prompt, enter your R2 Access Key ID and Secret Access Key (see `instructions to generate R2 credentials <https://developers.cloudflare.com/r2/data-access/s3-api/tokens/>`_). Select :code:`auto` for the default region and :code:`json` for the default output format.

.. code-block:: text

  AWS Access Key ID [None]: <access_key_id>
  AWS Secret Access Key [None]: <access_key_secret>
  Default region name [None]: auto
  Default output format [None]: json

Next, get your `Account ID <https://developers.cloudflare.com/fundamentals/get-started/basic-tasks/find-account-and-zone-ids/>`_ from your R2 dashboard and store it in :code:`~/.cloudflare/accountid` with:

.. code-block:: shell

  mkdir -p ~/.cloudflare
  echo <YOUR_ACCOUNT_ID_HERE> > ~/.cloudflare/accountid

.. note::

  Support for R2 is in beta. Please report and issues on `Github <https://github.com/skypilot-org/skypilot/issues>`_ or reach out to us on `Slack <http://slack.skypilot.co/>`_.


Prime Intellect |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`Prime Intellect <https://primeintellect.ai/>`__ makes it easy to find global compute resources and train state-of-the-art models through distributed training across clusters. To configure Prime Intellect access:

Install the necessary dependencies for Prime Intellect.

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[primeintellect]"
      # From nightly build
      uv pip install "skypilot-nightly[primeintellect]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[primeintellect]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[primeintellect]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # SkyPilot requires 3.7 <= python <= 3.13.
      # From stable release
      pip install "skypilot[primeintellect]"
      # From nightly build
      pip install "skypilot-nightly[primeintellect]"
      # From source
      pip install -e ".[primeintellect]"

Install and configure `Prime Intellect CLI <https://docs.primeintellect.ai/cli-reference/introduction>`__:

.. code-block:: shell

  mkdir -p ~/.prime
  prime login
  # optional: set team id
  prime config set-team-id <team_id>


Seeweb |community-badge|
~~~~~~~~~~~~~~~~~~~~~~~~

`Seeweb <https://www.seeweb.it/>`_ is your European GPU Cloud Provider. To access Seeweb:

1. Install the necessary dependencies for Seeweb.

.. note::
  Seeweb is only supported for Python >= 3.10

.. tab-set::
  .. tab-item:: uv venv
    :sync: uv-venv-tab

    .. code-block:: shell

      # Seeweb requires 3.10 <= python <= 3.13.
      # From stable release
      uv pip install "skypilot[seeweb]"
      # From nightly build
      uv pip install "skypilot-nightly[seeweb]"

  .. tab-item:: uv tool
    :sync: uv-tool-tab

    .. code-block:: shell

      # Seeweb requires 3.10 <= python <= 3.13.
      # From stable release
      uv tool install --with pip "skypilot[seeweb]"
      # From nightly build
      uv tool install --with pip "skypilot-nightly[seeweb]"

  .. tab-item:: pip
    :sync: pip-tab

    .. code-block:: shell

      # Seeweb requires 3.10 <= python <= 3.13.
      # From stable release
      pip install "skypilot[seeweb]"
      # From nightly build
      pip install "skypilot-nightly[seeweb]"
      # From source
      pip install -e ".[seeweb]"

2. Log into your `Seeweb dashboard : <https://cloudcenter.seeweb.it/>`__.
3. Navigate to *Compute → API Token* in the control panel, and create **New TOKEN**.
4. Create the file :code:`~/.seeweb_cloud/seeweb_keys` with the following contents:

.. code-block:: text

  [DEFAULT]
  api_key = <your-api-token>

.. _docker-image:

Appendix: Using SkyPilot in Docker
----------------------------------

As a **quick alternative to installing SkyPilot on your laptop**, we also
provide a Docker image with SkyPilot main branch automatically cloned.
You can simply run:

.. code-block:: shell

  # NOTE: '--platform linux/amd64' is needed for Apple silicon Macs
  docker run --platform linux/amd64 \
    -td --rm --name sky \
    -v "$HOME/.sky:/root/.sky:rw" \
    -v "$HOME/.aws:/root/.aws:rw" \
    -v "$HOME/.config/gcloud:/root/.config/gcloud:rw" \
    berkeleyskypilot/skypilot

  docker exec -it sky /bin/bash

If your cloud CLIs are already set up, your credentials (AWS and GCP) will be
mounted to the container and you can proceed to :ref:`Quickstart <quickstart>`.
Otherwise, you can follow the instructions in :ref:`Cloud account setup
<cloud-account-setup>` inside the container to set up your cloud accounts.

Once you are done with experimenting with SkyPilot, remember to delete any
clusters and storage resources you may have created using the following
commands:

.. code-block:: shell

  # Run inside the container:
  sky down -a -y
  sky storage delete -a -y

Finally, you can stop the container with:

.. code-block:: shell

  docker stop sky

See more details about the dev container image
``berkeleyskypilot/skypilot-nightly`` `here
<https://github.com/skypilot-org/skypilot/blob/master/CONTRIBUTING.md#testing-in-a-container>`_.
