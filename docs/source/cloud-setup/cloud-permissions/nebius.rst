Nebius
======

.. _nebius-service-account:

Service account
----------------

To use *Service Account* authentication, follow these steps:

1. **Create a Service Account** using the Nebius web console.
2. **Generate Service Account Credentials**:

Run the following command to generate the service account credentials:

.. code-block:: shell

   nebius iam auth-public-key generate \
   --service-account-id $SA_ID \
   --output ~/.nebius/credentials.json

The following script saves the service account credentials to `~/.nebius/credentials.json`:

See `Nebius documentation <https://docs.nebius.com/iam/service-accounts/authorized-keys#create>`_ for more details.

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

When using a proxy machine, don't forget to enable `AllowTcpForwarding` on the proxy host:

.. code-block:: bash

    sudo sed -i 's/^#\?AllowTcpForwarding.*/AllowTcpForwarding yes/' /etc/ssh/sshd_config
    sudo systemctl restart sshd
