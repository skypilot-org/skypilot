.. _kubernetes-ports:

Ports on Kubernetes
========================


.. note::
    This is a guide on how to configure an exisiting Kubernetes cluster (along with the caveats involved)
    in order to successfully expose ports and services externally through SkyPilot.

    If you are a SkyPilot user and your cluster has already been set up to expose ports,
    :ref:`Opening Ports <ports>` explains how to open ports through SkyPilot.

SkyPilot's Kubernetes Ports support is designed to be flexible to work with a wide variety of Kubernetes clusters.
Additionally, it uses the established Kubernetes Networking model and APIs internally.
Please refer to the official `Kubernetes Networking documentation <https://kubernetes.io/docs/concepts/services-networking/>`_ for more details.
Currently, SkyPilot supports two modes to expose ports:

* :ref:`Load Balancer <loadbalancer>`
* :ref:`Ingress <ingress>`

.. _loadbalancer:


Load Balancer
^^^^^^^^^^^^^

This mode exposes ports through a Kubernetes `LoadBalancer <https://kubernetes.io/docs/concepts/services-networking/service/#loadbalancer>`_ Service.
To use this mode, you must have a Kubernetes cluster that supports LoadBalancer Services.
Most of the cloud based Kubernetes providers support this mode out of the box, but do ensure that your cluster supports it before proceeding.
If you have a bare metal cluster, `MetalLB <https://metallb.universe.tf/>`_ can be used to support LoadBalancer Services.
When using this mode, SkyPilot will create a single LoadBalancer Service for all ports that you expose.
Refer to `kubernetes-loadbalancer.yml.j2` for the exact template that creates Kubernetes resources using this mode.
Each port can be accessed using the LoadBalancer's external IP address and the port number.
:ref:`sky status <sky-status>` can be used to view the external endpoints for each/all ports.

This mode in most cloud based Kubernetes clusters creates an external Load Balancer. For instance,
GKE creates a pass-through Load Balancer (see `here <https://cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer>`_)
and AWS creates a Network Load Balancer (see `here <https://docs.aws.amazon.com/eks/latest/userguide/network-load-balancing.html>`_).
This generally incurs an extra cost, so please check with your cloud provider for pricing details.
Currently, the load balancer pricing is not taken into account by SkyPilot.

.. note::
    This is the default mode used in SkyPilot config for opening ports.
    See the kubernetes section in :ref:`SkyPilot Config <config-yaml>` for more details.

.. note::
    The default LoadBalancer implementation in EKS selects a random port from the list of opened ports for the
    LoadBalancer's health check. This can cause issues if the selected port does not have a service running behind it.
    For example, a SkyPilot task can expose 5 ports but only 2 of them have services running behind them.
    Then, if EKS selects a port that does not have a service running behind it, the LoadBalancer will not pass the healthcheck
    and won't be able to assign an external IP address successfully.

.. note::
    This mode is currently not supported when using a kind cluster created using `sky local up`.

.. _ingress:


Ingress
^^^^^^^

This mode exposes ports through a Kubernetes `Ingress <https://kubernetes.io/docs/concepts/services-networking/ingress/>`_.
In order to use this mode, manual configuration is needed on the Kubernetes cluster.
This generally involves installing an Ingress Controller.
Currently, SkyPilot only supports the NGINX Ingress Controller. Refer to its `documentation <https://kubernetes.github.io/ingress-nginx/>`_ for installation instructions.
When using this mode, SkyPilot creates an ingress resource and a ClusterIP service for each port opened.
The port can be accessed externally by using the Ingress URL plus a path prefix of the form /skypilot/{cluster_name_on_cloud}/{port}.
Refer to `kubernetes-ingress.yml.j2` for the exact template that creates Kubernetes resources using this mode.
Once again, :ref:`sky status <sky-status>` can be used to view the external endpoints for each/all ports.

Since this mode exposes a port under a sub path, it runs into issues when the service running behind the port
expects to be accessed at the root path. For example, if the service running behind the port is a Jupyter server,
it expects assets to be present at the root path. Currently, we do not have a solution for this issue.
Please use the :ref:`LoadBalancer <loadbalancer>` mode if you run into this issue.

.. note::
    To use this mode, you need to first install the ingress controller then update the SkyPilot config.
    Generally, you will have to set `ports: ingress` in the kubernetes section of the SkyPilot config.
    See :ref:`SkyPilot Config <config-yaml>` for more details.

.. note::
    Currently, SkyPilot does not support opening ports on a Kubernetes cluster using the `Gateway API <https://kubernetes.io/docs/concepts/services-networking/gateway/>`.
    If you are interested in this feature, please reach out to us.
