Nebius
======

.. _nebius-service-account:

Service account
----------------

To use *Service Account* authentication, follow these steps:

1. **Create a Service Account** using the Nebius web console.
2. **Install and configure the Nebius CLI** following the `Nebius documentation <https://docs.nebius.com/cli/configure>`_.
3. **Generate Service Account Credentials**:

First, you need to get the service account ID and save it to ``$SA_ID`` environment variable.
You can either get it from the Nebius web console or run the following command:

.. code-block:: shell

  export SA_ID=$(nebius iam service-account get-by-name \
    --name <service account name> \
    --format json \
    | jq -r ".metadata.id")

Then, run the following command to generate the service account credentials:

.. code-block:: shell

  nebius iam auth-public-key generate \
  --service-account-id $SA_ID \
  --output ~/.nebius/credentials.json

The following script saves the service account credentials to `~/.nebius/credentials.json`:

See `Nebius documentation on creating authorized keys <https://docs.nebius.com/iam/service-accounts/authorized-keys#create>`_ for more details.

4. **Add the tenant ID information**

Find the tenant ID from the web console, or use ``nebius iam tenant list`` command.

Once the tenant ID is found, run the following command:

.. code-block:: shell

  echo <tenant id> > ~/.nebius/NEBIUS_TENANT_ID.txt

5. **Verify service account credentials**

To verify that the service account credentials are working with SkyPilot, run the following command:

.. code-block:: shell

  sky check nebius

**Important Notes:**

* The `NEBIUS_IAM_TOKEN` file, if present, will take priority for authentication.

Using internal IPs
-----------------------
For security reason, users may only want to use internal IPs for SkyPilot instances.
To do so, you can use SkyPilot's global config file ``~/.sky/config.yaml`` to specify the ``nebius.use_internal_ips`` and ``nebius.ssh_proxy_command`` fields (to see the detailed syntax, see :ref:`config-yaml`):

.. code-block:: yaml

    nebius:
      use_internal_ips: true
      ssh_proxy_command: ssh -W %h:%p -o StrictHostKeyChecking=no myself@my.proxy

The ``nebius.ssh_proxy_command`` field is optional. If SkyPilot is run on a machine that can directly access the internal IPs of the instances, it can be omitted. Otherwise, it should be set to a command that can be used to proxy SSH connections to the internal IPs of the instances.

Using static IPs
-----------------------
To ensure your VM’s IP address remains static across stop-start operations, set the `nebius.use_static_ip_address` field in SkyPilot's global config file (`~/.sky/config.yaml`). For a detailed syntax reference, see :ref:`config-yaml`.

.. code-block:: yaml

    nebius:
      use_static_ip_address: true

Not working with `use_internal_ips`

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
