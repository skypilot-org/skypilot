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
        vpc_name: <private-vpc>
        security_group_name: <private-sg>
        use_internal_ips: true
        use_ssm: true

The above configuration directs SkyPilot to use SSM for connectivity to clusters, using the configured VPC and security group to create a cluster in AWS using private IPs (as a result of ``use_internal_ips: true``).

See :ref:`Using AWS Systems Manager SSM <aws-ssm>` for further instructions on setting up SSM in SkyPilot, including required packages and permissions.


.. _airgap-skyserve:

SkyServe in Internal Networks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, SkyServe creates external LoadBalancer services to expose endpoints. For air-gapped or internal network environments, you can configure SkyPilot to keep services internal.

**Which option should I use?**

.. list-table::
   :header-rows: 1
   :widths: 25 40 35

   * - Option
     - Best For
     - Endpoint Type
   * - :ref:`Pod IP Mode <kubernetes-podip>`
     - Simple internal access, dev/testing
     - Pod IP (e.g., ``10.244.0.15:30001``)
   * - :ref:`Internal Load Balancers <kubernetes-loadbalancer>`
     - Production, stable endpoints needed
     - Internal LB IP (e.g., ``10.0.1.50:30001``)
   * - :ref:`Nginx Ingress <kubernetes-ingress>`
     - Path-based routing, shared ingress
     - Ingress URL with path prefix

Pod IP Mode
^^^^^^^^^^^

The simplest option for internal-only access. No Kubernetes Services or LoadBalancers are created.

.. code-block:: yaml

    # ~/.sky/config.yaml
    kubernetes:
      ports: podip

Services are only accessible from within the cluster network. See :ref:`Pod IP Mode <kubernetes-podip>` for details.

Internal Load Balancers
^^^^^^^^^^^^^^^^^^^^^^^

For stable internal IPs that persist across pod restarts, use internal load balancers:

.. code-block:: yaml

    # ~/.sky/config.yaml
    kubernetes:
      custom_metadata:
        annotations:
          # GKE
          networking.gke.io/load-balancer-type: "Internal"
          # EKS
          service.beta.kubernetes.io/aws-load-balancer-internal: "true"
          # AKS
          service.beta.kubernetes.io/azure-load-balancer-internal: "true"

See :ref:`Internal Load Balancers <kubernetes-loadbalancer>` for details.

Running the Controller on Kubernetes
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To keep all SkyServe components within your internal network, run the controller on Kubernetes:

.. code-block:: yaml

    # ~/.sky/config.yaml
    serve:
      controller:
        resources:
          cloud: kubernetes
        # Optional: Enable HA for production
        high_availability: true

    kubernetes:
      ports: podip

See :ref:`Customizing SkyServe Controller <customizing-sky-serve-controller-resources>` for more options.

Example: Deploying a Service
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1. Configure SkyPilot for internal networking (using Pod IP mode):

   .. code-block:: yaml

       # ~/.sky/config.yaml
       kubernetes:
         ports: podip

2. Deploy your service:

   .. code-block:: console

       $ sky serve up my-service.yaml -n my-service

3. Get the internal endpoint:

   .. code-block:: console

       $ sky serve status my-service
       Service         Uptime  Status  Replicas  Endpoint
       my-service      5m      READY   2/2       10.244.0.15:30001

4. Access from within the cluster:

   .. code-block:: console

       # From another pod in the cluster
       $ curl http://10.244.0.15:30001/health

