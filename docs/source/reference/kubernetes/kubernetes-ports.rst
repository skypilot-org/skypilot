.. _kubernetes-ports:

Exposing Services on Kubernetes
===============================

.. note::
    This is a guide on how to configure an existing Kubernetes cluster (along with the caveats involved) to successfully expose ports and services externally through SkyPilot.

    If you are a SkyPilot user and your cluster has already been set up to expose ports,
    :ref:`Opening Ports <ports>` explains how to expose services in your task through SkyPilot.

SkyServe and SkyPilot clusters can :ref:`open ports <ports>` to expose services. For SkyPilot
clusters running on Kubernetes, we support either of two modes to expose ports:

* :ref:`LoadBalancer Service <kubernetes-loadbalancer>` (default)
* :ref:`Nginx Ingress <kubernetes-ingress>`


By default, SkyPilot creates a `LoadBalancer Service <https://kubernetes.io/docs/concepts/services-networking/service/>`__ on your Kubernetes cluster to expose the port.

If your cluster does not support LoadBalancer services, SkyPilot can also use `an existing Nginx IngressController <https://kubernetes.github.io/ingress-nginx/>`_ to create an `Ingress <https://kubernetes.io/docs/concepts/services-networking/ingress/>`_ to expose your service.

.. _kubernetes-loadbalancer:

LoadBalancer Service
--------------------

This mode exposes ports through a Kubernetes `LoadBalancer Service <https://kubernetes.io/docs/concepts/services-networking/service/#loadbalancer>`__. This is the default mode used by SkyPilot.

To use this mode, you must have a Kubernetes cluster that supports LoadBalancer Services:

* On Google GKE, Amazon EKS or other cloud-hosted Kubernetes services, this mode is supported out of the box and no additional configuration is needed.
* On bare metal and self-managed Kubernetes clusters, `MetalLB <https://metallb.universe.tf/>`_ can be used to support LoadBalancer Services.

When using this mode, SkyPilot will create a single LoadBalancer Service for all ports that you expose on a cluster.
Each port can be accessed using the LoadBalancer's external IP address and the port number. Use :code:`sky status --endpoints <cluster>` to view the external endpoints for all ports.

In cloud based Kubernetes clusters, this will automatically create an external Load Balancer.
GKE creates a `Pass-through Load Balancer <https://cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer>`__
and AWS creates a `Network Load Balancer <https://docs.aws.amazon.com/eks/latest/userguide/network-load-balancing.html>`__.
These load balancers will be automatically terminated when the cluster is deleted.

.. note::
    LoadBalancer services are not supported on kind clusters created using :code:`sky local up`.

.. note::
    The default LoadBalancer implementation in EKS selects a random port from the list of opened ports for the
    `LoadBalancer's health check <https://docs.aws.amazon.com/elasticloadbalancing/latest/network/target-group-health-checks.html>`_. This can cause issues if the selected port does not have a service running behind it.


    For example, if a SkyPilot task exposes 5 ports but only 2 of them have services running behind them, EKS may select a port that does not have a service running behind it and the LoadBalancer will not pass the healthcheck. As a result, the service will not be assigned an external IP address.

    To work around this issue, make sure all your ports have services running behind them.

Internal load balancers
^^^^^^^^^^^^^^^^^^^^^^^

To restrict your services to be accessible only within the cluster, you can set all SkyPilot services to use `internal load balancers <https://kubernetes.io/docs/concepts/services-networking/service/#internal-load-balancer>`_.

Depending on your cloud, set the appropriate annotation in the SkyPilot config file (``~/.sky/config.yaml``):

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


.. _kubernetes-ingress:

Nginx Ingress
-------------

This mode exposes ports by creating a Kubernetes `Ingress <https://kubernetes.io/docs/concepts/services-networking/ingress/>`_ backed by an existing `Nginx Ingress Controller <https://kubernetes.github.io/ingress-nginx/>`_.

To use this mode:

1. Install the Nginx Ingress Controller on your Kubernetes cluster. Refer to the `documentation <https://kubernetes.github.io/ingress-nginx/deploy/>`_ for installation instructions specific to your environment.
2. Verify that the ``ingress-nginx-controller`` service has a valid external IP:

.. code-block:: bash

    $ kubectl get service ingress-nginx-controller -n ingress-nginx

    # Example output:
    # NAME                             TYPE                CLUSTER-IP    EXTERNAL-IP     PORT(S)
    # ingress-nginx-controller         LoadBalancer        10.24.4.254   35.202.58.117   80:31253/TCP,443:32699/TCP


.. note::
    If the ``EXTERNAL-IP`` field is ``<none>``, you can manually
    specify the Ingress IP or hostname through the ``skypilot.co/external-ip``
    annotation on the ``ingress-nginx-controller`` service. In this case,
    having a valid ``EXTERNAL-IP`` field is not required.

    For example, if your ``ingress-nginx-controller`` service is ``NodePort``:

    .. code-block:: bash

      # Add skypilot.co/external-ip annotation to the nginx ingress service.
      # Replace <IP> in the following command with the IP you select.
      # Can be any node's IP if using NodePort service type.
      $ kubectl annotate service ingress-nginx-controller skypilot.co/external-ip=<IP> -n ingress-nginx

    If the ``EXTERNAL-IP`` field is ``<none>`` and the ``skypilot.co/external-ip`` annotation does not exist,
    SkyPilot will use ``localhost`` as the external IP for the Ingress,
    and the endpoint may not be accessible from outside the cluster.


3. Update the :ref:`SkyPilot config <config-yaml>` at :code:`~/.sky/config.yaml` to use the ingress mode.

.. code-block:: yaml

    kubernetes:
      ports: ingress

.. tip::

    For RKE2 and K3s, the pre-installed Nginx ingress is not correctly configured by default. Follow the `bare-metal installation instructions <https://kubernetes.github.io/ingress-nginx/deploy/#bare-metal-clusters/>`_ to set up the Nginx ingress controller correctly.


When using this mode, SkyPilot creates an ingress resource and a ClusterIP service for each port opened. The port can be accessed externally by using the Ingress URL plus a path prefix of the form :code:`/skypilot/{pod_name}/{port}`.

Use :code:`sky status --endpoints <cluster>` to view the full endpoint URLs for all ports.

.. code-block::

    $ sky status --endpoints mycluster
    8888: http://34.173.152.251/skypilot/test-2ea4/8888

.. note::

    When exposing a port under a sub-path such as an ingress, services expecting root path access, (e.g., Jupyter notebooks) may face issues. To resolve this, configure the service to operate under a different base URL. For Jupyter, use `--NotebookApp.base_url <https://jupyter-notebook.readthedocs.io/en/5.7.4/config.html>`_ flag during launch. Alternatively, consider using :ref:`LoadBalancer <kubernetes-loadbalancer>` mode.
