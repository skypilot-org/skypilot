.. _kubernetes-docker-in-docker:

Container Runtimes in SkyPilot Pods (DinD / BuildKit)
======================================================

SkyPilot clusters running on Kubernetes are backed by one or more Pods.
Workflows that require container operations inside those Pods — such as
building and pushing images or launching nested containers — need an in-Pod
container runtime. SkyPilot exposes the full Kubernetes Pod spec via
:ref:`pod_config <config-yaml-kubernetes-pod-config>`, which makes it
straightforward to inject a sidecar-based container runtime without any
changes to SkyPilot itself.

This page describes two supported approaches and helps you choose the one
that fits your security posture and cluster capabilities.


Approaches
----------

.. list-table::
   :header-rows: 1
   :widths: 35 32 33

   * -
     - Docker-in-Docker Sidecar
     - Rootless BuildKit Sidecar
   * - Build & push images
     - ✅
     - ✅
   * - Run containers (``docker run``)
     - ✅
     - ❌
   * - Requires ``privileged: true``
     - ✅
     - ❌
   * - Requires Docker on K8s node
     - ❌ (sidecar brings its own ``dockerd``)
     - ❌
   * - Security risk
     - Higher (container escape surface)
     - Lower
   * - Tooling
     - Standard ``docker`` CLI
     - ``buildctl`` or ``docker buildx``

.. tip::

   Use BuildKit if you only need image build/push. Use DinD if you need full
   ``docker run`` capabilities.


.. _kubernetes-dind-sidecar:

Option 1: Docker-in-Docker (DinD) Sidecar
------------------------------------------

A ``docker:dind`` sidecar container starts its own isolated ``dockerd``
process. The main ``ray-node`` container communicates with it over a shared
Unix socket. The host node does **not** need Docker installed — the sidecar
is entirely self-contained.

**Cluster prerequisite:** The cluster must allow pods with ``privileged: true``.

Configuration
~~~~~~~~~~~~~

Add the following to SkyPilot config to apply it to all SkyPilot
clusters on Kubernetes:

.. code-block:: yaml

   kubernetes:
     pod_config:
       spec:
         containers:
           - name: ray-node
             env:
               - name: DOCKER_HOST
                 value: "unix:///var/run/dind/docker.sock"
             volumeMounts:
               - name: docker-sock-dir
                 mountPath: /var/run/dind
           - name: dind
             image: docker:29.3-dind
             securityContext:
               privileged: true
             env:
               - name: DOCKER_TLS_CERTDIR
                 value: ""
             args:
               - "--host=unix:///var/run/dind/docker.sock"
               - "--group=1000"
             volumeMounts:
               - name: docker-sock-dir
                 mountPath: /var/run/dind
               - name: dind-storage
                 mountPath: /var/lib/docker
         volumes:
           - name: docker-sock-dir
             emptyDir: {}
           - name: dind-storage
             emptyDir: {}

Alternatively, apply it only to a specific task by adding the same block
under the task YAML's ``config`` field, refer to `dind_cluster.yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/dind_cluster.yaml>`_ for more details.

Launch the cluster
~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   sky launch -c dev examples/dind_cluster.yaml

Then you can use ``docker`` command to build and push images inside the cluster.

.. _kubernetes-buildkit-sidecar:

Option 2: Rootless BuildKit sidecar
-------------------------------------

Running `BuildKit <https://github.com/moby/buildkit>`_ as a rootless sidecar provides
OCI-compliant image build and push without a privileged Docker daemon and
without requiring Docker on the host node.

**Limitation:** ``docker run`` / container execution is not supported. This
approach is suitable for build-only workflows (CI, image preparation).

Configuration
~~~~~~~~~~~~~

Add the following to SkyPilot config to apply it to all SkyPilot
clusters on Kubernetes:

.. code-block:: yaml

   kubernetes:
     pod_config:
       spec:
         containers:
           - name: ray-node
             env:
               - name: BUILDKIT_HOST
                 value: "unix:///run/buildkit/buildkitd.sock"
             volumeMounts:
               - name: buildkit-sock
                 mountPath: /run/buildkit
           - name: buildkitd
             image: moby/buildkit:v0.28.0-rootless
             args:
               - --addr=unix:///run/buildkit/buildkitd.sock
               - --oci-worker-no-process-sandbox
             securityContext:
               seccompProfile:
                 type: Unconfined
               appArmorProfile:
                 type: Unconfined
               runAsUser: 1000
               runAsGroup: 1000
             volumeMounts:
               - name: buildkit-sock
                 mountPath: /run/buildkit
         volumes:
           - name: buildkit-sock
             emptyDir: {}

Alternatively, apply it only to a specific task by adding the same block
under the task YAML's ``config`` field, refer to `buildkit_cluster.yaml <https://github.com/skypilot-org/skypilot/blob/master/examples/buildkit_cluster.yaml>`_ for more details.

Launch the cluster
~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   sky launch -c dev examples/buildkit_cluster.yaml

Then you can use ``buildctl`` or ``docker buildx`` command to build and push images inside the cluster.

.. code-block:: bash

  buildctl build \
  --frontend dockerfile.v0 \
  --local context=. \
  --local dockerfile=. \
  --output type=image,name=myregistry/myimage:latest,push=true

.. code-block:: bash

   # One-time setup
   docker buildx create \
     --name skypilot-builder \
     --driver remote \
     unix:///run/buildkit/buildkitd.sock
   docker buildx use skypilot-builder

   # Build and push
   docker buildx build -t myregistry/myimage:latest --push .
