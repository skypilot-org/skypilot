.. _kubernetes-debugging:

Debugging Kubernetes Issues
===========================

If you're unable to run SkyPilot tasks on your Kubernetes cluster, this guide will help you debug common issues.

If this guide does not help resolve your issue, please reach out to us on `Slack <https://slack.skypilot.co>`_ or `GitHub <http://www.github.com/skypilot-org/skypilot>`_.

Verifying basic setup
---------------------

Step A0 - Is Kubectl functional?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Are you able to run :code:`kubectl get nodes` without any errors?

.. code-block:: console

    $ kubectl get nodes
    # This should list all the nodes in your cluster.

Make sure at least one node is in the :code:`Ready` state.

If you see an error, ensure that your kubeconfig file at :code:`~/.kube/config` is correctly set up.

.. note::
    The :code:`kubectl` command should not require any additional flags or environment variables to run.
    If it requires additional flags, you must encode all configuration in your kubeconfig file at :code:`~/.kube/config`.
    For example, :code:`--context`, :code:`--token`, :code:`--certificate-authority`, etc. should all be configured directly in the kubeconfig file.

Step A1 - Can you create pods and services?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

As a sanity check, we will now try creating a simple pod running a HTTP server and a service to verify that your cluster and it's networking is functional.

We will use the SkyPilot default image :code:`us-central1-docker.pkg.dev/skypilot-375900/skypilotk8s/skypilot:latest` to verify that the image can be pulled from the registry.

.. code-block:: console

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
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Run :code:`sky check` to verify that SkyPilot can access your cluster.

.. code-block:: console

    $ sky check
    # Should show `Kubernetes: Enabled`

If you see an error, ensure that your kubeconfig file at :code:`~/.kube/config` is correctly set up.


Step A3 - Can you launch a SkyPilot task?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Next, try running a simple hello world task to verify that SkyPilot can launch tasks on your cluster.

.. code-block:: console

    $ sky launch -y -c mycluster --cloud kubernetes -- "echo hello world"
    # Task should run and print "hello world" to the console

    # Once you have verified that the task runs, you can delete it
    $ sky down -y mycluster

If your task does not run, check the terminal and provisioning logs for errors. Path to provisioning logs can be found at the start of the SkyPilot output,
starting with "To view detailed progress: ...".


Checking GPU support
--------------------

If you are trying to run a GPU task, make sure you have followed the instructions in :ref:`kubernetes-setup-gpusupport` to set up your cluster for GPU support.

In this section, we will verify that your cluster has GPU support and that SkyPilot can access it.

Step B0 - Is your cluster GPU-enabled?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Run :code:`kubectl describe nodes` to verify that your nodes have GPU support.

.. code-block:: console

    $ kubectl describe nodes
    # Look for the `nvidia.com/gpu` field under resources in the output. It should show the number of GPUs available for each node.

If you do not see the :code:`nvidia.com/gpu` field, your cluster likely does not have the Nvidia GPU operator installed. Please follow the instructions in :ref:`kubernetes-setup-gpusupport` to install the Nvidia GPU operator.

Step B1 - Can you run a GPU pod?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To verify can check if GPU operator is installed and the ``nvidia`` runtime is set as default by running:

.. code-block:: console

    $ kubectl apply -f https://raw.githubusercontent.com/skypilot-org/skypilot/master/tests/kubernetes/gpu_test_pod.yaml
    $ watch kubectl get pods
    # If the pod status changes to completed after a few minutes, your Kubernetes environment is set up correctly.

If the pod does not start, check the pod's logs for errors with :code:`kubectl describe gpu-test` and :code:`kubectl logs gpu-test`.

Step B2 - Are your nodes labeled correctly?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

SkyPilot requires nodes to be labeled with the correct GPU type to run GPU tasks. Run :code:`kubectl get nodes -o json` to verify that your nodes are labeled correctly.

.. tip::

    If you are using GKE, your nodes should be automatically labeled with :code:`cloud.google.com/gke-accelerator`. You can skip this step.

.. code-block:: console

    $ kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, labels: .metadata.labels}'
    # Look for the `skypilot.co/accelerator` label in the output. It should show the GPU type for each node.

If you do not see the `skypilot.co/accelerator` label, your nodes are not labeled correctly. Please follow the instructions in :ref:`kubernetes-setup-gpusupport` to label your nodes.

Step B3 - Can SkyPilot access your GPU?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Run :code:`sky check` to verify that SkyPilot can access your GPU.

.. code-block:: console

    $ sky check
    # Should show `Kubernetes: Enabled` and should not print any warnings about GPU support.

Step B4 - Try launching a dummy GPU task
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Next, try running a simple GPU task to verify that SkyPilot can launch GPU tasks on your cluster.

.. code-block:: console

    # List the available GPUs in your cluster
    $ sky show-gpus --cloud kubernetes

    # Use the GPU type from the output in your task launch command
    $ sky launch -y -c mycluster --cloud kubernetes --gpu <specify-a-gpu-type>:1 -- "nvidia-smi"

    # Task should run and print the nvidia-smi output to the console

    # Once you have verified that the task runs, you can delete it
    $ sky down -y mycluster

If your task does not run, check the terminal and provisioning logs for errors.
