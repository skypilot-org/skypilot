.. _airgap:

Setting Up SkyPilot with Airgapping
==================================

SkyPilot is compatible with any airgapped setup that allows downloading our required packages via a proxy (for example via an HTTP proxy or Amazon SSM).
This guide details how to setup SkyPilot in these cases.

.. _airgap-aws-ssm:

AWS SSM
~~~~~~~

AWS Systems Manager Session Manager allows for secure shell access to EC2 instances without direct network access.
This enables an airgapped setup where launched instances don’t have a public IP address but you can still access the instances.

Assume we have a private VPC for our airgapped AWS cluster with the name ``private-vpc`` and a private security group ``private-sg`` where we want to launch a SkyPilot cluster. We can use a very simple yaml file to enable SkyPilot. 

.. code-block:: yaml

    # ~/.sky/config.yaml
    aws:
        vpc_name: <private-vpc>
        security_group_name: <private-sg>
        use_internal_ips: true
        use_ssm: true
        
    # ... Rest of typical configuration ...

With only a small set of additional configuration we can have SkyPilot use the
directed VPC and security group and create a cluster in AWS using private IPs (as a result of ``use_internal_ips: true``) only relying on SSM for connectivity.

See :ref:`Using AWS Systems Manager SSM <aws-ssm>` for further instructions on setting up SSM in SkyPilot including required packages and permissions.

.. _airgap-kubernetes:

Kubernetes with HTTP Proxies
~~~~~~~~~~

SkyPilot can also support airgapped Kubernetes clusters that use an HTTP proxy to enable outbound traffic.

Assume we have a Kubernetes cluster with a corporate HTTP proxy at ``http://proxy-host:3128``. We can use a very simple yaml file to enable SkyPilot.

.. code-block:: yaml