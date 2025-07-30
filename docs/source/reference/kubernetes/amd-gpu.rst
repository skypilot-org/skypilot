.. _kubernetes-amd-gpu:

Using AMD GPUs on Kubernetes
============================

SkyPilot supports AMD GPUs on Kubernetes. This guide walks through the steps to enable AMD GPU detection and scheduling in SkyPilot on Kubernetes.

Dependency installation
-----------------------

Install cert-manager
~~~~~~~~~~~~~~~~~~~~

cert-manager is a `dependency <https://github.com/ROCm/gpu-operator?tab=readme-ov-file#prerequisites>`_ for the AMD GPU operator.

.. code-block:: bash

    helm repo add jetstack https://charts.jetstack.io --force-update
    helm install cert-manager jetstack/cert-manager \
          		--namespace cert-manager \
          		--create-namespace \
          		--version v1.15.1 \
          		--set crds.enabled=true

Add AMD GPU operator Helm repo
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    helm repo add rocm https://rocm.github.io/gpu-operator
    helm repo update


Install AMD GPU operator
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    helm install amd-gpu-operator rocm/gpu-operator-charts \
            --namespace kube-amd-gpu --create-namespace \
            --version=v1.3.0


GPU detection and labeling
--------------------------

Check for AMD GPU labels
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, labels: .metadata.labels}' | grep -e "amd.com/gpu" 

Check node capacity
~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, capacity: .status.capacity}'

Sample output:

.. code-block:: json

    {
        "name": "<node-name>",
        "capacity": {
            "amd.com/gpu": "8",
            "cpu": "160",
            "ephemeral-storage": "2077109340Ki",
            "hugepages-1Gi": "0",
            "hugepages-2Mi": "0",
            "memory": "1981633424Ki",
            "pods": "110"
        }
    }


Label nodes for SkyPilot
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    kubectl label node <node-name> skypilot.co/accelerator=mi300


Verify labels
~~~~~~~~~~~~~

.. code-block:: bash

    kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, labels: .metadata.labels}' | grep -e "skypilot.co/accelerator"


Launch a cluster with SkyPilot
------------------------------

Check Kubernetes cluster is enabled for SkyPilot
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    sky check kubernetes

Sample output:

.. code-block:: text

    🎉 Enabled infra 🎉
    Kubernetes [compute]
        Allowed contexts:
        └── <your context name>

List available accelerators (AMD GPUs)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    sky show-gpus --infra kubernetes

Sample output:

.. code-block:: text

    Kubernetes GPUs
    Context: <your context name>
    GPU    REQUESTABLE_QTY_PER_NODE  UTILIZATION  
    MI300  1, 2, 4, 8                6 of 8 free  
    Kubernetes per-node GPU availability
    CONTEXT              NODE         GPU    UTILIZATION  
    <your context name>  mi300-8gpus  MI300  6 of 8 free  

Run a sample example with AMD Docker images
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Smoke test: 

.. code-block:: bash

    sky launch -c amd-cluster examples/amd/amd_smoke_test.yaml

2. Pytorch example Reinforcement learning:

.. code-block:: bash

    sky launch -c amd-cluster examples/amd/amd_pytorch_RL.yaml

More example task YAMLs are available under `examples/amd <https://github.com/skypilot-org/skypilot/tree/master/examples/amd>`_ directory.
