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

When running SkyServe in an internal network (especially on Kubernetes), you may need to configure how services are exposed. By default, SkyServe creates external LoadBalancer services to expose endpoints, but this may not be desirable or possible in air-gapped or internal network environments.

SkyPilot provides several options for running SkyServe internally on Kubernetes:

Option 1: Pod IP Mode (Internal Only)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

For services that only need to be accessed from within the Kubernetes cluster, use the ``podip`` port mode. This exposes services using the Pod's internal IP address without creating any external LoadBalancer or NodePort services.

.. code-block:: yaml

    # ~/.sky/config.yaml
    kubernetes:
      ports: podip

With this configuration:

- SkyServe endpoints will use Pod IPs (e.g., ``10.244.0.15:30001``)
- Services are only accessible from within the cluster network
- No external cloud load balancers are created
- Ideal when service consumers are also running inside the cluster

To access the service from within the cluster:

.. code-block:: console

    # Get the service endpoint
    $ sky serve status my-service

    # Access from another pod in the cluster
    $ curl http://10.244.0.15:30001/v1/models

See :ref:`Pod IP Mode <kubernetes-podip>` for more details.

Option 2: Internal Load Balancers
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you need stable internal endpoints (Pod IPs can change when pods are rescheduled), use internal load balancers. These create load balancers that are only accessible within your VPC/internal network.

.. code-block:: yaml

    # ~/.sky/config.yaml
    kubernetes:
      custom_metadata:
        annotations:
          # For GCP/GKE
          networking.gke.io/load-balancer-type: "Internal"
          # For AWS/EKS
          service.beta.kubernetes.io/aws-load-balancer-internal: "true"
          # For Azure/AKS
          service.beta.kubernetes.io/azure-load-balancer-internal: "true"

With internal load balancers:

- SkyServe endpoints use stable internal IPs assigned by the cloud provider
- Services are accessible from within the VPC but not from the public internet
- Provides load balancing and health checking features

See :ref:`Internal Load Balancers <kubernetes-loadbalancer>` for more details.

Option 3: Nginx Ingress with Internal Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

For more complex routing requirements, you can use Nginx Ingress configured with internal-only access:

.. code-block:: yaml

    # ~/.sky/config.yaml
    kubernetes:
      ports: ingress

Ensure your Nginx Ingress Controller is configured with internal-only access by applying the appropriate cloud-specific annotations to the ingress-nginx-controller service.

See :ref:`Nginx Ingress <kubernetes-ingress>` for setup instructions.

SkyServe Controller on Kubernetes
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

When running SkyServe in internal networks, you may also want to run the SkyServe controller itself on Kubernetes. This keeps all components within your internal network:

.. code-block:: yaml

    # ~/.sky/config.yaml
    serve:
      controller:
        resources:
          cloud: kubernetes
        # Optional: Enable high availability for production deployments
        high_availability: true

    kubernetes:
      ports: podip  # or use internal load balancer annotations

This configuration ensures:

- The SkyServe controller runs as a pod in your Kubernetes cluster
- The controller can communicate with replica pods using internal networking
- All traffic stays within your cluster/VPC

See :ref:`SkyServe Controller <customizing-sky-serve-controller-resources>` and :ref:`High Availability Controller <sky-serve-high-availability-controller>` for more details.

Example: Complete Internal SkyServe Setup
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Here is a complete example configuration for running SkyServe entirely within an internal Kubernetes network:

.. code-block:: yaml

    # ~/.sky/config.yaml

    # Run SkyServe controller on Kubernetes with HA
    serve:
      controller:
        resources:
          cloud: kubernetes
        high_availability: true

    # Use Pod IPs for internal-only access
    kubernetes:
      ports: podip

      # Optional: Configure HTTP proxy for air-gapped clusters
      pod_config:
        spec:
          containers:
            - env:
                - name: HTTP_PROXY
                  value: http://proxy-host:3128
                - name: HTTPS_PROXY
                  value: http://proxy-host:3128

Then deploy your service:

.. code-block:: console

    $ sky serve up my-service.yaml

    # Check status - endpoint will be an internal Pod IP
    $ sky serve status my-service

    # Access from within the cluster
    $ kubectl run curl --rm -it --image=curlimages/curl -- \
        curl http://<pod-ip>:30001/health

