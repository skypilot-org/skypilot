.. _volumes:

Volumes
=======

Volumes offer a high-performance alternative to :ref:`cloud buckets <sky-storage>` for storing data. Unlike buckets, volumes are limited to a single region and cannot be accessed across regions and clouds.

Benefits of using volumes:

* **Performance**: Volumes are generally faster than object stores, and SkyPilot lets you choose from different storage classes based on your performance requirements.
* **Data persistence**: Volumes can persist data independently of task life cycles, making it easy to share data between different tasks (e.g., datasets, caches) or preserve results.
* **Size control**: You can set volume size limits to manage costs and limit storage usage.

SkyPilot supports creating and managing volumes directly through the ``sky`` CLI and the web dashboard.

Supported volume types:

- Kubernetes: `Persistent Volume Claims (PVCs) <https://kubernetes.io/docs/concepts/storage/persistent-volumes/#persistentvolumeclaims/>`_

  - Tested storage backends: AWS EBS, GCP Persistent Disk, Nebius network SSD, JuiceFS, Nebius shared file system, GCP Filestore

- RunPod: `Network Volumes <https://docs.runpod.io/pods/storage/types#network-volume>`_

.. _volumes-on-kubernetes:

Volumes on Kubernetes
---------------------

In Kubernetes clusters, PVCs (Persistent Volume Claims) request and bind to PV (Persistent Volume) resources. These persistent volumes can be backed by various storage backends, including **block storage** solutions (AWS EBS, GCP Persistent Disk) and **distributed file systems** (JuiceFS, Nebius shared file system, AWS EFS, GCP Filestore), etc.

SkyPilot supports two types of volumes on Kubernetes:

1. **Persistent volumes**: Managed independently through CLI commands with lifecycle separate from clusters
2. **Ephemeral volumes**: Bound to cluster lifecycle, automatically created and deleted with the cluster

.. list-table::
   :widths: 30 35 35
   :header-rows: 1

   * - Feature
     - Persistent Volumes
     - Ephemeral Volumes
   * - Lifecycle
     - Independent (manually managed)
     - Bound to cluster
   * - Creation
     - ``sky volumes apply``
     - Automatic (in task YAML)
   * - Deletion
     - ``sky volumes delete``
     - Automatic (with cluster)
   * - Sharing across clusters
     - Yes
     - No (cluster-specific)
   * - Use case
     - Long-term data, shared datasets
     - Temporary storage, caches

Persistent volumes
~~~~~~~~~~~~~~~~~~

Persistent volumes are created and managed independently using the following commands:

- ``sky volumes apply``: Create a new volume
- ``sky volumes ls``: List all volumes
- ``sky volumes delete``: Delete a volume

.. note::

  Volumes are shared across users on a SkyPilot API server. A user can mount volumes created by other users. This is useful for sharing caches and data across users.

Quickstart
^^^^^^^^^^

1. Prepare a volume YAML file:

   .. code-block:: yaml

     # volume.yaml
     name: new-pvc
     type: k8s-pvc
     infra: kubernetes  # or k8s or k8s/context
     size: 10Gi
     # If the PVC already exists, set `use_existing` to true and
     # set the `name` to the existing PVC name
     # use_existing: true
     labels:
       key: value
     config:
       namespace: default  # optional
       storage_class_name: csi-mounted-fs-path-sc  # optional
       access_mode: ReadWriteMany  # optional

2. Create the volume with ``sky volumes apply volume.yaml``:

   .. code-block:: console

     $ sky volumes apply volume.yaml
     Proceed to create volume 'new-pvc'? [Y/n]: Y
     Creating PVC: new-pvc-73ec42f2-5c6c4e

3. Mount the volume in your task YAML:

   .. code-block:: yaml

     # task.yaml
     volumes:
       /mnt/data: new-pvc  # The volume new-pvc will be mounted to /mnt/data

     run: |
       echo "Hello, World!" > /mnt/data/hello.txt

.. note::

  For multi-node clusters, volumes are mounted to all nodes. You must configure ``config.access_mode`` to ``ReadWriteMany`` and use a ``storage_class_name`` that supports the ``ReadWriteMany`` access mode. Otherwise, SkyPilot will fail to launch the cluster.

.. _volumes-on-kubernetes-manage:

Managing volumes
^^^^^^^^^^^^^^^^

List all volumes with ``sky volumes ls``:

.. code-block:: console

  $ sky volumes ls
  NAME     TYPE     INFRA                         SIZE  USER   WORKSPACE  AGE   STATUS  LAST_USE     USED_BY
  new-pvc  k8s-pvc  Kubernetes/nebius-mk8s-vol    1Gi   alice  default    8m    IN_USE  <timestamp>  <cluster_name>


.. tip::

  Use ``-v`` to view detailed information about a volume.

  .. code-block:: console

    $ sky volumes ls -v
    NAME     TYPE     INFRA                         SIZE  USER   WORKSPACE  AGE   STATUS  LAST_USE             USED_BY   NAME_ON_CLOUD              STORAGE_CLASS           ACCESS_MODE
    new-pvc  k8s-pvc  Kubernetes/nebius-mk8s-vol    1Gi   alice  default    8m    IN_USE  2025-06-24 10:18:32  training  new-pvc-73ec42f2-5c6c4e    csi-mounted-fs-path-sc  ReadWriteMany

Delete a volume with ``sky volumes delete``:

.. code-block:: console

  $ sky volumes delete new-pvc
  Proceed to delete volume 'new-pvc'? [Y/n]: Y
  Deleting PVC: new-pvc-73ec42f2-5c6c4e


If the volume is in use, it will be marked as ``IN_USE`` and cannot be deleted.

You can also check the volumes in the SkyPilot dashboard.

.. figure:: ../images/volumes.png
    :alt: SkyPilot volumes
    :align: center
    :width: 80%

Filesystem volume examples
^^^^^^^^^^^^^^^^^^^^^^^^^^

This section demonstrates how to configure and use distributed filesystems as SkyPilot volumes. We'll cover options like `JuiceFS <https://juicefs.com/docs/community/introduction/>`_ (a cloud-native distributed filesystem) and `Nebius shared file system <https://docs.nebius.com/compute/storage/types#filesystems>`_ (a high-performance shared storage solution).


.. tab-set::

    .. tab-item:: JuiceFS
        :sync: juicefs-tab

        To use JuiceFS as a SkyPilot volume:

        1. **Install the JuiceFS CSI driver** on your Kubernetes cluster. Follow the official `installation guide <https://juicefs.com/docs/csi/getting_started>`_ for detailed instructions.

        2. **Verify the driver installation** - Confirm that the ``juicefs-sc`` storage class has been created successfully:

        .. code-block:: console

          $ kubectl get storageclass
          NAME           PROVISIONER         RECLAIMPOLICY   VOLUMEBINDINGMODE   ALLOWVOLUMEEXPANSION   AGE
          juicefs-sc     csi.juicefs.com     Retain          Immediate           false                  10m

        .. note::
           If the ``juicefs-sc`` storage class is not available, refer to the `JuiceFS storage class creation guide <https://juicefs.com/docs/csi/guide/pv/#create-storage-class>`_ to set it up.

        3. **Create a SkyPilot volume for JuiceFS** with a volume YAML:

        .. code-block:: yaml

          # juicefs-volume.yaml
          name: juicefs-pvc
          type: k8s-pvc
          infra: k8s
          size: 100Gi
          config:
            storage_class_name: juicefs-sc
            access_mode: ReadWriteMany

        .. code-block:: console

          $ sky volumes apply juicefs-volume.yaml

        4. **Mount the volume to SkyPilot task** in your SkyPilot YAML:

        .. code-block:: yaml

          # task.yaml
          num_nodes: 2

          volumes:
            # Mount the JuiceFS volume to /mnt/data across all nodes
            /mnt/data: juicefs-pvc

          run: |
            # Verify the volume is mounted and accessible
            df -h /mnt/data
            ls -la /mnt/data

        .. code-block:: console

          # Launch the cluster with the JuiceFS volume
          $ sky launch -c juicefs-cluster task.yaml

    .. tab-item:: Nebius shared file system
        :sync: nebius-tab

        To use Nebius shared file system as a SkyPilot volume:

        1. **Set up the Nebius filesystem infrastructure** by following the official documentation:

           - `Create a shared filesystem <https://docs.nebius.com/kubernetes/storage/filesystem-over-csi#create-filesystem>`_
           - `Create a node group and mount the filesystem <https://docs.nebius.com/kubernetes/storage/filesystem-over-csi#create-node-group>`_
           - `Install the CSI driver <https://docs.nebius.com/kubernetes/storage/filesystem-over-csi#install-csi>`_

        2. **Verify the storage class** - Confirm that the ``csi-mounted-fs-path-sc`` storage class has been created:

        .. code-block:: console

          $ kubectl get storageclass
          NAME                     PROVISIONER                    RECLAIMPOLICY   VOLUMEBINDINGMODE      ALLOWVOLUMEEXPANSION   AGE
          csi-mounted-fs-path-sc   mounted-fs-path.csi.nebius.ai  Delete          WaitForFirstConsumer   false                  10m

        3. **Create a SkyPilot volume for Nebius file system** with a volume YAML:

        .. code-block:: yaml

          # nebius-volume.yaml
          name: nebius-pvc
          type: k8s-pvc
          infra: k8s
          size: 100Gi
          config:
            storage_class_name: csi-mounted-fs-path-sc
            access_mode: ReadWriteMany

        .. code-block:: console

          $ sky volumes apply nebius-volume.yaml

        4. **Mount the volume to SkyPilot task** in your SkyPilot YAML:

        .. code-block:: yaml

          # task.yaml
          num_nodes: 2

          volumes:
            # Mount the Nebius shared filesystem to /mnt/data across all nodes
            /mnt/data: nebius-pvc

          run: |
            # Verify the volume is mounted and accessible
            df -h /mnt/data
            ls -la /mnt/data

        .. code-block:: console

          # Launch the cluster with the Nebius volume
          $ sky launch -c nebius-cluster task.yaml


Ephemeral volumes
~~~~~~~~~~~~~~~~~

Unlike persistent volumes that are managed independently, ephemeral volumes are automatically created when a cluster is launched and deleted when the cluster is terminated. This makes them ideal for temporary storage needs such as caches, intermediate results, or any data that should only exist for the duration of a cluster's lifetime.

**Key characteristics:**

- **Automatic lifecycle management**: No need to manually create or delete volumes
- **Cluster-bound**: Created with the cluster and deleted when the cluster is terminated
- **Simplified usage**: Defined directly in the task YAML with the cluster configuration
- **Currently Kubernetes-only**: Only supported on Kubernetes clusters

To use an ephemeral volume, simply specify the ``size`` field in the volumes section of your task YAML:

.. code-block:: yaml

  # task.yaml
  resources:
    infra: kubernetes
    ...

  volumes:
    /mnt/cache:
      size: 100Gi
      #labels:        # optional
      #  env: production
      #config:        # optional
      #  storage_class_name: csi-mounted-fs-path-sc  # optional
      #  access_mode: ReadWriteMany                  # optional

  run: |
    echo "Using ephemeral volumes"
    df -h /mnt/cache
    echo "data" > /mnt/cache/temp.txt

When you launch the cluster with ``sky launch``, the ephemeral volumes will be automatically created:

.. code-block:: console

  $ sky launch -c my-cluster task.yaml
  # Ephemeral volumes are created automatically
  $ sky volumes ls
  Kubernetes PVCs:
  NAME                       TYPE     INFRA                      SIZE  USER  WORKSPACE  AGE   STATUS  LAST_USE             USED_BY                  IS_EPHEMERAL
  my-cluster-43dbb4ab-2f74bf k8s-pvc  Kubernetes/nebius-mk8s-vol 100Gi alice default    58m   IN_USE  2025-11-17 14:30:18  my-cluster-43dbb4ab-head True

.. note::

  For multi-node clusters, ephemeral volumes are mounted to all nodes. You must configure ``config.access_mode`` to ``ReadWriteMany`` and use a ``storage_class_name`` that supports the ``ReadWriteMany`` access mode. Otherwise, SkyPilot will fail to launch the cluster.

When you terminate the cluster, the ephemeral volumes are automatically deleted:

.. code-block:: console

  $ sky down my-cluster
  # Cluster and its ephemeral volumes are deleted


.. _volumes-on-runpod:

Volumes on RunPod
------------------

RunPod Network Volumes provide persistent storage that can be mounted into pods on RunPod. SkyPilot supports creating and managing RunPod network volumes via the same three commands:

- ``sky volumes apply``: Create a new network volume
- ``sky volumes ls``: List all volumes
- ``sky volumes delete``: Delete a volume

Notes specific to RunPod:

- ``infra`` must specify the RunPod data center (zone), e.g. ``runpod/CA/CA-MTL-1``.
- Volume name length is limited (max 30 characters).
- Labels are not currently supported for RunPod volumes.

Quickstart
~~~~~~~~~~

1. Prepare a volume YAML file:

   .. code-block:: yaml

     # runpod-volume.yaml
     name: rpvol
     type: runpod-network-volume
     infra: runpod/CA/CA-MTL-1  # DataCenterId (zone)
     size: 100Gi                # GiB
     # If the RunPod network volume already exists, set `use_existing` to true and
     # set the `name` to the existing RunPod network volume name
     # use_existing: true

2. Create the volume with ``sky volumes apply runpod-volume.yaml``:

   .. code-block:: console

     $ sky volumes apply runpod-volume.yaml
     Proceed to create volume 'rpvol'? [Y/n]: Y
     Created RunPod network volume rpvol-43dbb4ab-15e906 (id=5w6ecp2w9n)

3. Mount the volume in your task YAML:

   .. code-block:: yaml

     # task.yaml
     volumes:
       /workspace: rpvol

     run: |
       echo "Hello, RunPod!" > /workspace/hello.txt

Managing volumes
~~~~~~~~~~~~~~~~

Same as Kubernetes volumes, refer to :ref:`volumes-on-kubernetes-manage` for more details.
