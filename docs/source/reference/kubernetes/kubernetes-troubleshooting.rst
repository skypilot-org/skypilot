.. _kubernetes-troubleshooting:

Kubernetes Troubleshooting
==========================

If you're unable to run SkyPilot tasks on your Kubernetes cluster, this guide will help you debug common issues.

If this guide does not help resolve your issue, please reach out to us on `Slack <https://slack.skypilot.co>`_ or `GitHub <https://github.com/skypilot-org/skypilot>`_.

.. _kubernetes-cluster-states:

Understanding cluster states
----------------------------

SkyPilot clusters can be in one of the following states:

- **INIT**: The cluster is initializing, has incomplete setup, or is in an abnormal state (e.g., some pods are not running).
- **UP**: The cluster is healthy and all pods are running correctly.
- **STOPPED**: All pods in the cluster have been stopped (not applicable for Kubernetes, as pods cannot be "stopped" - they are either running or terminated).

.. note::
    On Kubernetes, clusters cannot be "stopped" like VMs on cloud providers. When a cluster is terminated on Kubernetes, it is removed entirely. This means the **STOPPED** state is generally not used for Kubernetes clusters.

.. _kubernetes-pod-failure-states:

Cluster state transitions when pods fail
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

When pods experience failures on Kubernetes, SkyPilot detects these failures and transitions the cluster to the appropriate state. Below are the common pod failure scenarios and their expected cluster state behavior.

**Pod OOM Killed**

When a pod is terminated due to Out-of-Memory (OOM) conditions:

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - **Cause**
     - Container exceeds its memory limit or the node runs out of memory
   * - **Cluster State**
     - Transitions to **INIT**
   * - **Pod Status**
     - ``OOMKilled`` in container termination reason
   * - **Recovery**
     - Run ``sky down <cluster>`` to clean up, then ``sky launch`` to recreate. If OOM persists, increase memory in your task YAML.

**Pod Evicted by Kubelet**

When kubelet evicts a pod due to node resource pressure:

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - **Cause**
     - Node is low on resources (CPU, memory, ephemeral storage, or disk pressure)
   * - **Cluster State**
     - Transitions to **INIT**
   * - **Pod Status**
     - ``Evicted`` with a message indicating the resource pressure (e.g., "The node was low on resource: ephemeral-storage")
   * - **Recovery**
     - Run ``sky down <cluster>`` to clean up, then ``sky launch`` to recreate. Consider requesting more storage or using nodes with more resources.

**Node Drained or Deleted**

When a node is drained (e.g., for maintenance) or deleted from the cluster:

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - **Cause**
     - Cluster administrator drains the node, node is deleted, or node becomes unhealthy
   * - **Cluster State**
     - Transitions to **INIT** (if some pods are affected) or cluster is removed from ``sky status`` (if all pods are terminated)
   * - **Pod Status**
     - Pod events show ``DeletingNode``, ``NodeNotReady``, or ``TaintManagerEviction``
   * - **Recovery**
     - If the cluster shows **INIT**, run ``sky down <cluster>`` and then ``sky launch`` to recreate on a healthy node.

**Pod Preempted by Kueue**

When using Kueue for workload scheduling and a pod is preempted:

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - **Cause**
     - Higher priority workload needs resources, or quota is exceeded
   * - **Cluster State**
     - Transitions to **INIT**
   * - **Pod Status**
     - Pod condition ``TerminationTarget`` with reason indicating Kueue preemption
   * - **Recovery**
     - Run ``sky down <cluster>`` and then ``sky launch``. Consider using :ref:`managed jobs <managed-jobs>` for automatic recovery from preemptions.

**Generic Pod Disruption**

When a pod is disrupted for other reasons (e.g., PodDisruptionBudget, voluntary disruption):

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - **Cause**
     - Various disruption events (voluntary or involuntary)
   * - **Cluster State**
     - Transitions to **INIT**
   * - **Pod Status**
     - Pod condition ``DisruptionTarget`` with specific reason and message
   * - **Recovery**
     - Run ``sky down <cluster>`` and then ``sky launch`` to recreate.

.. _kubernetes-state-transition-summary:

State transition summary
^^^^^^^^^^^^^^^^^^^^^^^^

The table below summarizes how SkyPilot maps Kubernetes pod phases to cluster states:

.. list-table::
   :widths: 30 30 40
   :header-rows: 1

   * - Pod Phase
     - Cluster State
     - Notes
   * - ``Pending``
     - **INIT**
     - Pod is waiting to be scheduled or initialized
   * - ``Running``
     - **UP**
     - Pod is running and healthy
   * - ``Failed``
     - **INIT**
     - Pod has failed (OOM, eviction, etc.)
   * - ``Succeeded``
     - Terminated
     - Pod completed successfully (cluster removed from status)
   * - ``Unknown``
     - Terminated
     - Pod state is unknown (cluster removed from status)

.. tip::
    Run ``sky status --refresh`` to force a status refresh and see the latest cluster state. This queries the Kubernetes API for the current pod status.

.. _kubernetes-troubleshooting-init-state:

Why is my cluster stuck in INIT state?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A cluster in **INIT** state indicates that something went wrong during provisioning or that the cluster is in an abnormal state. Common causes include:

1. **Pod failed to start**: Check pod events with ``kubectl describe pod <pod-name>``
2. **Pod was evicted or OOM killed**: Check pod status and events for termination reasons
3. **Node issues**: The node may be unhealthy or have resource pressure
4. **Image pull failures**: The container image could not be pulled
5. **Resource constraints**: Requested resources (GPU, CPU, memory) are not available

**Debugging steps:**

.. code-block:: bash

    # Find the pod name for your cluster
    $ kubectl get pods -l skypilot-cluster-name=<cluster-name>

    # Get detailed pod information including events
    $ kubectl describe pod <pod-name>

    # Check pod logs if the container started
    $ kubectl logs <pod-name>

    # Check node status and events
    $ kubectl describe node <node-name>

.. _kubernetes-recovery-guidelines:

Recovery guidelines
^^^^^^^^^^^^^^^^^^^

For most pod failure scenarios, the recommended recovery approach is:

1. **Clean up the failed cluster**:

   .. code-block:: bash

       $ sky down <cluster-name>

2. **Investigate the root cause** (if the failure is unexpected):

   .. code-block:: bash

       # Before running sky down, check pod events
       $ kubectl get pods -l skypilot-cluster-name=<cluster-name>
       $ kubectl describe pod <pod-name>

3. **Relaunch with appropriate resources**:

   .. code-block:: bash

       $ sky launch -c <cluster-name> <task.yaml>

   If the failure was due to resource constraints, update your task YAML to request appropriate resources:

   .. code-block:: yaml

       resources:
         memory: 32+  # Request at least 32GB memory
         disk_size: 100  # Request 100GB disk

4. **Consider using managed jobs** for fault tolerance:

   For long-running jobs that may be affected by preemptions or node failures, use SkyPilot's :ref:`managed jobs <managed-jobs>` feature which provides automatic recovery:

   .. code-block:: bash

       $ sky jobs launch <task.yaml>

.. _kubernetes-troubleshooting-basic:

Verifying basic setup
---------------------

Step A0 - Is Kubectl functional?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Are you able to run :code:`kubectl get nodes` without any errors?

.. code-block:: bash

    $ kubectl get nodes
    # This should list all the nodes in your cluster.

Make sure at least one node is in the :code:`Ready` state.

If you see an error, ensure that your kubeconfig file at :code:`~/.kube/config` is correctly set up.

.. note::
    The :code:`kubectl` command should not require any additional flags or environment variables to run.
    If it requires additional flags, you must encode all configuration in your kubeconfig file at :code:`~/.kube/config`.
    For example, :code:`--context`, :code:`--token`, :code:`--certificate-authority`, etc. should all be configured directly in the kubeconfig file.

Step A1 - Can you create pods and services?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

As a sanity check, we will now try creating a simple pod running a HTTP server and a service to verify that your cluster and it's networking is functional.

We will use the SkyPilot default image :code:`us-docker.pkg.dev/sky-dev-465/skypilotk8s/skypilot:latest` to verify that the image can be pulled from the registry.

.. code-block:: bash

    $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/cpu_test_pod.yaml

    # Verify that the pod is running by checking the status of the pod
    $ kubectl get pod skytest

    # Try accessing the HTTP server in the pod by port-forwarding it to your local machine
    $ kubectl port-forward svc/skytest-svc 8080:8080

    # Open a browser and navigate to http://localhost:8080 to see an index page

    # Once you have verified that the pod is running, you can delete it
    $ kubectl delete -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/cpu_test_pod.yaml

If your pod does not start, check the pod's logs for errors with :code:`kubectl describe skytest` and :code:`kubectl logs skytest`.

Step A2 - Can SkyPilot access your cluster?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Run :code:`sky check` to verify that SkyPilot can access your cluster.

.. code-block:: bash

    $ sky check
    # Should show `Kubernetes: Enabled`

If you see an error, ensure that your kubeconfig file at :code:`~/.kube/config` is correctly set up.
Also, make sure that the cluster context name doesn't start with the prefix :code:`ssh-`. This prefix is reserved for SkyPilot internal use.


Step A3 - Do your nodes have enough disk space?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If your nodes are out of disk space, pulling the SkyPilot images may fail with :code:`rpc error: code = Canceled desc = failed to pull and unpack image: context canceled` error in the terminal during provisioning.
Make sure your nodes are not under disk pressure by checking :code:`Conditions` in :code:`kubectl describe nodes`, or by running:

.. code-block:: bash

    $ kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{range .status.conditions[?(@.type=="DiskPressure")]}{.type}={.status}{"\n"}{end}{"\n"}{end}'
    # Should not show DiskPressure=True for any node


Step A4 - Can you launch a SkyPilot task?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Next, try running a simple hello world task to verify that SkyPilot can launch tasks on your cluster.

.. code-block:: bash

    $ sky launch -y -c mycluster --infra k8s -- "echo hello world"
    # Task should run and print "hello world" to the console

    # Once you have verified that the task runs, you can delete it
    $ sky down -y mycluster

If your task does not run, check the terminal and provisioning logs for errors. Path to provisioning logs can be found at the start of the SkyPilot output,
starting with "To view detailed progress: ...".

.. _kubernetes-troubleshooting-gpus:

Checking GPU support
--------------------

If you are trying to run a GPU task, make sure you have followed the instructions in :ref:`kubernetes-setup-gpusupport` to set up your cluster for GPU support.

In this section, we will verify that your cluster has GPU support and that SkyPilot can access it.

Step B0 - Is your cluster GPU-enabled?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Run :code:`kubectl describe nodes` or the below snippet to verify that your nodes have :code:`nvidia.com/gpu` resources.

.. code-block:: bash

    $ kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, capacity: .status.capacity}'
    # Look for the `nvidia.com/gpu` field under resources in the output. It should show the number of GPUs available for each node.

If you do not see the :code:`nvidia.com/gpu` field, your cluster likely does not have the Nvidia GPU operator installed.
Please follow the instructions in :ref:`kubernetes-setup-gpusupport` to install the Nvidia GPU operator.
Note that GPU operator installation can take several minutes, and you may see 0 capacity for ``nvidia.com/gpu`` resources until the installation is complete.

.. tip::

    If you are using GKE, refer to :ref:`kubernetes-setup-gke` to install the appropriate drivers.

Step B1 - Can you run a GPU pod?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Verify if GPU operator is installed and the ``nvidia`` runtime is set as default by running:

.. code-block:: bash

    $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/gpu_test_pod.yaml

    # Verify that the pod is running by checking the status of the pod
    $ kubectl get pod skygputest

    $ kubectl logs skygputest
    # Should print the nvidia-smi output to the console

    # Once you have verified that the pod is running, you can delete it
    $ kubectl delete -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/gpu_test_pod.yaml

If the pod status is pending, make the :code:`nvidia.com/gpu` resources available on your nodes in the previous step. You can debug further by running :code:`kubectl describe pod skygputest`.

If the logs show `nvidia-smi: command not found`, likely the ``nvidia`` runtime is not set as default. Please install the Nvidia GPU operator and make sure the ``nvidia`` runtime is set as default.
For example, for RKE2, refer to instructions on `Nvidia GPU Operator installation with Helm on RKE2 <https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#custom-configuration-for-runtime-containerd>`_ to set the ``nvidia`` runtime as default.


Step B2 - Are your nodes labeled correctly?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

SkyPilot requires nodes to be labeled with the correct GPU type to run GPU tasks. Run :code:`kubectl get nodes -o json` to verify that your nodes are labeled correctly.

.. tip::

    If you are using GKE, your nodes should be automatically labeled with :code:`cloud.google.com/gke-accelerator`. You can skip this step.

.. code-block:: bash

    $ kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, labels: .metadata.labels}'
    # Look for the `skypilot.co/accelerator` label in the output. It should show the GPU type for each node.

If you do not see the `skypilot.co/accelerator` label, your nodes are not labeled correctly. Please follow the instructions in :ref:`kubernetes-setup-gpusupport` to label your nodes.

Step B3 - Can SkyPilot see your GPUs?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Run :code:`sky check` to verify that SkyPilot can see your GPUs.

.. code-block:: bash

    $ sky check
    # Should show `Kubernetes: Enabled` and should not print any warnings about GPU support.

    # List the available GPUs in your cluster
    $ sky gpus list --infra k8s

Step B4 - Try launching a dummy GPU task
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Next, try running a simple GPU task to verify that SkyPilot can launch GPU tasks on your cluster.

.. code-block:: bash

    # Replace the GPU type from the sky gpus list output in the task launch command
    $ sky launch -y -c mygpucluster --infra k8s --gpu <gpu-type>:1 -- "nvidia-smi"

    # Task should run and print the nvidia-smi output to the console

    # Once you have verified that the task runs, you can delete it
    $ sky down -y mygpucluster

If your task does not run, check the terminal and provisioning logs for errors. Path to provisioning logs can be found at the start of the SkyPilot output,
starting with "To view detailed progress: ...".

.. _kubernetes-troubleshooting-ports:

Verifying ports support
-----------------------

If you are trying to run a task that requires ports to be opened, make sure you have followed the instructions in :ref:_kubernetes-ports`
to configure SkyPilot and your cluster to use the desired method (LoadBalancer service or Nginx Ingress) for port support.

In this section, we will first verify that your cluster has ports support and services launched by SkyPilot can be accessed.

.. _kubernetes-troubleshooting-ports-loadbalancer:

Step C0 - Verifying LoadBalancer service setup
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you are using LoadBalancer services for ports support, follow the below steps to verify that your cluster is configured correctly.

.. tip::

    If you are using Nginx Ingress for ports support, skip to :ref:`kubernetes-troubleshooting-ports-nginx`.

Does your cluster support LoadBalancer services?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To verify that your cluster supports LoadBalancer services, we will create an example service and verify that it gets an external IP.

.. code-block:: bash

    $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/cpu_test_pod.yaml
    $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/loadbalancer_test_svc.yaml

    # Verify that the service gets an external IP
    # Note: It may take some time on cloud providers to change from pending to an external IP
    $ watch kubectl get svc skytest-loadbalancer

    # Once you get an IP, try accessing the HTTP server by curling the external IP
    $ IP=$(kubectl get svc skytest-loadbalancer -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
    $ curl $IP:8080

    # Once you have verified that the service is accessible, you can delete it
    $ kubectl delete -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/cpu_test_pod.yaml
    $ kubectl delete -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/loadbalancer_test_svc.yaml

If your service does not get an external IP, check the service's status with :code:`kubectl describe svc skytest-loadbalancer`. Your cluster may not support LoadBalancer services.


.. _kubernetes-troubleshooting-ports-nginx:

Step C0 - Verifying Nginx Ingress setup
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you are using Nginx Ingress for ports support, refer to :ref:`kubernetes-ingress` for instructions on how to install and configure Nginx Ingress.

.. tip::

    If you are using LoadBalancer services for ports support, you can skip this section.

Does your cluster support Nginx Ingress?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To verify that your cluster supports Nginx Ingress, we will create an example ingress.

.. code-block:: bash

        $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/cpu_test_pod.yaml
        $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/ingress_test.yaml

        # Get the external IP of the ingress using the externalIPs field or the loadBalancer field
        $ IP=$(kubectl get service ingress-nginx-controller -n ingress-nginx -o jsonpath='{.spec.externalIPs[*]}') && [ -z "$IP" ] && IP=$(kubectl get service ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[*].ip}')
        $ echo "Got IP: $IP"
        $ curl http://$IP/skytest

        # Once you have verified that the service is accessible, you can delete it
        $ kubectl delete -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/cpu_test_pod.yaml
        $ kubectl delete -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/ingress_test_svc.yaml

If your IP is not acquired, check the service's status with :code:`kubectl describe svc ingress-nginx-controller -n ingress-nginx`.
Your ingress's service must be of type :code:`LoadBalancer` or :code:`NodePort` and must have an external IP.

Is SkyPilot configured to use Nginx Ingress?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Take a look at your :code:`~/.sky/config.yaml` file to verify that the :code:`ports: ingress` section is configured correctly.

.. code-block:: bash

    $ cat ~/.sky/config.yaml

    # Output should contain:
    #
    # kubernetes:
    #   ports: ingress

If not, add the :code:`ports: ingress` section to your :code:`~/.sky/config.yaml` file.

.. _kubernetes-troubleshooting-ports-dryrun:

Step C1 - Verifying SkyPilot can launch services
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Next, try running a simple task with a service to verify that SkyPilot can launch services on your cluster.

.. code-block:: bash

    $ sky launch -y -c myserver --infra k8s --ports 8080 -- "python -m http.server 8080"

    # Obtain the endpoint of the service
    $ sky status --endpoint 8080 myserver

    # Try curling the endpoint to verify that the service is accessible
    $ curl <endpoint>

If you are unable to get the endpoint from SkyPilot,
consider running :code:`kubectl describe services` or :code:`kubectl describe ingress` to debug it.
