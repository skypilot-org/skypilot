.. _installation:

Installation
==================

.. note::

    For Macs, macOS >= 10.15 is required to install SkyPilot. Apple Silicon-based devices (e.g. Apple M1) must run :code:`pip uninstall grpcio; conda install -c conda-forge grpcio=1.43.0` prior to installing SkyPilot.

Install SkyPilot using pip:

.. tab-set::

    .. tab-item:: Nightly (recommended)
        :sync: nightly-tab

        .. code-block:: shell

          # Recommended: use a new conda env to avoid package conflicts.
          # SkyPilot requires 3.7 <= python <= 3.11.
          conda create -y -n sky python=3.10
          conda activate sky

          # Choose your cloud:

          pip install "skypilot-nightly[aws]"
          pip install "skypilot-nightly[gcp]"
          pip install "skypilot-nightly[azure]"
          pip install "skypilot-nightly[oci]"
          pip install "skypilot-nightly[lambda]"
          pip install "skypilot-nightly[runpod]"
          pip install "skypilot-nightly[fluidstack]"
          pip install "skypilot-nightly[cudo]"
          pip install "skypilot-nightly[ibm]"
          pip install "skypilot-nightly[scp]"
          pip install "skypilot-nightly[vsphere]"
          pip install "skypilot-nightly[kubernetes]"
          pip install "skypilot-nightly[all]"

    .. tab-item:: Latest Release
        :sync: latest-release-tab

        .. code-block:: shell

          # Recommended: use a new conda env to avoid package conflicts.
          # SkyPilot requires 3.7 <= python <= 3.11.
          conda create -y -n sky python=3.10
          conda activate sky

          # Choose your cloud:

          pip install "skypilot[aws]"
          pip install "skypilot[gcp]"
          pip install "skypilot[azure]"
          pip install "skypilot[oci]"
          pip install "skypilot[lambda]"
          pip install "skypilot[runpod]"
          pip install "skypilot[fluidstack]"
          pip install "skypilot[cudo]"
          pip install "skypilot[ibm]"
          pip install "skypilot[scp]"
          pip install "skypilot[vsphere]"
          pip install "skypilot[kubernetes]"
          pip install "skypilot[all]"

    .. tab-item:: From Source
        :sync: from-source-tab

        .. code-block:: shell

          # Recommended: use a new conda env to avoid package conflicts.
          # SkyPilot requires 3.7 <= python <= 3.11.
          conda create -y -n sky python=3.10
          conda activate sky

          git clone https://github.com/skypilot-org/skypilot.git
          cd skypilot

          # Choose your cloud:

          pip install -e ".[aws]"
          pip install -e ".[gcp]"
          pip install -e ".[azure]"
          pip install -e ".[oci]"
          pip install -e ".[lambda]"
          pip install -e ".[runpod]"
          pip install -e ".[fluidstack]"
          pip install -e ".[cudo]"
          pip install -e ".[ibm]"
          pip install -e ".[scp]"
          pip install -e ".[vsphere]"
          pip install -e ".[kubernetes]"
          pip install -e ".[all]"

To use more than one cloud, combine the pip extras:

.. tab-set::

    .. tab-item:: Nightly (recommended)
        :sync: nightly-tab

        .. code-block:: shell

          pip install -U "skypilot-nightly[aws,gcp]"

    .. tab-item:: Latest Release
        :sync: latest-release-tab

        .. code-block:: shell

          pip install -U "skypilot[aws,gcp]"

    .. tab-item:: From Source
        :sync: from-source-tab

        .. code-block:: shell

          pip install -e ".[aws,gcp]"

Alternatively, we also provide a :ref:`Docker image <docker-image>` as a quick way to try out SkyPilot.

.. _verify-cloud-access:

Verifying cloud access
------------------------------------

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
    RunPod: enabled
    Paperspace: enabled
    Fluidstack: enabled
    Cudo: enabled
    IBM: enabled
    SCP: enabled
    vSphere: enabled
    Cloudflare (for R2 object store): enabled
    Kubernetes: enabled

  SkyPilot will use only the enabled clouds to run tasks. To change this, configure cloud credentials, and run sky check.

If any cloud's credentials or dependencies are missing, ``sky check`` will
output hints on how to resolve them. You can also refer to the cloud setup
section :ref:`below <cloud-account-setup>`.

.. tip::

  If your clouds show ``enabled`` --- |:tada:| |:tada:| **Congratulations!** |:tada:| |:tada:| You can now head over to
  :ref:`Quickstart <quickstart>` to get started with SkyPilot.

.. _cloud-account-setup:

Cloud account setup
-------------------

SkyPilot currently supports these cloud providers: AWS, GCP, Azure, OCI, Lambda Cloud, RunPod, Fluidstack, Cudo,
IBM, SCP, VMware vSphere and Cloudflare (for R2 object store).

If you already have cloud access set up on your local machine, run ``sky check`` to :ref:`verify that SkyPilot can properly access your enabled clouds<verify-cloud-access>`.

Otherwise, configure access to at least one cloud using the following guides.

.. _aws-installation:

Amazon Web Services (AWS)
~~~~~~~~~~~~~~~~~~~~~~~~~~~


To get the **AWS access key** required by :code:`aws configure`, please go to the `AWS IAM Management Console <https://us-east-1.console.aws.amazon.com/iam/home?region=us-east-1#/security_credentials>`_ and click on the "Access keys" dropdown (detailed instructions `here <https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html#Using_CreateAccessKey>`__). The **Default region name [None]:** and **Default output format [None]:** fields are optional and can be left blank to choose defaults.

.. code-block:: shell

  # Install boto
  pip install boto3

  # Configure your AWS credentials
  aws configure

To use AWS IAM Identity Center (AWS SSO), see :ref:`here<aws-sso>` for instructions.

**Optional**: To create a new AWS user with minimal permissions for SkyPilot, see :ref:`AWS User Creation <cloud-permissions-aws>`.

.. _installation-gcp:

Google Cloud Platform (GCP)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: shell

  conda install -c conda-forge google-cloud-sdk

  gcloud init

  # Run this if you don't have a credentials file.
  # This will generate ~/.config/gcloud/application_default_credentials.json.
  gcloud auth application-default login

.. tip::

  If you are using multiple GCP projects, list all the projects by :code:`gcloud projects list` and activate one by :code:`gcloud config set project <PROJECT_ID>` (see `GCP docs <https://cloud.google.com/sdk/gcloud/reference/config/set>`_).

.. dropdown:: Common GCP installation errors

    Here some commonly encountered errors and their fixes:

    * ``RemoveError: 'requests' is a dependency of conda and cannot be removed from conda's operating environment`` when running :code:`conda install -c conda-forge google-cloud-sdk` --- run :code:`conda update --force conda` first and rerun the command.
    * ``Authorization Error (Error 400: invalid_request)`` with the url generated by :code:`gcloud auth login` --- install the latest version of the `Google Cloud SDK <https://cloud.google.com/sdk/docs/install>`_ (e.g., with :code:`conda install -c conda-forge google-cloud-sdk`) on your local machine (which opened the browser) and rerun the command.

**Optional**: To create and use a long-lived service account on your local machine, see :ref:`here<gcp-service-account>`.

**Optional**: To create a new GCP user with minimal permissions for SkyPilot, see :ref:`GCP User Creation <cloud-permissions-gcp>`.

Azure
~~~~~~~~~

.. code-block:: shell

  # Login
  az login
  # Set the subscription to use
  az account set -s <subscription_id>

Hint: run ``az account subscription list`` to get a list of subscription IDs under your account.


Oracle Cloud Infrastructure (OCI)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To access Oracle Cloud Infrastructure (OCI), setup the credentials by following `this guide <https://docs.oracle.com/en-us/iaas/Content/API/Concepts/apisigningkey.htm>`__. After completing the steps in the guide, the :code:`~/.oci` folder should contain the following files:

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
  key_file=~/.oci/oci_api_key.pem


Lambda Cloud
~~~~~~~~~~~~~~~~~~

`Lambda Cloud <https://lambdalabs.com/>`_ is a cloud provider offering low-cost GPUs. To configure Lambda Cloud access, go to the `API Keys <https://cloud.lambdalabs.com/api-keys>`_ page on your Lambda console to generate a key and then add it to :code:`~/.lambda_cloud/lambda_keys`:

.. code-block:: shell

  mkdir -p ~/.lambda_cloud
  echo "api_key = <your_api_key_here>" > ~/.lambda_cloud/lambda_keys

Paperspace
~~~~~~~~~~~~~~~~~~

`Paperspace <https://www.paperspace.com/>`_ is a cloud provider that provides access to GPU accelerated VMs. To configure Paperspace access, go to follow `these instructions to generate an API key <https://docs.digitalocean.com/reference/paperspace/api-keys/>`_. Add the API key with:

.. code-block:: shell

  mkdir -p ~/.paperspace
  echo "{'api_key' : <your_api_key_here>}" > ~/.paperspace/config.json

RunPod
~~~~~~~~~~

`RunPod <https://runpod.io/>`__ is a specialized AI cloud provider that offers low-cost GPUs. To configure RunPod access, go to the `Settings <https://www.runpod.io/console/user/settings>`_ page on your RunPod console and generate an **API key**. Then, run:

.. code-block:: shell
  
  pip install "runpod>=1.5.1"
  runpod config


Fluidstack
~~~~~~~~~~~~~~~~~~

`Fluidstack <https://fluidstack.io/>`__ is a cloud provider offering low-cost GPUs. To configure Fluidstack access, go to the `Home <https://console.fluidstack.io/>`__ page on your Fluidstack console to generate an API key and then add the :code:`API key` to :code:`~/.fluidstack/api_key` and the :code:`API token` to :code:`~/.fluidstack/api_token`:

.. code-block:: shell

  mkdir -p ~/.fluidstack
  echo "your_api_key_here" > ~/.fluidstack/api_key
  echo "your_api_token_here" > ~/.fluidstack/api_token


Cudo Compute
~~~~~~~~~~~~~~~~~~

`Cudo Compute <https://www.cudocompute.com/>`__ GPU cloud provides low cost GPUs powered with green energy.

1. Create an API Key by following `this guide <https://www.cudocompute.com/docs/guide/api-keys/>`__.
2. Download and install the `cudoctl <https://www.cudocompute.com/docs/cli-tool/>`__ command line tool
3. Run :code:`cudoctl init`:

.. code-block:: shell

  cudoctl init
    ✔ api key: my-api-key
    ✔ project: my-project
    ✔ billing account: my-billing-account
    ✔ context: default
    config file saved ~/.config/cudo/cudo.yml

  pip install "cudocompute>=0.1.8"

If you want to want to use skypilot with a different Cudo Compute account or project, just run :code:`cudoctl init`: again.




IBM
~~~~~~~~~

To access `IBM's VPC service <https://www.ibm.com/cloud/vpc>`__, store the following fields in ``~/.ibm/credentials.yaml``:

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



Samsung Cloud Platform (SCP)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Samsung Cloud Platform(SCP) provides cloud services optimized for enterprise customers. You can learn more about SCP `here <https://cloud.samsungsds.com/>`__.

To configure SCP access, you need access keys and the ID of the project your tasks will run. Go to the `Access Key Management <https://cloud.samsungsds.com/console/#/common/access-key-manage/list?popup=true>`_ page on your SCP console to generate the access keys, and the Project Overview page for the project ID. Then, add them to :code:`~/.scp/scp_credential` by running:

.. code-block:: shell

  # Create directory if required
  mkdir -p ~/.scp
  # Add the lines for "access_key", "secret_key", and "project_id" to scp_credential file
  echo "access_key = <your_access_key>" >> ~/.scp/scp_credential
  echo "secret_key = <your_secret_key>" >> ~/.scp/scp_credential
  echo "project_id = <your_project_id>" >> ~/.scp/scp_credential

.. note::

  Multi-node clusters are currently not supported on SCP.



VMware vSphere
~~~~~~~~~~~~~~

To configure VMware vSphere access, store the vSphere credentials in :code:`~/.vsphere/credential.yaml`:

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

Cloudflare R2
~~~~~~~~~~~~~~~~~~

Cloudflare offers `R2 <https://www.cloudflare.com/products/r2>`_, an S3-compatible object storage without any egress charges.
SkyPilot can download/upload data to R2 buckets and mount them as local filesystem on clusters launched by SkyPilot. To set up R2 support, run:

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


Kubernetes
~~~~~~~~~~

SkyPilot can also run tasks on on-prem or cloud hosted Kubernetes clusters (e.g., EKS, GKE). The only requirement is a valid kubeconfig at :code:`~/.kube/config`.

.. code-block:: shell

  # Place your kubeconfig at ~/.kube/config
  mkdir -p ~/.kube
  cp /path/to/kubeconfig ~/.kube/config

See :ref:`SkyPilot on Kubernetes <kubernetes-overview>` for more.


Requesting quotas for first time users
--------------------------------------

If your cloud account has not been used to launch instances before, the
respective quotas are likely set to zero or a low limit.  This is especially
true for GPU instances.

Please follow :ref:`Requesting Quota Increase <quota>` to check quotas and request quota
increases before proceeding.

.. _docker-image:

Quick alternative: trying in Docker
------------------------------------------------------

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
    berkeleyskypilot/skypilot-nightly

  docker exec -it sky /bin/bash

If your cloud CLIs are already setup, your credentials (AWS and GCP) will be
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

.. _shell-completion:

Enabling shell completion
-------------------------

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
