.. _skyserve-high-availability-controller:

=========================================
High Availability SkyServe Controller
=========================================

Overview
--------
By default, the SkyServe controller runs as a single instance (either a VM or a Kubernetes Pod). If this instance fails due to node issues, pod crashes, or other unexpected events, the service endpoint becomes unavailable until the controller is manually recovered or relaunched.

To enhance resilience and ensure service continuity, SkyServe offers a High Availability mode for its controller. When enabled, the controller leverages the automatic recovery feature of Kubernetes Deployments to automatically recover from failures.

Benefits of high availability controller:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Automatic Recovery:** The controller and load balancer can automatically restart after crashes or node failures.
* **Service Continuity:** Minimizes downtime for your served applications.
* **Managed by Kubernetes:** Relies on robust, industry-standard Kubernetes features for state management and process supervision.

Prerequisites
-------------
* **Kubernetes Cluster:** High Availability mode is **currently only supported when using Kubernetes** as the cloud provider for the SkyServe controller. You must have a Kubernetes cluster configured for SkyPilot. See :ref:`Kubernetes Setup <kubernetes-setup>` for details.
* **Persistent Kubernetes:** The underlying Kubernetes cluster (control plane and nodes) must be running persistently. If using a local Kubernetes deployment (e.g., Minikube, Kind via ``sky local up``), the machine hosting the cluster must remain online.

.. note::
    Currently, High Availability mode is only supported for Kubernetes. Support for other clouds (e.g., AWS, GCP, Azure VMs) is under development.

How to enable high availability mode
-------------------------------------
To enable High Availability for the SkyServe controller, set the ``high_availability`` flag to ``true`` within the ``serve.controller`` section of your :ref:`SkyPilot configuration <config-yaml>`:

.. code-block:: yaml
    :emphasize-lines: 8,11

    serve:
      # NOTE: these settings only take effect for a *new* SkyServe controller,
      # not if you have an existing one (even if stopped).
      controller:
        # --- Controller Resources ---
        # Optional: Specify resources like cloud, region, cpus, disk_size
        resources:
          cloud: kubernetes  # High Availability mode requires Kubernetes
          # region: <your-k8s-region> # Optional
          cpus: 2+           # Optional, example value
          # disk_size: 100     # Optional, example value (affects PVC size in High Availability mode)

        # --- Enable High Availability ---
        high_availability: true

.. note::
    Enabling or disabling ``high_availability`` only affects **new** SkyServe controllers. If you have an existing controller (either running or stopped), changing this setting will not modify it. To apply the change, you must first terminate all services and then tear down the existing controller using ``sky down <controller_name>``. See `Important considerations`_ below.

.. note::
    While the controller requires Kubernetes for High Availability mode, service replicas can still use any cloud or infrastructure supported by SkyPilot.

How it works
------------
When ``high_availability: true`` is set, SkyPilot modifies how the SkyServe controller is deployed on Kubernetes:

1.  **Kubernetes Deployment:** Instead of launching a single Kubernetes Pod, the controller is launched as a Kubernetes `Deployment <https://kubernetes.io/docs/concepts/workloads/controllers/deployment/>`_ with ``replicas: 1``. The Deployment ensures that one instance of the controller pod is always running. If the pod crashes or the node it's on fails, Kubernetes automatically reschedules and starts a new pod.
2.  **Persistent Volume Claim (PVC):** Controller state, including the service database (SQLite), logs, and potentially other runtime information, needs to persist across pod restarts. SkyPilot automatically creates a `PersistentVolumeClaim (PVC) <https://kubernetes.io/docs/concepts/storage/persistent-volumes/#persistentvolumeclaims>`_ for the controller. This PVC is mounted into the controller pod (typically at ``/home/sky``, which includes ``~/.sky/``). The size of the PVC is determined by the ``disk_size`` specified in the controller's ``resources`` configuration (default is 100GB if not specified).
3.  **Restart Policy:** The controller pod within the Deployment is configured with ``restartPolicy: Always``.

Configuration details
---------------------
Besides the main ``serve.controller.high_availability: true`` flag, you can customize High Availability behavior further:

.. raw:: html

   <ul>
   <li><strong>Controller Resources (<code>serve.controller.resources</code>):</strong> As usual, you can specify <code>cloud</code> (must be Kubernetes), <code>region</code>, <code>cpus</code>, etc. The <code>disk_size</code> here directly determines the size of the PersistentVolumeClaim created for the High Availability controller.</li>
   <li><strong>Kubernetes Storage Class (<code>kubernetes.high_availability.storage_class_name</code> - Optional):</strong> If your Kubernetes cluster has specific storage classes defined (e.g., for different performance tiers like SSD vs HDD, or specific features like backup), you can specify which one to use for the controller's PVC. This is configured under the <code>kubernetes</code> section in <code>config.yaml</code>:</li>
   </ul>

.. code-block:: yaml

    kubernetes:
      # ... other kubernetes settings ...
      high_availability:
        # Optional: Specify the StorageClass name for the controller's PVC
        storage_class_name: <your-storage-class-name> # e.g., premium-ssd

**Purpose:** Different storage classes offer varying performance (IOPS, throughput), features (snapshots, backups), and costs. If your cluster provides multiple options and you have specific requirements for the controller's storage (e.g., needing faster disk I/O or a particular backup strategy), you can specify a storage class. If omitted, the default storage class configured in your Kubernetes cluster will be used.

Important considerations
------------------------
* **Currently Kubernetes Only:** This feature relies entirely on Kubernetes mechanisms (Deployments, PVCs) and is only available when the controller's specified ``cloud`` is ``kubernetes``. Support for other clouds (AWS, GCP, Azure VMs) is under development.
* **Persistent K8s Required:** The High Availability mechanism depends on the Kubernetes cluster itself being available. Ensure your K8s control plane and nodes are stable.
* **No Effect on Existing Controllers:** Setting ``high_availability: true`` in ``config.yaml`` will **not** convert an existing non-High Availability controller (running or stopped) to High Availability mode, nor will setting it to ``false`` convert an existing High Availability controller to non-High Availability. You must tear down the existing controller first (``sky down <sky-serve-controller-name>`` after terminating all services) for the new setting to apply when the controller is next launched.
* **Inconsistent State Error:** If you attempt to launch a service (``sky serve up``) and the ``high_availability`` setting in your ``config.yaml`` *conflicts* with the actual state of the existing SkyServe controller cluster on Kubernetes (e.g., you enabled High Availability in config, but the controller exists as a non-High Availability Pod, or vice-versa), SkyPilot will raise an ``InconsistentHighAvailabilityError``. To resolve this, terminate all services, tear down the controller (``sky down <sky-serve-controller-name>``), and then run ``sky serve up`` again with the desired consistent configuration.

Recovery example
----------------
This example demonstrates the automatic recovery capability of the High Availability controller:

**0. Preparatory Steps (Ensure Clean State & Correct Config):**

* **Terminate Existing Controller (if any):**
    * First, ensure **no services are running**. Terminate them with ``sky serve down <service_name>`` or ``sky serve down --all``.
    * Find the controller name:

      .. code-block:: bash

          sky status | grep sky-serve-controller

    * Terminate and purge the controller (replace ``<sky-serve-controller-name>`` with the name you found above):

      .. code-block:: bash

          sky down <sky-serve-controller-name>

* **Set Configuration:** First, ensure your ``~/.sky/config.yaml`` enables High Availability mode as shown in the `How to enable High Availability mode`_ section.

  .. code-block:: yaml
      :caption: ~/.sky/config.yaml (relevant part)

      serve:
        controller:
          resources:
            cloud: kubernetes
          high_availability: true

* **Prepare Service Definition:**

1.  **Prepare Configuration Files:**

* **Service Definition (e.g., ``http_service.yaml``):** Use a simple HTTP service.

  .. code-block:: yaml
    :caption: http_service.yaml

    service:
      readiness_probe: / # Default path for http.server
      replicas: 1

    resources:
      ports: 8080
      cpus: 1 # Minimal resources

    run: python3 -m http.server 8080 --bind 0.0.0.0

  You can also use the ``http_server.yaml`` from the `examples/serve/http_server/task.yaml <https://github.com/skypilot-ai/skypilot/blob/main/examples/serve/http_server/task.yaml>`_ file.

2.  **Launch the Service:**

    .. code-block:: bash

        sky serve up -n my-http-service http_service.yaml
        # This will launch the new High Availability controller based on your config.

3.  **Wait and Verify the Service:** Wait until the service status becomes ``READY``.

    .. code-block:: bash

        watch sky serve status my-http-service
        # Wait for STATUS to become READY

        # Get the endpoint URL
        ENDPOINT=$(sky serve status my-http-service --endpoint)
        echo "Service endpoint: $ENDPOINT"

        # Verify the service is rnvesponding correctly
        curl $ENDPOINT
        # Should see the default HTML output from http.server

4.  **Simulate Controller Failure (Manually Delete Pod):**
    
    * Find the name of the controller pod. Controller pods typically contain "sky-serve-controller" and have the label ``skypilot-head-node=1``.

    .. code-block:: bash

        kubectl get pods -l skypilot-head-node=1 | grep sky-serve-controller
        # Copy the controller pod name (e.g., sky-serve-controller-deployment-xxxxx-yyyyy)

        CONTROLLER_POD=<paste_controller_pod_name_here>

    * Delete the controller pod.

      .. code-block:: bash

          echo "Deleting controller pod: $CONTROLLER_POD"
          kubectl delete pod $CONTROLLER_POD

5.  **Observe Recovery:** The Kubernetes Deployment will detect the missing pod and automatically create a new one to replace it.

    .. code-block:: bash

        echo "Waiting for controller pod to recover..."
        # Wait a few seconds for Kubernetes to react
        sleep 15

        # Check that a new pod has started and is running (Status should be Running 1/1)
        kubectl get pods -l skypilot-head-node=1
        # Note the pod name will be different, and STATUS should be Running

6.  **Verify Service Again:** Even though the controller pod was restarted, the service endpoint should remains the same and still be accessible (there might be a brief interruption depending on load balancer and K8s response times).

    .. code-block:: bash

        echo "Re-checking service endpoint: $ENDPOINT"
        curl $ENDPOINT
        # Should still see the http.server output, indicating the service has recovered

This example shows that even if the controller pod terminates unexpectedly, the Kubernetes Deployment mechanism automatically restores it, and thanks to the persisted state (via PVC) and recovery logic, the service continues to operate.