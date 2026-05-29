.. _airgap:

Setting Up SkyPilot with Airgapped Environments
================================================

SkyPilot is compatible with any airgapped setup that allows downloading our required packages via a proxy (for example via an HTTP proxy or Amazon SSM).
This guide details how to setup SkyPilot in these cases.

.. _airgap-kubernetes:

Kubernetes with HTTP proxies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

SkyPilot can also support airgapped Kubernetes clusters that use an HTTP proxy to enable outbound traffic.

Given an airgapped Kubernetes cluster with a corporate HTTP proxy at ``http://proxy-host:3128``, a simple config can enable SkyPilot on the cluster.
The following yaml is the SkyPilot config which can be edited at ``http://<api-server-url>/dashboard/config``. See the :ref:`yaml-spec` spec for more details. 

.. code-block:: yaml

    # ~/.sky/config.yaml
    kubernetes:
      pod_config:
        spec:
          containers:
            - env:
                - name: HTTP_PROXY
                  value: http://proxy-host:3128
                - name: HTTPS_PROXY
                  value: http://proxy-host:3128
                - name: NO_PROXY
                  value: localhost,127.0.0.1
                - name: http_proxy
                  value: http://proxy-host:3128
                - name: https_proxy
                  value: http://proxy-host:3128
                - name: no_proxy
                  value: localhost,127.0.0.1

``NO_PROXY`` is used to specify the addresses that should be excluded from the proxy, typically internal addresses.
Because different tools and libraries use different environment variable names we include all the possible names to ensure compatibility.

This configuration directs SkyPilot pods to use the corporate proxy for outbound traffic.

.. _airgap-aws-ssm:

AWS SSM
~~~~~~~

:ref:`AWS SSM <aws-ssm>` allows for secure shell access to EC2 instances without direct network access.
This enables an airgapped setup where instances without public IP addresses can still be accessed.

Given an airgapped AWS cluster with a private VPC ``private-vpc`` and a private security group ``private-sg``, a simple config can enable SkyPilot on the cluster.
The following yaml is the SkyPilot config which can be edited at ``http://<api-server-url>/dashboard/config``. See the :ref:`yaml-spec` spec for more details. 

.. code-block:: yaml

    # ~/.sky/config.yaml
    aws:
        vpc_names: <private-vpc>
        security_group_name: <private-sg>
        use_internal_ips: true
        use_ssm: true

The above configuration directs SkyPilot to use SSM for connectivity to clusters, using the configured VPC and security group to create a cluster in AWS using private IPs (as a result of ``use_internal_ips: true``).

See :ref:`Using AWS Systems Manager SSM <aws-ssm>` for further instructions on setting up SSM in SkyPilot, including required packages and permissions.

