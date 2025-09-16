.. _api-server-gpu-metrics-setup:

Monitoring Cluster-wide GPU Metrics
===================================

SkyPilot provides native integration with NVIDIA DCGM to surface
real-time GPU metrics directly in the SkyPilot dashboard.

.. image:: ../../../images/metrics/gpu-metrics.png
    :alt: GPU metrics dashboard
    :align: center
    :width: 80%

Prerequisites
-------------

Before you begin, make sure your Kubernetes cluster meets the following
requirements:

* **NVIDIA GPUs** are available on your worker nodes.
* The `NVIDIA device plugin <https://github.com/NVIDIA/k8s-device-plugin>`_
  is installed.
* **DCGM-Exporter** is running on the cluster and exposes metrics on
  port ``9400``.  Most GPU Operator installations already deploy DCGM-Exporter for you.
* `Node Exporter <https://prometheus.io/docs/guides/node-exporter/>`_ is running on the cluster and exposes metrics on port ``9100``. This is required only if you want to monitor the CPU and Memory metrics.

Depend on different Kubernetes environments, some additional setup may be required.

Take `CoreWeave managed Kubernetes cluster <https://docs.coreweave.com/docs/products/cks>`_ for example:

The node exporter and the dcgm exporter are already deployed on the cluster. You can verify this by running the following commands:

.. code-block:: bash

    kubectl get pods -n cw-exporters

To make the Prometheus server scrape the metrics from the node exporter and the dcgm exporter, you need to create a service for each of them.

.. code-block:: bash

    kubectl create -f https://raw.githubusercontent.com/skypilot-org/skypilot/refs/heads/master/examples/metrics/dcgm_service.yaml -n cw-exporters
    kubectl create -f https://raw.githubusercontent.com/skypilot-org/skypilot/refs/heads/master/examples/metrics/node_exporter_service.yaml -n cw-exporters

Check that the service endpoints are created by running the following commands:

.. code-block:: bash

    kubectl get endpoints -n cw-exporters

If this is not the Kubernetes cluster you will be deploying the SkyPilot API server on, install the SkyPilot Prometheus server:

.. code-block:: bash

    helm upgrade --install skypilot skypilot/skypilot-prometheus-server --devel \
     --namespace skypilot \
     --create-namespace

If you are using the Nebius Kubernetes cluster, refer to :ref:`api-server-gpu-metrics-setup-nebius` for how to setup the GPU metrics.

.. _api-server-setup-dcgm-metrics-scraping:

Set up DCGM metrics scraping
----------------------------

Deploy the SkyPilot API server with GPU metrics enabled:

.. code-block:: bash

   helm upgrade --install skypilot skypilot/skypilot-nightly --devel \
     --namespace skypilot \
     --create-namespace \
     --reuse-values \
     --set apiService.metrics.enabled=true \
     --set prometheus.enabled=true \
     --set grafana.enabled=true

The flags do the following:

* ``apiService.metrics.enabled`` – turn on the ``/metrics`` endpoint in the
  SkyPilot API server.
* ``prometheus.enabled`` – deploy a Prometheus instance pre-configured to
  scrape both the SkyPilot API server and DCGM-Exporter.
* ``grafana.enabled`` – deploy Grafana with an out-of-the-box dashboard that will be embedded in the SkyPilot dashboard.

What metrics are exposed?
---------------------------

By default, the SkyPilot dashboard exposes the following metrics:

* GPU utilization
* GPU memory usage
* GPU power usage
* GPU temperature
* CPU utilization
* Memory usage


However, all `metrics <https://github.com/NVIDIA/dcgm-exporter/blob/main/etc/dcp-metrics-included.csv>`__ exported by DCGM exporter
can be accessed via Prometheus/Grafana including GPU errors, NVLink stats and more.
