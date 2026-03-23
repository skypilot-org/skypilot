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

   - Use ``enable_docker: BUILD`` if you only need image build/push.
   - Use ``enable_docker: true`` if you need full ``docker run`` capabilities.

Option 1: Full Docker access (privileged permission required)
-------------------------------------------------------------

Set ``enable_docker: true`` to make the full ``docker`` CLI available inside
the pod — you can build images, push them, and run containers
(``docker run``).

**Cluster prerequisite:** The cluster must allow pods with ``privileged: true``.

.. note::

   GPU passthrough to nested containers (``docker run --gpus``) is not
   currently supported. To test a GPU image, build and push it first,
   then launch it directly with ``sky launch``.

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

To persist the Docker cache across cluster restarts, see :ref:`persist-docker-cache`.

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

See `dind_cluster.yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/enable_docker/dind_cluster.yaml>`_ for a complete example.

Option 2: Build-only
--------------------

If your cluster does not allow ``privileged: true`` pods, or you only need
to build and push images, set ``enable_docker: BUILD``. This makes
``docker buildx build`` available inside the pod without
requiring privileged permissions.

**Limitation:** ``docker run`` / container execution is not supported.

Configuration
^^^^^^^^^^^^^

Add the following to the task YAML's ``config`` field:

.. code-block:: yaml

   config:
     kubernetes:
       enable_docker: BUILD

Or apply it globally in SkyPilot config:

.. code-block:: yaml

   kubernetes:
     enable_docker: BUILD

To persist the BuildKit cache across cluster restarts, see :ref:`persist-docker-cache`.

Launch and verify
^^^^^^^^^^^^^^^^^

.. code-block:: bash

   sky launch -c dev examples/enable_docker/buildkit_cluster.yaml

   # SSH into the cluster and confirm buildx is configured
   ssh dev
   docker buildx ls
   # Build and push an image using buildx
   docker buildx build -t myregistry/myimage:latest --push .

See `buildkit_cluster.yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/enable_docker/buildkit_cluster.yaml>`_ for a complete example.

.. _persist-docker-cache:

Persist the cache
-----------------

By default the Docker / BuildKit cache is lost when the cluster is stopped or
restarted. To persist it, create a SkyPilot volume and reference it in the
``enable_docker`` config.

1. Define a volume YAML:

   .. code-block:: yaml

      # docker-cache-vol.yaml
      name: my-builder-cache
      type: k8s-pvc
      infra: k8s
      size: 50Gi
      config:
        storage_class_name: standard-rwo
        access_mode: ReadWriteOnce

   .. note::

      The cache volume **must** be backed by a block-storage filesystem (e.g.,
      ext4/xfs on EBS, Persistent Disk, etc.).  NFS-based storage such as AWS
      EFS, Google Cloud Filestore, or CephFS **cannot** be used because:

      - **ALL mode (DinD):** The overlay storage driver is not supported on NFS.
      - **BUILD mode (rootless BuildKit):** NFS prevents unpacking image layers
        with correct file ownership.

      See the `Docker known limitations
      <https://docs.docker.com/engine/security/rootless/troubleshoot/#known-limitations>`_
      for details.

2. Create the volume:

   .. code-block:: console

      $ sky volumes apply docker-cache-vol.yaml

3. Reference the volume in ``enable_docker``:

   .. code-block:: yaml

      config:
        kubernetes:
          enable_docker:
            mode: ALL   # or BUILD
            cache_volume: my-builder-cache
