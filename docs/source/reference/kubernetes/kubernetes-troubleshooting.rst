.. _kubernetes-troubleshooting:

Kubernetes Troubleshooting
==========================

If you're unable to run SkyPilot tasks on your Kubernetes cluster, this guide will help you debug common issues.

If this guide does not help resolve your issue, please reach out to us on `Slack <https://slack.skypilot.co>`_ or `GitHub <http://www.github.com/skypilot-org/skypilot>`_.

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

We will use the SkyPilot default image :code:`us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest` to verify that the image can be pulled from the registry.

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
    $ sky show-gpus --infra k8s

Step B4 - Try launching a dummy GPU task
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Next, try running a simple GPU task to verify that SkyPilot can launch GPU tasks on your cluster.

.. code-block:: bash

    # Replace the GPU type from the sky show-gpus output in the task launch command
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
