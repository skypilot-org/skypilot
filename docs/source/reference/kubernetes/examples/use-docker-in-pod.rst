.. _use-docker-in-pod:

Use Docker in Pods
==================

SkyPilot clusters running on Kubernetes are backed by one or more Pods.
Workflows that require container operations inside those Pods — such as
building and pushing images or launching nested containers — need an in-Pod
container runtime. SkyPilot provides a built-in ``enable_docker`` config that
automatically injects a sidecar container with the appropriate runtime.

This page describes two supported approaches and helps you choose the one
that fits your security posture and cluster capabilities.

Approaches
----------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * -
     - Docker-in-Docker (DinD)
     - Rootless BuildKit
   * - Build & push images
     - Yes
     - Yes
   * - Run containers (``docker run``)
     - Yes
     - No
   * - Requires ``privileged: true``
     - Yes
     - No
   * - Requires Docker on K8s node
     - No (sidecar brings its own ``dockerd``)
     - No
   * - Security risk
     - Higher (container escape surface)
     - Lower

.. tip::

   Use BuildKit (``enable_docker: build``) if you only need image build/push.
   Use DinD (``enable_docker: true``) if you need full ``docker run`` capabilities.

Option 1: Full Docker (``enable_docker: true``)
------------------------------------------------

Set ``enable_docker: true`` to make the full ``docker`` CLI available inside
the pod — you can build images, push them, and run containers
(``docker run``).

**Cluster prerequisite:** The cluster must allow pods with ``privileged: true``.

Configuration
^^^^^^^^^^^^^

Add the following to the task YAML's ``config`` field:

.. code-block:: yaml

   config:
     kubernetes:
       enable_docker: true

Or apply it globally to all SkyPilot clusters in SkyPilot config:

.. code-block:: yaml

   kubernetes:
     enable_docker: true

To persist the Docker cache across cluster restarts, specify a SkyPilot volume:

.. code-block:: yaml

   config:
     kubernetes:
       enable_docker:
         enabled: true
         cache_volume: my-builder-cache  # SkyPilot volume name

.. note::

   For multi-node clusters, use a ``ReadWriteMany`` volume so all
   nodes can mount it simultaneously. Each pod gets its own ``subPath`` within
   the PVC, so a single volume can be safely shared across clusters.

See `dind_cluster.yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/enable_docker/dind_cluster.yaml>`_ for a complete example.

Launch and verify
^^^^^^^^^^^^^^^^^

.. code-block:: bash

   sky launch -c dev examples/enable_docker/dind_cluster.yaml

   # SSH into the cluster and confirm Docker is available
   ssh dev
   docker info
   # Build and push an image using the docker CLI
   docker build -t myregistry/myimage:latest .
   docker push myregistry/myimage:latest

Option 2: Build-only (``enable_docker: build``)
------------------------------------------------

If your cluster does not allow ``privileged: true`` pods, or you only need
to build and push images, set ``enable_docker: build``. This makes
``docker buildx build`` available inside the pod without
requiring privileged permissions.

**Limitation:** ``docker run`` / container execution is not supported.

Configuration
^^^^^^^^^^^^^

Add the following to the task YAML's ``config`` field:

.. code-block:: yaml

   config:
     kubernetes:
       enable_docker: build

Or apply it globally in SkyPilot config:

.. code-block:: yaml

   kubernetes:
     enable_docker: build

To persist the BuildKit cache across cluster restarts:

.. code-block:: yaml

   config:
     kubernetes:
       enable_docker:
         enabled: build
         cache_volume: my-builder-cache  # SkyPilot volume name

.. note::

   For multi-node clusters, use a ``ReadWriteMany`` volume so all
   nodes can mount it simultaneously. Each pod gets its own ``subPath`` within
   the PVC, so a single volume can be safely shared across clusters.

See `buildkit_cluster.yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/enable_docker/buildkit_cluster.yaml>`_ for a complete example.

Launch and verify
^^^^^^^^^^^^^^^^^

.. code-block:: bash

   sky launch -c dev examples/enable_docker/buildkit_cluster.yaml

   # SSH into the cluster and confirm buildx is configured
   ssh dev
   docker buildx ls
   # Build and push an image using buildx
   docker buildx build -t myregistry/myimage:latest --push .
