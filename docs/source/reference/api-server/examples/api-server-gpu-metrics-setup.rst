.. _api-server-gpu-metrics-setup:

Monitoring Cluster-wide GPU Metrics
===================================

The SkyPilot provides native integration with Nvidia DCGM to surface 
real-time GPU metrics directly in the SkyPilot dashboard. 

TODO: Add a screenshot of the GPU metrics dashboard.

Prerequisites
-------------

Before you begin, make sure your Kubernetes cluster meets the following
requirements:

* **NVIDIA GPUs** are available on your worker nodes.
* The `NVIDIA device plugin <https://github.com/NVIDIA/k8s-device-plugin>`__
  and the NVIDIA **GPU Operator** are installed.
* **DCGM-Exporter** is running on every GPU node and exposes metrics on
  port ``9400``.  Most GPU Operator installations already deploy DCGM-Exporter for you.

Set up GPU metrics
------------------

Step 1 - Set up scraping for DCGM metrics
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Create a Service that selects the DCGM-Exporter Pods and annotates the Service so Prometheus automatically scrapes it:

.. code-block:: yaml

  apiVersion: v1
  kind: Service
  metadata:
    name: dcgm-exporter
    namespace: default  # change if needed
    labels:
      app: dcgm-exporter
    annotations:
      prometheus.io/scrape: "true"
      prometheus.io/port: "9400"
      prometheus.io/path: "/metrics"
  spec:
    selector:
      app.kubernetes.io/name: dcgm-exporter
    ports:
    - name: metrics
      port: 9400
      targetPort: 9400
      protocol: TCP
    type: ClusterIP

Apply it with:

.. code-block:: bash

  kubectl apply -f dcgm-service.yaml

Step 2 - Deploy the SkyPilot API server with GPU metrics enabled
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

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


TODO: Remove this step. Step 3 - Access the Grafana dashboard
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Add the following to your global SkyPilot config (see :ref:`sky-api-server-config`) and restart the API server pod:

.. code-block:: yaml

  dashboard:
    grafana_url: http://<API_SERVER_IP>/grafana


What metrics are collected?
---------------------------

* GPU Utilization
* GPU Memory Usage
* GPU Power Usage

More detailed metrics, such as GPU errors, NVLink stats and more are available directly on the grafana dashboard. TODO: Add instructions to access the grafana dashboard.
