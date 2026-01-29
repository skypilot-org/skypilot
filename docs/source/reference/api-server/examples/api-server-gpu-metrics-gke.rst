.. _api-server-gpu-metrics-setup-gke:

Setting up GPU Metrics on GKE
=============================

This guide covers setting up GPU metrics for SkyPilot on Google Kubernetes Engine (GKE).

There are two approaches to enable GPU metrics on GKE:

1. **GKE-Managed DCGM Metrics** (Recommended): For GKE clusters running version 1.30.1-gke.1204000 or later, Google provides managed DCGM metrics collection.

2. **Manual DCGM Exporter Installation**: For older GKE versions or when you need more control, you can manually install the NVIDIA DCGM exporter.


Option 1: GKE-Managed DCGM Metrics (Recommended)
------------------------------------------------

This is the recommended approach for GKE clusters running version 1.30.1 or later.

Prerequisites
~~~~~~~~~~~~~

* GKE cluster version 1.30.1-gke.1204000 or later
* Node pools with NVIDIA GPUs
* Node pools using GKE managed GPU drivers (``--gpu-driver-version=default`` or ``--gpu-driver-version=latest``)

Step 1: Create GPU Node Pools with Managed Drivers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When creating GPU node pools, ensure you're using GKE managed GPU drivers:

.. code-block:: bash

   gcloud container node-pools create gpu-pool \
     --cluster $CLUSTER_NAME \
     --location $REGION \
     --machine-type n1-standard-4 \
     --accelerator type=nvidia-tesla-t4,count=1,gpu-driver-version=default \
     --num-nodes 1

The key is ``gpu-driver-version=default`` (or ``latest``), which enables GKE to manage GPU drivers and DCGM.

Step 2: Enable DCGM Metrics Collection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Enable DCGM metrics on your GKE cluster:

.. code-block:: bash

   gcloud container clusters update $CLUSTER_NAME \
     --location $REGION \
     --monitoring=SYSTEM,DCGM

This command enables GKE to:

* Install the DCGM-Exporter tool on GPU nodes
* Deploy a ClusterPodMonitoring resource for metrics collection
* Send metrics to Google Cloud Managed Service for Prometheus

.. note::

   For GKE clusters created with version 1.32.1-gke.1357000 or later, DCGM metrics are enabled by default.


Step 3: Verify DCGM Exporter is Running
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Check that the DCGM exporter pods are running:

.. code-block:: bash

   kubectl get pods -n kube-system | grep dcgm

You should see DCGM exporter pods running on each GPU node.

Step 4: Configure Prometheus Scrape Annotations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The GKE-managed DCGM exporter may not have Prometheus scrape annotations by default.
Check if the DCGM exporter pods have the required annotations:

.. code-block:: bash

   kubectl get pods -n kube-system -l app.kubernetes.io/name=dcgm-exporter \
     -o jsonpath='{.items[0].metadata.annotations}'

If the annotations are missing, create a Service with Prometheus annotations to expose the DCGM metrics:

.. code-block:: bash

   kubectl apply -f - <<EOF
   apiVersion: v1
   kind: Service
   metadata:
     name: dcgm-exporter
     namespace: kube-system
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
   EOF


Option 2: Manual DCGM Exporter Installation
-------------------------------------------

For older GKE versions or when you need more control over the GPU stack, you can manually install the NVIDIA GPU Operator or DCGM exporter.

Using the NVIDIA GPU Operator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The GPU Operator manages all NVIDIA software components needed to provision GPU nodes.

1. Create GPU node pools **without** managed drivers:

   .. code-block:: bash

      gcloud container node-pools create gpu-pool \
        --cluster $CLUSTER_NAME \
        --location $REGION \
        --machine-type n1-standard-4 \
        --accelerator type=nvidia-tesla-t4,count=1,gpu-driver-version=disabled \
        --num-nodes 1

   Note: ``gpu-driver-version=disabled`` is required when using the GPU Operator.

2. Install the GPU Operator using Helm:

   .. code-block:: bash

      helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
      helm repo update

      helm install gpu-operator nvidia/gpu-operator \
        --namespace gpu-operator \
        --create-namespace \
        --set dcgmExporter.enabled=true

3. Wait for the GPU Operator pods to be ready:

   .. code-block:: bash

      kubectl get pods -n gpu-operator

Using DCGM Exporter Directly
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you already have GPU drivers installed and just need the DCGM exporter:

1. Install the DCGM exporter using Helm:

   .. code-block:: bash

      helm repo add gpu-helm-charts https://nvidia.github.io/dcgm-exporter/helm-charts
      helm repo update

      helm install dcgm-exporter gpu-helm-charts/dcgm-exporter \
        --namespace kube-system \
        --set serviceMonitor.enabled=false

2. Create a Service with Prometheus annotations:

   .. code-block:: bash

      kubectl create -f https://raw.githubusercontent.com/skypilot-org/skypilot/refs/heads/master/examples/metrics/dcgm_service.yaml -n kube-system


Installing Prometheus for SkyPilot
----------------------------------

Regardless of which DCGM approach you chose, you need to install Prometheus for SkyPilot to scrape GPU metrics.

SkyPilot requires a Service named ``skypilot-prometheus-server`` in the ``skypilot`` namespace.

1. Create a ``prometheus-values.yaml`` file:

   .. literalinclude:: ../../../../../examples/metrics/prometheus-values.yaml
      :language: yaml

2. Install Prometheus:

   .. code-block:: bash

      helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
      helm repo update

      helm upgrade --install skypilot-prometheus prometheus-community/prometheus \
        --namespace skypilot \
        --create-namespace \
        -f prometheus-values.yaml

3. Verify the Prometheus service was created:

   .. code-block:: bash

      kubectl get svc skypilot-prometheus-server -n skypilot

.. note::

   Do not use the Prometheus Operator (kube-prometheus-stack) for GPU metrics.
   The Prometheus Operator adds an ``exported_`` prefix to pod and namespace labels,
   which breaks the PromQL queries used by SkyPilot.


Enabling GPU Metrics in SkyPilot
--------------------------------

Once DCGM exporter and Prometheus are running, enable GPU metrics in the SkyPilot API server:

.. code-block:: bash

   helm upgrade --install skypilot skypilot/skypilot-nightly --devel \
     --namespace skypilot \
     --reuse-values \
     --set apiService.metrics.enabled=true \
     --set prometheus.enabled=true \
     --set grafana.enabled=true

Refer to :ref:`api-server-setup-dcgm-metrics-scraping` for more details on enabling metrics in SkyPilot.


Verifying the Setup
-------------------

1. Check that DCGM exporter is running and exposing metrics:

   .. code-block:: bash

      # Port-forward to the DCGM exporter service
      kubectl port-forward svc/dcgm-exporter -n kube-system 9400:9400 &

      # Query metrics
      curl http://localhost:9400/metrics | grep DCGM_FI_DEV_GPU_UTIL

   You should see GPU utilization metrics.

2. Verify Prometheus is scraping the metrics:

   .. code-block:: bash

      # Port-forward to Prometheus
      kubectl port-forward svc/skypilot-prometheus-server -n skypilot 9090:80 &

      # Query Prometheus for GPU metrics
      curl 'http://localhost:9090/api/v1/query?query=DCGM_FI_DEV_GPU_UTIL'

3. Open the SkyPilot dashboard and navigate to the GPU metrics page to verify metrics are displayed.


Troubleshooting
---------------

No DCGM Metrics in Prometheus
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If Prometheus is not scraping DCGM metrics:

1. Check that the DCGM exporter pods are running:

   .. code-block:: bash

      kubectl get pods -A | grep dcgm

2. Verify the Service has the correct Prometheus annotations:

   .. code-block:: bash

      kubectl get svc dcgm-exporter -n kube-system -o yaml | grep -A5 annotations

3. Check Prometheus targets to see if DCGM exporter is being discovered:

   .. code-block:: bash

      kubectl port-forward svc/skypilot-prometheus-server -n skypilot 9090:80 &
      # Open http://localhost:9090/targets in your browser

DCGM Exporter Pods Not Starting
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If DCGM exporter pods fail to start:

1. Check the pod logs:

   .. code-block:: bash

      kubectl logs -n kube-system -l app.kubernetes.io/name=dcgm-exporter

2. Verify GPU drivers are installed on the nodes:

   .. code-block:: bash

      kubectl get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable."nvidia\.com/gpu"

3. For GKE-managed drivers, ensure your node pool was created with ``gpu-driver-version=default`` or ``gpu-driver-version=latest``.


PriorityClass Quota Issues with GPU Operator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you see errors about insufficient quota for PriorityClass when using the GPU Operator:

.. code-block:: text

   Error creating: insufficient quota to match these scopes: [{PriorityClass In [system-node-critical system-cluster-critical]}]

This is because GKE limits consumption of these priority classes by default. Solutions:

1. Install DCGM exporter in the ``kube-system`` namespace:

   .. code-block:: bash

      helm install dcgm-exporter gpu-helm-charts/dcgm-exporter -n kube-system

2. Or, request a quota increase for ``ResourceQuota`` in your GKE project.
