Nebius
======

.. _nebius-service-account:

Service account
----------------

To use *Service Account* authentication, follow these steps:

1. **Create a Service Account** using the Nebius web console.
2. **Generate PEM Keys**:

.. code-block:: shell

   openssl genrsa -out private.pem 4096
   openssl rsa -in private.pem -outform PEM -pubout -out public.pem

3.  **Generate and Save the Credentials File**:

* Save the file as `~/.nebius/credentials.json`.
* Ensure the file matches the expected format below:

.. code-block:: json

     {
         "subject-credentials": {
             "alg": "RS256",
             "private-key": "PKCS#8 PEM with new lines escaped as \n",
             "kid": "public-key-id",
             "iss": "service-account-id",
             "sub": "service-account-id"
         }
     }


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
