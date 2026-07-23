.. _kubernetes-image-prepull:

Speeding Up Image Pulls
=======================

Heavy container images -- CUDA / NVIDIA base images, large model runtimes -- can
take **several minutes to tens of minutes** to pull the first time they land on a
node. Until the pull finishes, the pod sits in ``ContainerCreating``, so
``sky launch`` or ``sky jobs launch`` can take a long time to finish.

The most portable fix is to **pre-pull** the images onto every node *ahead of
time* with a `DaemonSet <https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/>`_.
When a task later schedules onto that node, the image is already in the node's
local cache and the pod starts almost immediately.

How it works
------------

The DaemonSet runs one pod per node. Each image you want to warm is listed as an
**init container**: Kubernetes must pull an init container's image before it can
run, so the pull is the whole point -- the container itself does nothing and
exits immediately. A tiny ``pause`` container keeps the pod scheduled on the node.

.. code-block:: text

    ┌── every node ─────────────────────────────┐
    │  DaemonSet pod                             │
    │   initContainer prepull-0  → pulls image A │  ← kubelet pulls A, container exits
    │   initContainer prepull-1  → pulls image B │  ← kubelet pulls B, container exits
    │   container    pause       → stays running │
    └────────────────────────────────────────────┘
          images A, B now warm in the node's containerd cache

Generate and apply
------------------

SkyPilot provides a helper script that expands a list of images into a ready-to-apply
DaemonSet. Download it, pass the images as arguments, and pipe the output to ``kubectl``:

.. code-block:: bash

    # Download the generator script
    curl -sSLO https://raw.githubusercontent.com/skypilot-org/skypilot/master/sky/utils/kubernetes/generate_prepull_ds.sh

    # Review the generated manifest
    bash generate_prepull_ds.sh nvcr.io/nvidia/pytorch:24.05-py3 my-registry/model:v1

    # Apply it directly
    bash generate_prepull_ds.sh nvcr.io/nvidia/pytorch:24.05-py3 my-registry/model:v1 | kubectl apply -f -

Options are passed as environment variables:

.. list-table::
    :header-rows: 1
    :widths: 30 70

    * - Variable
      - Description
    * - ``NAMESPACE``
      - Namespace to deploy into (default: ``default``).
    * - ``NODE_SELECTOR_KEY`` / ``NODE_SELECTOR_VALUE``
      - Restrict pre-pulling to nodes carrying this label. Leave unset for all nodes.
    * - ``PULL_SECRET``
      - Name of an image pull secret (a ``kubernetes.io/dockerconfigjson`` secret in ``NAMESPACE``) for private registries.

For example, to warm an image on GPU nodes only, pulling from a private registry:

.. code-block:: bash

    NODE_SELECTOR_KEY=nvidia.com/gpu.present NODE_SELECTOR_VALUE=true PULL_SECRET=regcred \
      bash generate_prepull_ds.sh my-registry/model:v1 | kubectl apply -f -

.. tip::

    Find your GPU node labels with ``kubectl get nodes --show-labels``. Common keys
    are ``nvidia.com/gpu.present`` or cloud-specific labels such as
    ``cloud.google.com/gke-accelerator``.

Apply a manifest by hand
------------------------

If you prefer a static manifest, this is exactly what the script produces.
Duplicate the ``prepull-*`` init container block once per image.

.. code-block:: yaml

    apiVersion: apps/v1
    kind: DaemonSet
    metadata:
      name: skypilot-image-prepuller
      namespace: default
      labels:
        app.kubernetes.io/name: skypilot-image-prepuller
    spec:
      selector:
        matchLabels:
          app.kubernetes.io/name: skypilot-image-prepuller
      template:
        metadata:
          labels:
            app.kubernetes.io/name: skypilot-image-prepuller
        spec:
          # nodeSelector:                     # optional: GPU nodes only
          #   nvidia.com/gpu.present: "true"
          # imagePullSecrets:                 # optional: private registries
          #   - name: regcred
          tolerations:
            - operator: "Exists"        # tolerate all taints (e.g. GPU nodes)...
          affinity:
            nodeAffinity:               # ...but keep off control-plane / master nodes
              requiredDuringSchedulingIgnoredDuringExecution:
                nodeSelectorTerms:
                  - matchExpressions:
                      - key: node-role.kubernetes.io/control-plane
                        operator: DoesNotExist
                      - key: node-role.kubernetes.io/master
                        operator: DoesNotExist
          initContainers:
            - name: prepull-0
              image: "nvcr.io/nvidia/pytorch:24.05-py3"
              imagePullPolicy: IfNotPresent
              command: ["sh", "-c", "exit 0"]
              resources:
                requests: { cpu: "10m", memory: "16Mi" }
                limits:   { cpu: "50m", memory: "64Mi" }
            # - name: prepull-1               # duplicate per image
            #   image: "my-registry/model:v1"
            #   imagePullPolicy: IfNotPresent
            #   command: ["sh", "-c", "exit 0"]
            #   resources:
            #     requests: { cpu: "10m", memory: "16Mi" }
            #     limits:   { cpu: "50m", memory: "64Mi" }
          containers:
            - name: pause
              image: registry.k8s.io/pause:3.9
              resources:
                requests: { cpu: "10m", memory: "16Mi" }
                limits:   { cpu: "50m", memory: "64Mi" }

Verify
------

.. code-block:: bash

    # One pod per (matching) node; Running once all images are pulled
    kubectl get pods -l app.kubernetes.io/name=skypilot-image-prepuller -o wide

    # Watch pull progress on a node
    kubectl describe pod <prepuller-pod> | grep -A2 -iE "pulling|pulled"

Once the pods are ``Running`` (init containers ``Completed``), the listed images
are warm on every matching node and new tasks that use them skip the pull.

To change the image set, re-run the script (or edit the manifest) and re-apply.

To remove the pre-puller:

.. code-block:: bash

    kubectl delete daemonset skypilot-image-prepuller -n default

Deleting the DaemonSet removes the pre-puller pods but does not evict the images
already cached on the nodes -- those stay until the kubelet's image garbage
collection reclaims them.

Things to know
--------------

- **Image garbage collection can evict warmed images.** The kubelet reclaims
  images once node disk usage crosses ``imageGCHighThresholdPercent`` (default
  **85%**), least-recently-used first -- so on a busy node a pre-pulled image can
  be evicted before it is used. If that happens, either raise the threshold via
  your kubelet configuration, or change the ``prepull-*`` blocks from
  ``initContainers`` to long-running ``containers`` with
  ``command: ["sh", "-c", "sleep infinity"]`` so the images stay referenced by a
  running container (at the cost of a few idle containers per node).
- **Pin by digest for mutable tags.** With ``imagePullPolicy: IfNotPresent``, a
  node that already has ``myimage:latest`` will *not* re-pull even if the tag was
  re-pushed. If you rely on mutable tags, reference images by digest
  (``myimage@sha256:...``) so "pre-pulled" always means the exact bytes your task
  will run.
- **Disk and bandwidth.** Warming multi-GB images on every node consumes node disk
  and, on first apply, registry bandwidth. Target only the nodes that need each
  image with ``NODE_SELECTOR_KEY``.
