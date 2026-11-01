Nebius
======

.. _nebius-service-account:

Service account
----------------

A service account lets SkyPilot use Nebius without a personal access token,
which expires. Use one for a shared :ref:`API server
<sky-api-server-configure-credentials>`, CI, or any long-running setup.

The steps below create the service account, give it access, and save its key
where SkyPilot reads it. You need the `Nebius CLI
<https://docs.nebius.com/cli/configure>`_, signed in as a user who can manage
service accounts and groups in your tenant.

.. important::

   The service account needs the **editor** role in your tenant, which it gets
   by joining the tenant's ``editors`` group (step 3). Without it,
   ``sky check nebius`` or launching clusters fails with a permission error.

1. **Set the IDs used below.** Fill these in once. Every later command reads
   them, so you can paste those commands unchanged.

   .. code-block:: shell

      # Tenant ID: from the web console, or `nebius iam tenant list`.
      export NEBIUS_TENANT_ID=<tenant id>
      # Project to hold the service account: from the web console, or
      # `nebius iam project list --parent-id $NEBIUS_TENANT_ID`.
      export NEBIUS_PROJECT_ID=<project id>
      # Name of the service account to create, or of an existing one.
      export NEBIUS_SA_NAME=skypilot

2. **Create the service account.** Skip this command if it already exists.

   .. code-block:: shell

      nebius iam service-account create \
        --parent-id $NEBIUS_PROJECT_ID \
        --name $NEBIUS_SA_NAME

   Then look up its ID:

   .. code-block:: shell

      export SA_ID=$(nebius iam service-account get-by-name \
        --parent-id $NEBIUS_PROJECT_ID \
        --name $NEBIUS_SA_NAME \
        --format jsonpath='{.metadata.id}')

   .. note::

      Service accounts belong to a project, so the lookup needs
      ``--parent-id``. Without it, and with no default parent ID in your
      Nebius CLI profile, the command fails with ``Invalid nid format ''``.

3. **Give it the editor role.** Add the service account to your tenant's
   ``editors`` group, which lets SkyPilot create and manage VMs, disks, and
   networks:

   .. code-block:: shell

      export NEBIUS_EDITORS_GROUP_ID=$(nebius iam group get-by-name \
        --parent-id $NEBIUS_TENANT_ID \
        --name editors \
        --format jsonpath='{.metadata.id}')
      nebius iam group-membership create \
        --parent-id $NEBIUS_EDITORS_GROUP_ID \
        --member-id $SA_ID

4. **Generate a key** and save it where SkyPilot reads it,
   ``~/.nebius/credentials.json``:

   .. code-block:: shell

      nebius iam auth-public-key generate \
        --service-account-id $SA_ID \
        --output ~/.nebius/credentials.json

   See the `Nebius documentation on authorized keys
   <https://docs.nebius.com/iam/service-accounts/authorized-keys#create>`_ for
   more details.

5. **Save the tenant ID** where SkyPilot reads it:

   .. code-block:: shell

      echo $NEBIUS_TENANT_ID > ~/.nebius/NEBIUS_TENANT_ID.txt

6. **Verify** that SkyPilot can use the service account:

   .. code-block:: shell

      sky check nebius

   Under enabled infra, the output lists ``Nebius`` with ``compute``.

.. important::

   If ``~/.nebius/NEBIUS_IAM_TOKEN.txt`` exists, SkyPilot uses that token
   instead of the service account. The :ref:`installation guide
   <installation>` creates this file. Remove it to use the service account:

   .. code-block:: shell

      rm ~/.nebius/NEBIUS_IAM_TOKEN.txt

Next steps:

- To use this service account with a remote API server, see
  :ref:`sky-api-server-configure-credentials` (Nebius tab).
- To use a different service account per :ref:`workspace <workspaces>`, set
  ``nebius.credentials_file_path`` in the workspace config.
- Nebius Object Storage uses a separate access key; see the Nebius section of
  the :ref:`installation guide <installation>`.

Using internal IPs
-----------------------
For security reasons, users may only want to use internal IPs for SkyPilot instances.
To do so, you can update the SkyPilot config (for a remote API server, through the :ref:`dashboard <sky-api-server-config>`) to specify the ``nebius.use_internal_ips`` and ``nebius.ssh_proxy_command`` fields (to see the detailed syntax, see :ref:`config-yaml`):

.. code-block:: yaml

    nebius:
      use_internal_ips: true
      ssh_proxy_command: ssh -W %h:%p -o StrictHostKeyChecking=no myself@my.proxy

The ``nebius.ssh_proxy_command`` field is optional. If SkyPilot is run on a machine that can directly access the internal IPs of the instances, it can be omitted. Otherwise, it should be set to a command that can be used to proxy SSH connections to the internal IPs of the instances.

Using static IPs
-----------------------
To ensure your VM’s IP address remains static across stop-start operations, set the ``nebius.use_static_ip_address`` field in SkyPilot's global config file (``~/.sky/config.yaml``). For a detailed syntax reference, see :ref:`config-yaml`.

.. code-block:: yaml

    nebius:
      use_static_ip_address: true

.. note::

   This makes the VM's *public* IP static, so it has no effect when
   ``nebius.use_internal_ips`` is ``true``.

Spot VMs
--------

Set ``resources.use_spot: true`` to explicitly accept the current Nebius spot
price. SkyPilot launches price-taking VMs; no pricing policy is required.
Prices can change while a VM runs, and SkyPilot does not set a maximum bid.
Displayed costs are estimates; ``max_hourly_cost`` filters estimated costs
and does not enforce a running price limit.

Use ``sky jobs launch task.yaml`` for managed recovery after preemption.

Migrating existing preemptible VMs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Upgrading SkyPilot does not opt existing VMs into spot pricing. When Nebius
enables auctions for a region and SKU, legacy preemptible VMs are stopped
and cannot restart until their owners explicitly opt in.

To preserve an existing cluster:

1. Stop it with ``sky stop <cluster-name>`` if it is still running.
2. Using the Nebius console or API, explicitly select price-taking spot pricing
   for **each stopped VM**, including the head and workers. In the API, set
   ``spec.follows_spot_price`` to ``{}`` and preserve the other VM settings.
3. Run ``sky start <cluster-name>`` after every VM has been opted in.

SkyPilot reports a migration error when asked to restart a legacy VM; it does
not change the VM's pricing consent automatically.
