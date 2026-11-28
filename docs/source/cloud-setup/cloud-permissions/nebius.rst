Nebius
======

.. _nebius-service-account:

Service account
----------------

A service account lets SkyPilot use Nebius without a personal access token,
which expires. Use one for a shared :ref:`API server
<sky-api-server-configure-credentials>`, CI, or any long-running setup.

You need the `Nebius CLI <https://docs.nebius.com/cli/configure>`_, signed in
as a user who can manage service accounts and groups in your tenant.

.. important::

   The service account needs the **editor** role in your tenant (step 2 grants
   it). Without it, ``sky check nebius`` and launches fail with a permission
   error.

1. **Set the IDs used below.** Fill these in once; you can then paste every
   later command unchanged.

   .. code-block:: shell

      # Tenant ID: from the web console, or `nebius iam tenant list`.
      export NEBIUS_TENANT_ID=<tenant id>
      # Project to hold the service account: from the web console, or
      # `nebius iam project list --parent-id $NEBIUS_TENANT_ID`.
      export NEBIUS_PROJECT_ID=<project id>
      # Name of the service account to create, or of an existing one.
      export NEBIUS_SA_NAME=skypilot

2. **Create the service account and give it the editor role.** If the
   account already exists, ``service-account create`` reports that and the
   rest still runs.

   .. code-block:: shell

      nebius iam service-account create \
        --parent-id $NEBIUS_PROJECT_ID \
        --name $NEBIUS_SA_NAME
      export NEBIUS_SA_ID=$(nebius iam service-account get-by-name \
        --parent-id $NEBIUS_PROJECT_ID \
        --name $NEBIUS_SA_NAME \
        --format jsonpath='{.metadata.id}')
      # Editor role: add the account to the tenant's editors group.
      export NEBIUS_EDITORS_GROUP_ID=$(nebius iam group get-by-name \
        --parent-id $NEBIUS_TENANT_ID \
        --name editors \
        --format jsonpath='{.metadata.id}')
      nebius iam group-membership create \
        --parent-id $NEBIUS_EDITORS_GROUP_ID \
        --member-id $NEBIUS_SA_ID

3. **Save its key and your tenant ID** where SkyPilot reads them (`more on
   authorized keys
   <https://docs.nebius.com/iam/service-accounts/authorized-keys#create>`_):

   .. code-block:: shell

      nebius iam auth-public-key generate \
        --service-account-id $NEBIUS_SA_ID \
        --output ~/.nebius/credentials.json
      echo $NEBIUS_TENANT_ID > ~/.nebius/NEBIUS_TENANT_ID.txt

4. **Verify.** SkyPilot prefers an access token in
   ``~/.nebius/NEBIUS_IAM_TOKEN.txt`` (the :ref:`installation guide
   <installation>` creates one) over the service account, so remove it first:

   .. code-block:: shell

      rm -f ~/.nebius/NEBIUS_IAM_TOKEN.txt
      sky check nebius

   ``Nebius`` should be listed under *Enabled infra*.

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
price. By default, SkyPilot launches price-taking VMs; no pricing policy is
required. Prices can change while a VM runs, and the spot price can be higher
than the on-demand price. Displayed costs are estimates; ``max_hourly_cost``
filters estimated costs and does not enforce a running price limit.

Use ``sky jobs launch task.yaml`` for managed recovery after preemption.

Capping the spot price
~~~~~~~~~~~~~~~~~~~~~~

To cap what a spot VM pays, create a Nebius `pricing policy
<https://docs.nebius.com/signup-billing/manage-pricing-policy>`_ with a max price
per GPU-hour and point SkyPilot at it by name. A policy belongs to one project
(one region) and covers one platform, so create one per platform in every
project you launch spot VMs in, under a name that includes the platform, e.g.
``spot-max-gpu-h100-sxm``. Then set:

.. code-block:: yaml

    nebius:
      spot_pricing_policy_name: spot-max-{platform}

SkyPilot replaces ``{platform}`` with the VM's platform and looks the policy up
in the project of the region it launches in:

- If it finds the policy, the VM references it and is preempted rather than pay
  more than the max price.
- If the policy is blocking new VMs (the spot price is above its max price),
  SkyPilot fails over to the next region, as for a capacity error. Managed jobs
  therefore run where the spot price is still below your cap.
- If there is no such policy in that project, SkyPilot logs a warning and
  launches the VM at the current spot price, without a cap.

See :ref:`nebius.spot_pricing_policy_name <config-yaml-nebius-spot-pricing-policy-name>`
for details.

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
